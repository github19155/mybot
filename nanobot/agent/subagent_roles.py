"""Builtin and runtime-managed subagent role definitions."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from nanobot.config.schema import Config, SubagentRoleConfig
    from nanobot.webui.settings_services import WebUISettingsConfig


BUILTIN_SUBAGENT_ROLE_NAMES = (
    "researcher", "planner", "coder", "debugger", "tester", "writer", "analyst",
)

SUBAGENT_ROLES: dict[str, dict[str, str]] = {
    "researcher": {
        "description": "Find and compare evidence; cite sources and distinguish facts from inference.",
        "permissions": "read-only",
    },
    "planner": {
        "description": "Inspect the task and propose a concrete plan, dependencies, and acceptance checks.",
        "permissions": "read-only",
    },
    "coder": {
        "description": "Implement the assigned changes using existing project conventions.",
        "permissions": "read-write-exec",
    },
    "debugger": {
        "description": "Reproduce the failure, find its root cause, and apply a focused fix.",
        "permissions": "read-write-exec",
    },
    "tester": {
        "description": "Exercise the assigned behavior and report reproducible failures and evidence.",
        "permissions": "read-write-exec",
    },
    "writer": {
        "description": "Read source material and write clear, accurate documentation or prose.",
        "permissions": "read-write",
    },
    "analyst": {
        "description": "Analyze code or data, check assumptions, and report evidence-backed conclusions.",
        "permissions": "read-write-exec",
    },
}

_READ_TOOLS = {
    "read_file": "filesystem",
    "list_dir": "filesystem",
    "find_files": "search",
    "grep": "search",
    "web_search": "web",
    "web_fetch": "web",
}
_WRITE_TOOLS = {
    **_READ_TOOLS,
    "write_file": "filesystem",
    "edit_file": "filesystem",
    "apply_patch": "apply_patch",
}
_EXEC_TOOLS = {
    **_WRITE_TOOLS,
    "exec": "shell",
    "exec_session": "exec_session",
    "list_exec_sessions": "exec_session",
    "run_cli_app": "cli_apps",
}
ROLE_TOOL_MODULES = {
    "read-only": _READ_TOOLS,
    "read-write": _WRITE_TOOLS,
    "read-write-exec": _EXEC_TOOLS,
}
ALL_SUBAGENT_TOOL_NAMES = frozenset(_EXEC_TOOLS)
ROLE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


@dataclass(frozen=True, slots=True)
class ResolvedSubagentRole:
    """Effective role snapshot used by one child launch."""

    name: str
    description: str
    system_prompt: str
    tools: tuple[str, ...]
    model: str | None
    model_preset: str | None
    thinking: str | None
    temperature: float | None
    timeout_seconds: float | None
    context: str
    disabled: bool
    builtin: bool
    permissions: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "tools": list(self.tools),
            "model": self.model,
            "model_preset": self.model_preset,
            "thinking": self.thinking,
            "temperature": self.temperature,
            "timeout_seconds": self.timeout_seconds,
            "context": self.context,
            "disabled": self.disabled,
            "builtin": self.builtin,
            "permissions": self.permissions,
        }


def normalize_role_name(name: object) -> str:
    """Normalize and validate a role identifier."""
    if not isinstance(name, str):
        raise ValueError("role name must be a string")
    normalized = name.strip().lower()
    if not ROLE_NAME_PATTERN.fullmatch(normalized):
        raise ValueError("role name must match [a-z][a-z0-9_-]{0,63}")
    return normalized


def _default_tools(permissions: str) -> tuple[str, ...]:
    return tuple(ROLE_TOOL_MODULES[permissions])


def _validate_role_tools(role: "SubagentRoleConfig") -> None:
    requested = set(role.tools or ())
    unknown = requested - ALL_SUBAGENT_TOOL_NAMES
    if "subagent" in requested:
        unknown.add("subagent")
    if unknown:
        raise ValueError(f"Unknown or forbidden subagent tools: {', '.join(sorted(unknown))}")


def _validate_role_config(config: Config, role: "SubagentRoleConfig") -> None:
    _validate_role_tools(role)
    if role.model is not None and not role.model.strip():
        raise ValueError("model must be a non-empty string")
    if (
        role.model_preset
        and role.model_preset != "default"
        and role.model_preset not in config.model_presets
    ):
        raise ValueError(f"Unknown model preset '{role.model_preset}'")


def resolve_role(config: Config | None, name: str) -> ResolvedSubagentRole:
    """Resolve a builtin template plus its persisted override."""
    normalized = normalize_role_name(name)
    builtin = SUBAGENT_ROLES.get(normalized)
    if builtin is None and (config is None or normalized not in config.subagent_roles):
        raise ValueError(f"Unknown subagent role '{normalized}'")
    override = config.subagent_roles.get(normalized) if config is not None else None
    if override is None:
        from nanobot.config.schema import SubagentRoleConfig

        override = SubagentRoleConfig()
    permissions = builtin["permissions"] if builtin else "read-only"
    description = (override.description or (builtin or {}).get("description") or "").strip()
    system_prompt = (override.system_prompt or description).strip()
    tools = tuple(
        override.tools if override.tools is not None else _default_tools(permissions)
    )
    return ResolvedSubagentRole(
        name=normalized,
        description=description,
        system_prompt=system_prompt,
        tools=tools,
        model=override.model,
        model_preset=override.model_preset,
        thinking=override.thinking,
        temperature=override.temperature,
        timeout_seconds=override.timeout_seconds,
        context=override.context or "fresh",
        disabled=override.disabled,
        builtin=builtin is not None,
        permissions=permissions,
    )


def list_roles(config: Config | None) -> list[ResolvedSubagentRole]:
    names: set[str] = set(BUILTIN_SUBAGENT_ROLE_NAMES)
    if config is not None:
        names.update(str(name) for name in config.subagent_roles)
    return [resolve_role(config, name) for name in sorted(names)]


class SubagentRoleStore:
    """Runtime role CRUD backed by the existing locked config writer."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._store: WebUISettingsConfig | None = None
        if config.source_path is not None:
            from nanobot.webui.settings_services import WebUISettingsConfig

            self._store = WebUISettingsConfig(config.source_path)

    def _commit(self, mutation: Callable[["Config"], None]) -> None:
        def apply(config: Config) -> Config:
            mutation(config)
            return config

        if self._store is not None:
            updated = self._store.update(apply)
        else:
            updated = deepcopy(self.config)
            apply(updated)
        self.config.subagent_roles = updated.subagent_roles

    def list(self) -> list[dict[str, Any]]:
        return [role.as_dict() for role in list_roles(self.config)]

    def get(self, name: str) -> dict[str, Any]:
        return resolve_role(self.config, name).as_dict()

    def create(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_role_name(name)
        if normalized in SUBAGENT_ROLES or normalized in self.config.subagent_roles:
            raise ValueError(f"Role '{normalized}' already exists")
        from nanobot.config.schema import SubagentRoleConfig

        candidate = SubagentRoleConfig.model_validate(values)
        if not (candidate.description or "").strip() or not (candidate.system_prompt or "").strip():
            raise ValueError("Custom role requires non-empty description and system_prompt")

        def mutate(config: Config) -> None:
            _validate_role_config(config, candidate)
            config.subagent_roles[normalized] = candidate

        self._commit(mutate)
        return self.get(normalized)

    def update(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_role_name(name)
        current = self.config.subagent_roles.get(normalized)
        if normalized not in SUBAGENT_ROLES and current is None:
            raise ValueError(f"Unknown subagent role '{normalized}'")
        merged = (current.model_dump() if current is not None else {}) | values
        from nanobot.config.schema import SubagentRoleConfig

        candidate = SubagentRoleConfig.model_validate(merged)
        if normalized not in SUBAGENT_ROLES and (
            not (candidate.description or "").strip()
            or not (candidate.system_prompt or "").strip()
        ):
            raise ValueError("Custom role requires non-empty description and system_prompt")

        def mutate(config: Config) -> None:
            _validate_role_config(config, candidate)
            config.subagent_roles[normalized] = candidate

        self._commit(mutate)
        return self.get(normalized)

    def delete(self, name: str) -> dict[str, Any]:
        normalized = normalize_role_name(name)
        if normalized in SUBAGENT_ROLES:
            self.update(normalized, {"disabled": True})
        elif normalized in self.config.subagent_roles:
            def mutate(config: Config) -> None:
                config.subagent_roles.pop(normalized, None)

            self._commit(mutate)
        else:
            raise ValueError(f"Unknown subagent role '{normalized}'")
        return self.get(normalized) if normalized in SUBAGENT_ROLES else {"name": normalized, "deleted": True}

    def reset(self, name: str) -> dict[str, Any]:
        normalized = normalize_role_name(name)
        if normalized not in SUBAGENT_ROLES:
            raise ValueError("Only builtin roles can be reset")

        def mutate(config: Config) -> None:
            config.subagent_roles.pop(normalized, None)

        self._commit(mutate)
        return self.get(normalized)
