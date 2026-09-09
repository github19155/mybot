"""Subagent role definitions, resolution, and user-facing role management."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from nanobot.agent.subagent_role_storage import (
    delete_dream_role,
    delete_role_usage,
    dream_role_entries,
    normalize_role_name,
    role_usage,
    workspace_from_config,
    write_dream_role,
)
from nanobot.agent.subagent_role_storage import (
    record_role_use as record_role_use,
)

if TYPE_CHECKING:
    from nanobot.config.schema import Config, SubagentRoleConfig
    from nanobot.webui.settings_services import WebUISettingsConfig


BUILTIN_SUBAGENT_ROLE_NAMES = (
    "general",
    "researcher",
    "planner",
    "coder",
    "debugger",
    "tester",
    "writer",
    "analyst",
)

SUBAGENT_ROLES: dict[str, dict[str, str]] = {
    "general": {
        "description": (
            "General-purpose worker for mixed, cross-domain, or uncategorized tasks. "
            "It is the permanent capable fallback when no specialist clearly fits."
        ),
        "permissions": "read-write-exec",
    },
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
_EXEC_BASE_TOOLS = {
    **_WRITE_TOOLS,
    "exec": "shell",
    "exec_session": "exec_session",
    "list_exec_sessions": "exec_session",
    "run_cli_app": "cli_apps",
}
_BROWSER_TOOLS = {
    "browser_open": "subagent_browser",
    "browser_snapshot": "subagent_browser",
    "browser_click": "subagent_browser",
    "browser_type": "subagent_browser",
    "browser_scroll": "subagent_browser",
    "browser_wait": "subagent_browser",
    "browser_screenshot": "subagent_browser",
    "browser_tabs": "subagent_browser",
    "browser_back": "subagent_browser",
    "browser_handoff": "subagent_browser",
    "browser_status": "subagent_browser",
    "browser_close": "subagent_browser",
}
_EXEC_TOOLS = {**_EXEC_BASE_TOOLS, **_BROWSER_TOOLS}

ROLE_TOOL_MODULES = {
    "read-only": _READ_TOOLS,
    "read-write": _WRITE_TOOLS,
    "read-write-exec": _EXEC_TOOLS,
}
_DEFAULT_TOOL_MODULES = {
    "read-only": _READ_TOOLS,
    "read-write": _WRITE_TOOLS,
    # Focused specialists keep Browser opt-in. General remains fully capable.
    "read-write-exec": _EXEC_BASE_TOOLS,
}
ALL_SUBAGENT_TOOL_NAMES = frozenset(_EXEC_TOOLS)


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
    category: str = "specialist"
    source: str = "builtin"
    status: str = "active"
    version: int = 1
    created_by: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

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
            "category": self.category,
            "source": self.source,
            "status": self.status,
            "version": self.version,
            "created_by": self.created_by,
            "usage": dict(self.usage),
        }


def _default_tools(permissions: str) -> tuple[str, ...]:
    return tuple(_DEFAULT_TOOL_MODULES[permissions])


def _default_tools_for_role(name: str, permissions: str) -> tuple[str, ...]:
    tools = _default_tools(permissions)
    if name == "general" and permissions == "read-write-exec":
        # General is intentionally the all-capability fallback, including Browser.
        return (*tools, *_BROWSER_TOOLS)
    return tools


def _permissions_for_custom_tools(tools: tuple[str, ...]) -> str:
    names = set(tools)
    if names & (set(_EXEC_TOOLS) - set(_WRITE_TOOLS)):
        return "read-write-exec"
    if names & (set(_WRITE_TOOLS) - set(_READ_TOOLS)):
        return "read-write"
    return "read-only"


def _validate_role_tools(role: "SubagentRoleConfig") -> None:
    requested = set(role.tools or ())
    unknown = requested - ALL_SUBAGENT_TOOL_NAMES
    if "subagent" in requested:
        unknown.add("subagent")
    if unknown:
        raise ValueError(f"Unknown or forbidden subagent tools: {', '.join(sorted(unknown))}")


def _validate_role_config(config: "Config", role: "SubagentRoleConfig") -> None:
    _validate_role_tools(role)
    if role.model is not None and not role.model.strip():
        raise ValueError("model must be a non-empty string")
    if (
        role.model_preset
        and role.model_preset != "default"
        and role.model_preset not in config.model_presets
    ):
        raise ValueError(f"Unknown model preset '{role.model_preset}'")


def _dream_role_config(
    config: "Config | None",
    name: str,
) -> tuple["SubagentRoleConfig | None", dict[str, Any]]:
    payload = dream_role_entries(config).get(name)
    if payload is None:
        return None, {}
    if payload.get("created_by") != "dream":
        raise ValueError(f"Dream specialist role '{name}' must declare created_by='dream'")

    from nanobot.config.schema import SubagentRoleConfig

    candidate = SubagentRoleConfig.model_validate(payload)
    if not (candidate.description or "").strip() or not (candidate.system_prompt or "").strip():
        raise ValueError(f"Dream specialist role '{name}' requires description and system_prompt")
    _validate_role_tools(candidate)
    status = str(payload.get("status") or "active").strip().lower()
    if status not in {"active", "cold"}:
        raise ValueError(f"Dream specialist role '{name}' has invalid status '{status}'")
    return candidate, payload


def resolve_role(config: "Config | None", name: str) -> ResolvedSubagentRole:
    """Resolve a builtin, config-managed role, or Dream-managed specialist."""
    normalized = normalize_role_name(name)
    builtin = SUBAGENT_ROLES.get(normalized)
    config_override = config.subagent_roles.get(normalized) if config is not None else None
    dream_override, dream_meta = (
        (None, {}) if builtin is not None else _dream_role_config(config, normalized)
    )
    if builtin is None and config_override is None and dream_override is None:
        raise ValueError(f"Unknown subagent role '{normalized}'")

    from nanobot.config.schema import SubagentRoleConfig

    if config_override is not None:
        override = config_override
        source = "builtin" if builtin is not None else "config"
        metadata: dict[str, Any] = {}
    elif dream_override is not None:
        override = dream_override
        source = "dream"
        metadata = dream_meta
    else:
        override = SubagentRoleConfig()
        source = "builtin"
        metadata = {}

    base_permissions = builtin["permissions"] if builtin else "read-only"
    description = (override.description or (builtin or {}).get("description") or "").strip()
    system_prompt = (override.system_prompt or description).strip()
    tools = tuple(
        override.tools
        if override.tools is not None
        else _default_tools_for_role(normalized, base_permissions)
    )
    permissions = base_permissions if builtin else _permissions_for_custom_tools(tools)
    status = str(metadata.get("status") or "active").strip().lower() or "active"
    raw_version = metadata.get("version", 1)
    version = raw_version if isinstance(raw_version, int) and not isinstance(raw_version, bool) else 1
    workspace = workspace_from_config(config)
    usage = role_usage(workspace, normalized) if workspace is not None else {}

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
        disabled=False if normalized == "general" else override.disabled,
        builtin=builtin is not None,
        permissions=permissions,
        category="general" if normalized == "general" else "specialist",
        source=source,
        status=status,
        version=max(1, version),
        created_by=(str(metadata.get("created_by")) if metadata.get("created_by") else None),
        usage=usage,
    )


def list_roles(config: "Config | None") -> list[ResolvedSubagentRole]:
    names: set[str] = set(BUILTIN_SUBAGENT_ROLE_NAMES)
    if config is not None:
        names.update(str(name) for name in config.subagent_roles)
        names.update(dream_role_entries(config))
    return [resolve_role(config, name) for name in sorted(names)]


class SubagentRoleStore:
    """User-facing role CRUD; persistence mechanics stay in the storage layer."""

    def __init__(self, config: "Config") -> None:
        self.config = config
        self._store: WebUISettingsConfig | None = None
        if config.source_path is not None:
            from nanobot.webui.settings_services import WebUISettingsConfig

            self._store = WebUISettingsConfig(config.source_path)

    @property
    def workspace(self):
        workspace = workspace_from_config(self.config)
        assert workspace is not None
        return workspace

    def _commit(self, mutation: Callable[["Config"], None]) -> None:
        def apply(config: "Config") -> "Config":
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
        if (
            normalized in SUBAGENT_ROLES
            or normalized in self.config.subagent_roles
            or normalized in dream_role_entries(self.config)
        ):
            raise ValueError(f"Role '{normalized}' already exists")

        from nanobot.config.schema import SubagentRoleConfig

        candidate = SubagentRoleConfig.model_validate(values)
        if not (candidate.description or "").strip() or not (candidate.system_prompt or "").strip():
            raise ValueError("Custom role requires non-empty description and system_prompt")

        def mutate(config: "Config") -> None:
            _validate_role_config(config, candidate)
            config.subagent_roles[normalized] = candidate

        self._commit(mutate)
        return self.get(normalized)

    def _update_dream_role(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_role_name(name)
        current = dream_role_entries(self.config).get(normalized)
        if not current or current.get("created_by") != "dream":
            raise ValueError(f"Unknown Dream-managed subagent role '{normalized}'")
        merged = current | values | {"name": normalized, "created_by": "dream"}

        from nanobot.config.schema import SubagentRoleConfig

        candidate = SubagentRoleConfig.model_validate(merged)
        if not (candidate.description or "").strip() or not (candidate.system_prompt or "").strip():
            raise ValueError("Dream specialist role requires non-empty description and system_prompt")
        _validate_role_config(self.config, candidate)
        status = str(merged.get("status") or "active").strip().lower()
        if status not in {"active", "cold"}:
            raise ValueError("Dream specialist status must be active or cold")
        write_dream_role(self.workspace, normalized, merged)
        return self.get(normalized)

    def update(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_role_name(name)
        if normalized == "general" and values.get("disabled") is True:
            raise ValueError("The general subagent role is permanent and cannot be disabled")

        current = self.config.subagent_roles.get(normalized)
        dream_exists = normalized in dream_role_entries(self.config)
        if normalized not in SUBAGENT_ROLES and current is None and not dream_exists:
            raise ValueError(f"Unknown subagent role '{normalized}'")
        if dream_exists and current is None and normalized not in SUBAGENT_ROLES:
            return self._update_dream_role(normalized, values)

        merged = current.model_dump() if current is not None else {}
        if normalized == "general" and current is None:
            base = SUBAGENT_ROLES["general"]
            merged.update({
                "description": base["description"],
                "system_prompt": base["description"],
                "tools": list(_default_tools_for_role("general", base["permissions"])),
            })
        merged |= values

        from nanobot.config.schema import SubagentRoleConfig

        candidate = SubagentRoleConfig.model_validate(merged)
        if normalized not in SUBAGENT_ROLES and (
            not (candidate.description or "").strip()
            or not (candidate.system_prompt or "").strip()
        ):
            raise ValueError("Custom role requires non-empty description and system_prompt")

        def mutate(config: "Config") -> None:
            _validate_role_config(config, candidate)
            config.subagent_roles[normalized] = candidate

        self._commit(mutate)
        return self.get(normalized)

    def delete(self, name: str) -> dict[str, Any]:
        normalized = normalize_role_name(name)
        if normalized == "general":
            raise ValueError("The general subagent role is permanent and cannot be deleted")
        if normalized in SUBAGENT_ROLES:
            self.update(normalized, {"disabled": True})
            return self.get(normalized)
        if normalized in self.config.subagent_roles:
            def mutate(config: "Config") -> None:
                config.subagent_roles.pop(normalized, None)

            self._commit(mutate)
            delete_role_usage(self.workspace, normalized)
            return {"name": normalized, "deleted": True}
        if delete_dream_role(self.workspace, normalized):
            delete_role_usage(self.workspace, normalized)
            return {"name": normalized, "deleted": True}
        raise ValueError(f"Unknown subagent role '{normalized}'")

    def reset(self, name: str) -> dict[str, Any]:
        normalized = normalize_role_name(name)
        if normalized not in SUBAGENT_ROLES:
            raise ValueError("Only builtin roles can be reset")

        def mutate(config: "Config") -> None:
            config.subagent_roles.pop(normalized, None)

        self._commit(mutate)
        return self.get(normalized)
