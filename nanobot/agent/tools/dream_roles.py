"""Restricted role-management capability for Dream specialist evolution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nanobot.agent.subagent_role_storage import (
    candidate_entries,
    dream_role_entries_for_workspace,
    normalize_role_name,
    observe_candidate,
    remove_candidate,
    role_usage,
    write_dream_role,
)
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.schema import StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from nanobot.config.schema import Config, SubagentRoleConfig


_ACTIONS = (
    "list",
    "get",
    "candidates",
    "observe",
    "drop_candidate",
    "create",
    "update",
    "mark_cold",
    "activate",
)


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema("Role evolution operation", enum=list(_ACTIONS)),
        role=StringSchema("Normalized role/candidate name", nullable=True),
        responsibility=StringSchema("Recurring responsibility for candidate evidence", nullable=True),
        evidence=StringSchema("Short evidence summary for one occurrence", nullable=True),
        occurrence=StringSchema(
            "Stable task/source identity for the underlying occurrence; repeated wording from the same occurrence must reuse this value",
            nullable=True,
        ),
        values={"type": "object", "additionalProperties": True},
        required=["action"],
        additional_properties=False,
    )
)
class DreamRoleTool(Tool):
    """Dream-only role manager. It intentionally has no specialist delete operation."""

    _plugin_discoverable = False

    def __init__(self, workspace: Path, config: "Config | None" = None) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.config = config

    @property
    def name(self) -> str:
        return "dream_roles"

    @property
    def description(self) -> str:
        return (
            "Manage Dream-owned specialist roles and recurring-role evidence. Use observe before "
            "create; pass a stable occurrence identity so repeated wording from one task is counted "
            "once. Creation requires at least two distinct persisted observations. Role updates are "
            "versioned and retain a bounded evolution history; specialists may be marked cold or "
            "reactivated, but this capability can never delete or disable them."
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def _role_rows(self) -> list[dict[str, Any]]:
        return [
            {**payload, "usage": role_usage(self.workspace, name)}
            for name, payload in sorted(dream_role_entries_for_workspace(self.workspace).items())
        ]

    def _validate_values(self, name: str, values: dict[str, Any]) -> "SubagentRoleConfig":
        from nanobot.agent.subagent_roles import (
            ALL_SUBAGENT_TOOL_NAMES,
            BUILTIN_SUBAGENT_ROLE_NAMES,
        )
        from nanobot.config.schema import SubagentRoleConfig

        if name in BUILTIN_SUBAGENT_ROLE_NAMES:
            raise ValueError("Dream cannot create or replace builtin roles")
        if self.config is not None and name in self.config.subagent_roles:
            raise ValueError("Dream cannot replace a user/config-managed role")
        protected = {"name", "created_by", "version"} & set(values)
        if protected:
            raise ValueError(f"Dream role fields are runtime-managed: {', '.join(sorted(protected))}")

        candidate = SubagentRoleConfig.model_validate(values)
        if not (candidate.description or "").strip() or not (candidate.system_prompt or "").strip():
            raise ValueError("Dream specialist requires description and system_prompt")
        requested = set(candidate.tools or ())
        unknown = requested - ALL_SUBAGENT_TOOL_NAMES
        if unknown:
            raise ValueError(f"Unknown specialist tools: {', '.join(sorted(unknown))}")
        if candidate.disabled:
            raise ValueError("Dream cannot disable specialist roles")
        if (
            self.config is not None
            and candidate.model_preset
            and candidate.model_preset != "default"
            and candidate.model_preset not in self.config.model_presets
        ):
            raise ValueError(f"Unknown model preset '{candidate.model_preset}'")
        return candidate

    @staticmethod
    def _evolution_history(
        current: dict[str, Any] | None,
        requested: Any,
    ) -> list[str]:
        history = [
            str(item).strip()
            for item in (current or {}).get("evolution", [])
            if str(item).strip()
        ]
        if isinstance(requested, list):
            additions = [str(item).strip() for item in requested if str(item).strip()]
            for item in additions:
                if item not in history:
                    history.append(item)
        return history[-12:]

    def _write_role(
        self,
        name: str,
        values: dict[str, Any],
        *,
        current: dict[str, Any] | None,
    ) -> dict[str, Any]:
        candidate = self._validate_values(name, values)
        old_version = current.get("version", 0) if current else 0
        if not isinstance(old_version, int) or isinstance(old_version, bool):
            old_version = 0
        evolution = self._evolution_history(current, values.get("evolution"))

        payload = candidate.model_dump(exclude_none=True)
        payload.pop("evolution", None)
        payload.update({
            "name": name,
            "created_by": "dream",
            "status": str(values.get("status") or (current or {}).get("status") or "active"),
            "version": max(1, old_version + 1),
        })
        if evolution:
            payload["evolution"] = evolution
        if payload["status"] not in {"active", "cold"}:
            raise ValueError("Dream specialist status must be active or cold")
        write_dream_role(self.workspace, name, payload)
        return {**payload, "usage": role_usage(self.workspace, name)}

    async def execute(
        self,
        action: str,
        role: str | None = None,
        responsibility: str | None = None,
        evidence: str | None = None,
        occurrence: str | None = None,
        values: dict[str, Any] | None = None,
        **_: Any,
    ) -> str:
        try:
            if action == "list":
                return self._json(self._role_rows())
            if action == "candidates":
                return self._json(candidate_entries(self.workspace))

            if not role:
                return ToolResult.error(f"Error: {action} requires role")
            name = normalize_role_name(role)

            if action == "get":
                payload = dream_role_entries_for_workspace(self.workspace).get(name)
                if payload is None:
                    return ToolResult.error(f"Error: unknown Dream specialist '{name}'")
                return self._json({**payload, "usage": role_usage(self.workspace, name)})

            if action == "observe":
                if not responsibility or not evidence:
                    return ToolResult.error("Error: observe requires responsibility and evidence")
                return self._json(observe_candidate(
                    self.workspace,
                    name,
                    responsibility=responsibility,
                    evidence=evidence,
                    occurrence=occurrence,
                ))

            if action == "drop_candidate":
                existed = name in candidate_entries(self.workspace)
                remove_candidate(self.workspace, name)
                return self._json({"name": name, "candidate_removed": existed})

            existing = dream_role_entries_for_workspace(self.workspace).get(name)
            if action == "create":
                if existing is not None:
                    return ToolResult.error(f"Error: Dream specialist '{name}' already exists")
                candidate = candidate_entries(self.workspace).get(name, {})
                if candidate.get("evidence_count", 0) < 2:
                    return ToolResult.error(
                        "Error: create requires at least two distinct persisted candidate observations"
                    )
                created = self._write_role(name, dict(values or {}), current=None)
                remove_candidate(self.workspace, name)
                return self._json(created)

            if existing is None or existing.get("created_by") != "dream":
                return ToolResult.error(f"Error: unknown Dream-managed specialist '{name}'")

            if action == "update":
                if not values:
                    return ToolResult.error("Error: update requires values")
                merged = {
                    key: value
                    for key, value in existing.items()
                    if key not in {"name", "created_by", "version", "usage", "evolution"}
                }
                merged.update(values)
                return self._json(self._write_role(name, merged, current=existing))

            if action in {"mark_cold", "activate"}:
                merged = {
                    key: value
                    for key, value in existing.items()
                    if key not in {"name", "created_by", "version", "usage", "evolution"}
                }
                merged["status"] = "cold" if action == "mark_cold" else "active"
                return self._json(self._write_role(name, merged, current=existing))

            return ToolResult.error(f"Error: unknown Dream role action '{action}'")
        except (OSError, ValueError) as exc:
            return ToolResult.error(f"Error: {exc}")
