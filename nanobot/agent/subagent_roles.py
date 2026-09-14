"""Subagent role definitions, resolution, and user-facing role management."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from nanobot.agent.subagent_role_storage import (
    delete_role_usage,
    normalize_role_name,
    role_usage,
    workspace_from_config,
)
from nanobot.config.store import ConfigStore

if TYPE_CHECKING:
    from nanobot.config.schema import Config, SubagentRoleConfig


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
    "general": {"description": "General-purpose worker for mixed, cross-domain, or uncategorized tasks."},
    "researcher": {"description": "Find and compare evidence; cite sources and distinguish facts from inference."},
    "planner": {"description": "Inspect the task and propose a concrete plan, dependencies, and acceptance checks."},
    "coder": {"description": "Implement assigned changes using existing project conventions."},
    "debugger": {"description": "Reproduce failures, identify root causes, and apply focused fixes."},
    "tester": {"description": "Exercise assigned behavior and report reproducible failures and evidence."},
    "writer": {"description": "Read source material and write clear, accurate documentation or prose."},
    "analyst": {"description": "Analyze code or data, check assumptions, and report evidence-backed conclusions."},
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
_IMAGE_TOOLS = {
    "image_analyze": "image_analysis",
    "generate_image": "image_generation",
}
_PROGRESS_TOOLS = {
    "report_progress": "report_progress",
}
_EXEC_TOOLS = {**_EXEC_BASE_TOOLS, **_BROWSER_TOOLS}

TOOL_MODULES = {**_EXEC_TOOLS, **_IMAGE_TOOLS, **_PROGRESS_TOOLS}
ALL_SUBAGENT_TOOL_NAMES = frozenset(TOOL_MODULES)


@dataclass(frozen=True, slots=True)
class ResolvedSubagentRole:
    """Effective immutable role snapshot used by one child launch."""

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
    category: str = "specialist"
    source: str = "config"
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
            "category": self.category,
            "source": self.source,
            "usage": dict(self.usage),
        }


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


def resolve_role(config: "Config | None", name: str) -> ResolvedSubagentRole:
    """Resolve a builtin or config-managed specialist role."""
    normalized = normalize_role_name(name)
    builtin = SUBAGENT_ROLES.get(normalized)
    override = config.subagent_roles.get(normalized) if config is not None else None
    if builtin is None and override is None:
        raise ValueError(f"Unknown subagent role '{normalized}'")

    if override is None:
        from nanobot.config.schema import SubagentRoleConfig

        override = SubagentRoleConfig()
        source = "builtin"
    else:
        source = "builtin" if builtin is not None else "config"
        _validate_role_config(config, override) if config is not None else None

    description = (override.description or (builtin or {}).get("description") or "").strip()
    system_prompt = (override.system_prompt or description).strip()
    if normalized == "general":
        requested_tools = tuple(TOOL_MODULES)
    else:
        requested_tools = tuple(override.tools) if override.tools is not None else tuple(TOOL_MODULES)
    # Milestone reporting is transport metadata, not worker authority. Keep it
    # available even when a Specialist narrows its actual work capabilities.
    tools = tuple(dict.fromkeys((*requested_tools, "report_progress")))
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
        category="general" if normalized == "general" else "specialist",
        source=source,
        usage=usage,
    )


def list_roles(config: "Config | None") -> list[ResolvedSubagentRole]:
    names: set[str] = set(BUILTIN_SUBAGENT_ROLE_NAMES)
    if config is not None:
        names.update(str(name) for name in config.subagent_roles)
    return [resolve_role(config, name) for name in sorted(names)]


class SubagentRoleStore:
    """Canonical role CRUD. Persistent specialists live only in config."""

    def __init__(self, config: "Config") -> None:
        self.config = config
        self._store: ConfigStore | None = None
        if config.source_path is not None:
            self._store = ConfigStore(config.source_path)

    @property
    def workspace(self) -> Path:
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
        if normalized in SUBAGENT_ROLES or normalized in self.config.subagent_roles:
            raise ValueError(f"Role '{normalized}' already exists")

        from nanobot.config.schema import SubagentRoleConfig

        candidate = SubagentRoleConfig.model_validate(values)
        if not (candidate.description or "").strip() or not (candidate.system_prompt or "").strip():
            raise ValueError("Custom role requires non-empty description and system_prompt")
        _validate_role_config(self.config, candidate)

        def mutate(config: "Config") -> None:
            config.subagent_roles[normalized] = candidate

        self._commit(mutate)
        return self.get(normalized)

    def update(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_role_name(name)
        if normalized == "general" and values.get("disabled") is True:
            raise ValueError("The general subagent role is permanent and cannot be disabled")

        current = self.config.subagent_roles.get(normalized)
        if normalized not in SUBAGENT_ROLES and current is None:
            raise ValueError(f"Unknown subagent role '{normalized}'")
        merged = current.model_dump() if current is not None else {}
        merged |= values

        from nanobot.config.schema import SubagentRoleConfig

        candidate = SubagentRoleConfig.model_validate(merged)
        if normalized not in SUBAGENT_ROLES and (
            not (candidate.description or "").strip()
            or not (candidate.system_prompt or "").strip()
        ):
            raise ValueError("Custom role requires non-empty description and system_prompt")
        _validate_role_config(self.config, candidate)

        def mutate(config: "Config") -> None:
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
        if normalized not in self.config.subagent_roles:
            raise ValueError(f"Unknown subagent role '{normalized}'")

        def mutate(config: "Config") -> None:
            config.subagent_roles.pop(normalized, None)

        self._commit(mutate)
        delete_role_usage(self.workspace, normalized)
        return {"name": normalized, "deleted": True}

    def reset(self, name: str) -> dict[str, Any]:
        normalized = normalize_role_name(name)
        if normalized not in SUBAGENT_ROLES:
            raise ValueError("Only builtin roles can be reset")

        def mutate(config: "Config") -> None:
            config.subagent_roles.pop(normalized, None)

        self._commit(mutate)
        return self.get(normalized)
