"""Unified capability-based permission management for nanobot agents.

PermissionManager is the single runtime authority for agent capabilities.
Resource ownership, workspace sandboxing and OS/container isolation remain
separate hard boundaries.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable, Iterator

from nanobot.permission_types import (
    AUTOMATION_MANAGE,
    BROWSER_CONTROL,
    CONTEXT_MANAGE,
    DREAM_SUBJECT,
    EXEC,
    EXTERNAL_CALL,
    IMAGE_GENERATE,
    KNOWN_CAPABILITIES,
    MAIN_SUBJECT,
    MODEL_MANAGE,
    SPECIALIST_PREFIX,
    SUBAGENT_MANAGE,
    TOOL_USE,
    VISION_DELEGATE,
    WEB,
    WORK_SUBJECT,
    WORKSPACE_READ,
    WORKSPACE_WRITE,
)

if TYPE_CHECKING:
    from nanobot.config.schema import Config
    from nanobot.permission_config import PermissionPolicyConfig


@dataclass(frozen=True, slots=True)
class _PermissionScope:
    manager: "PermissionManager"
    subject: str
    grants: frozenset[str]


_CURRENT_PERMISSION_SCOPE: ContextVar[_PermissionScope | None] = ContextVar(
    "nanobot_permission_scope",
    default=None,
)

_TOOL_CAPABILITIES = {
    "read_file": WORKSPACE_READ,
    "list_dir": WORKSPACE_READ,
    "find_files": WORKSPACE_READ,
    "grep": WORKSPACE_READ,
    "write_file": WORKSPACE_WRITE,
    "edit_file": WORKSPACE_WRITE,
    "apply_patch": WORKSPACE_WRITE,
    "exec": EXEC,
    "exec_session": EXEC,
    "list_exec_sessions": EXEC,
    "run_cli_app": EXEC,
    "web_search": WEB,
    "web_fetch": WEB,
    "image_analyze": VISION_DELEGATE,
    "generate_image": IMAGE_GENERATE,
    "subagent": SUBAGENT_MANAGE,
    "context": CONTEXT_MANAGE,
    "model_config": MODEL_MANAGE,
    "cron": AUTOMATION_MANAGE,
}

@dataclass(frozen=True, slots=True)
class PermissionDecision:
    subject: str
    capability: str
    allowed: bool
    within_ceiling: bool
    reason: str


class PermissionDeniedError(PermissionError):
    """Raised when a subject attempts to use a capability it does not own."""


class PermissionManager:
    """Resolve and enforce capability policy from current validated Config."""

    def __init__(self, config: "Config | Callable[[], Config]") -> None:
        self._config_source = config

    @property
    def config(self) -> "Config":
        source = self._config_source
        return source() if callable(source) else source

    @staticmethod
    def specialist_subject(role: str) -> str:
        return f"{SPECIALIST_PREFIX}{role.strip().lower()}"

    @staticmethod
    def tool_capability(name: str) -> str:
        normalized = str(name or "").strip()
        if normalized.startswith("browser_"):
            return BROWSER_CONTROL
        if normalized.startswith("mcp_"):
            return EXTERNAL_CALL
        return _TOOL_CAPABILITIES.get(normalized, TOOL_USE)

    def _policy(self, subject: str) -> "PermissionPolicyConfig":
        permissions = self.config.permissions
        if subject == MAIN_SUBJECT:
            return permissions.main
        if subject == DREAM_SUBJECT:
            return permissions.dream
        if subject == WORK_SUBJECT:
            return permissions.work
        if subject.startswith(SPECIALIST_PREFIX):
            role = subject[len(SPECIALIST_PREFIX):]
            return permissions.specialists.get(role, permissions.specialist_default)
        raise KeyError(f"Unknown permission subject {subject!r}")

    def _scoped_grants(self, subject: str) -> frozenset[str]:
        scope = _CURRENT_PERMISSION_SCOPE.get()
        if scope is None or scope.manager is not self or scope.subject != subject:
            return frozenset()
        return scope.grants

    def decision(self, subject: str, capability: str) -> PermissionDecision:
        normalized = str(capability or "").strip().lower()
        if normalized not in KNOWN_CAPABILITIES:
            return PermissionDecision(
                subject, normalized, False, False, "unknown_capability"
            )
        policy = self._policy(subject)
        granted = set(policy.capabilities) | set(self._scoped_grants(subject))
        ceiling = set(policy.ceiling)
        within_ceiling = normalized in ceiling
        allowed = normalized in granted and within_ceiling
        reason = "allowed" if allowed else ("ceiling" if not within_ceiling else "not_granted")
        return PermissionDecision(
            subject=subject,
            capability=normalized,
            allowed=allowed,
            within_ceiling=within_ceiling,
            reason=reason,
        )

    def allowed(self, subject: str, capability: str) -> bool:
        return self.decision(subject, capability).allowed

    def require(self, subject: str, capability: str) -> None:
        decision = self.decision(subject, capability)
        if decision.allowed:
            return
        raise PermissionDeniedError(
            f"Permission denied: subject={subject!r} capability={capability!r} "
            f"reason={decision.reason}"
        )

    def tool_allowed(self, subject: str, tool_name: str) -> bool:
        return self.allowed(subject, self.tool_capability(tool_name))

    def effective_capabilities(self, subject: str) -> frozenset[str]:
        policy = self._policy(subject)
        granted = set(policy.capabilities) | set(self._scoped_grants(subject))
        return frozenset(granted & set(policy.ceiling))

    def requires_user_approval(self, capabilities: Iterable[str]) -> bool:
        required = set(self.config.permissions.require_user_approval)
        normalized = {str(item).strip().lower() for item in capabilities}
        return bool(normalized & required)

    def validate_policy(self, policy: "PermissionPolicyConfig") -> None:
        capabilities = set(policy.capabilities)
        ceiling = set(policy.ceiling)
        unknown = (capabilities | ceiling) - KNOWN_CAPABILITIES
        if unknown:
            raise ValueError(f"Unknown capabilities: {', '.join(sorted(unknown))}")
        excess = capabilities - ceiling
        if excess:
            raise ValueError(
                "Granted capabilities exceed privilege ceiling: "
                + ", ".join(sorted(excess))
            )

    @contextmanager
    def grant_scope(
        self,
        subject: str,
        capabilities: Iterable[str],
    ) -> Iterator[None]:
        """Temporarily grant capabilities without exceeding the subject ceiling."""
        normalized = frozenset(str(item).strip().lower() for item in capabilities)
        unknown = normalized - KNOWN_CAPABILITIES
        if unknown:
            raise ValueError(f"Unknown capabilities: {', '.join(sorted(unknown))}")
        ceiling = set(self._policy(subject).ceiling)
        excess = normalized - ceiling
        if excess:
            raise PermissionDeniedError(
                "Temporary grant exceeds privilege ceiling: " + ", ".join(sorted(excess))
            )
        current = _CURRENT_PERMISSION_SCOPE.get()
        inherited = (
            current.grants
            if current is not None and current.manager is self and current.subject == subject
            else frozenset()
        )
        token = _CURRENT_PERMISSION_SCOPE.set(
            _PermissionScope(self, subject, inherited | normalized)
        )
        try:
            yield
        finally:
            _CURRENT_PERMISSION_SCOPE.reset(token)

    def revoke_scoped(
        self,
        subject: str,
        capabilities: Iterable[str],
    ) -> None:
        """Revoke temporary grants from the current scope only."""
        scope = _CURRENT_PERMISSION_SCOPE.get()
        if scope is None or scope.manager is not self or scope.subject != subject:
            return
        revoked = {str(item).strip().lower() for item in capabilities}
        _CURRENT_PERMISSION_SCOPE.set(
            _PermissionScope(self, subject, scope.grants - revoked)
        )


def current_permission_allowed(capability: str) -> bool:
    """Check a capability against the currently bound permission scope."""
    scope = _CURRENT_PERMISSION_SCOPE.get()
    return scope is not None and scope.manager.allowed(scope.subject, capability)


def revoke_current_permission(capability: str) -> None:
    """Revoke one temporary capability from the current permission scope."""
    scope = _CURRENT_PERMISSION_SCOPE.get()
    if scope is not None:
        scope.manager.revoke_scoped(scope.subject, (capability,))
