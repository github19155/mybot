"""Unified tool for running and controlling in-process subagents."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

from nanobot.agent.subagent_roles import (
    ALL_SUBAGENT_TOOL_NAMES,
    ResolvedSubagentRole,
    record_role_use,
)
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.agent.tools.registry import is_tool_error_result
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    NumberSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.agent.tools.subagent_browser import bind_subagent_browser_bus
from nanobot.security.workspace_access import current_workspace_scope

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.tools.context import ToolContext


_ACTIONS = (
    "run", "status", "steer", "stop",
    "role.list", "role.get", "role.create", "role.update", "role.delete", "role.reset",
)
_THINKING = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "adaptive")


def _fork_snapshot(history: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Copy portable history fields and drop provider-private reasoning metadata."""
    private_keys = {
        "reasoning_content", "reasoning_details", "thinking_blocks", "thought_signature",
        "provider_state", "_provider_state", "_reasoning", "_reasoning_delta",
    }
    snapshot: list[dict[str, Any]] = []
    for message in history:
        clean = {
            key: copy.deepcopy(value)
            for key, value in message.items()
            if key not in private_keys
        }
        if clean.get("role") in {"system", "tool"} and clean.get("role") == "tool":
            # Tool results are portable, but an orphaned provider tool call is not.
            clean.pop("tool_call_id", None)
        snapshot.append(clean)
    return snapshot


def _nonempty_override(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty string")
    return normalized


def _work_role_definition(
    general: dict[str, Any],
    *,
    description: str | None,
    system_prompt: str | None,
    tools: list[str] | None,
) -> ResolvedSubagentRole:
    """Create one non-persistent worker snapshot from the permanent general baseline."""
    description = _nonempty_override(description, "description")
    system_prompt = _nonempty_override(system_prompt, "system_prompt")
    requested_tools = tuple(tools if tools is not None else general.get("tools", ()))
    unknown = set(requested_tools) - ALL_SUBAGENT_TOOL_NAMES
    if "subagent" in requested_tools:
        unknown.add("subagent")
    if unknown:
        raise ValueError(
            f"Unknown or forbidden WorkAgent tools: {', '.join(sorted(unknown))}"
        )
    base_description = str(general.get("description") or "General-purpose worker.").strip()
    effective_description = description or "Task-scoped worker for the assigned responsibility."
    effective_prompt = system_prompt or description or str(
        general.get("system_prompt") or base_description
    ).strip()
    return ResolvedSubagentRole(
        name="work",
        description=effective_description,
        system_prompt=effective_prompt,
        tools=requested_tools,
        model=None,
        model_preset=None,
        thinking=None,
        temperature=None,
        timeout_seconds=None,
        context="fresh",
        disabled=False,
        builtin=False,
        permissions="read-write-exec",
        category="work",
        source="ephemeral",
        status="active",
        version=1,
        created_by=None,
        usage={},
    )


_SUBAGENT_PARAMETERS = tool_parameters_schema(
    action=StringSchema("Operation to perform", enum=list(_ACTIONS)),
    task=StringSchema("Task brief for run", nullable=True),
    label=StringSchema("Optional short label", nullable=True),
    task_id=StringSchema("Task ID for status/steer/stop", nullable=True),
    message=StringSchema("Steering message", nullable=True),
    role=StringSchema("Persistent role name; omitted uses general or a task-scoped WorkAgent", nullable=True),
    system_prompt=StringSchema(
        "Task-scoped WorkAgent system prompt for run, or role system prompt for role mutations",
        nullable=True,
    ),
    disabled=BooleanSchema(description="Disable a role", nullable=True),
    model=StringSchema("Explicit provider/model", nullable=True),
    model_preset=StringSchema("Configured model preset", nullable=True),
    thinking=StringSchema("Thinking effort", enum=list(_THINKING), nullable=True),
    temperature=NumberSchema(
        description="Sampling temperature",
        minimum=0.0,
        maximum=2.0,
        nullable=True,
    ),
    timeout_seconds=NumberSchema(
        description="Per-task LLM timeout in seconds",
        minimum=0.001,
        nullable=True,
    ),
    context=StringSchema("History mode", enum=["fresh", "fork"], nullable=True),
    wait=BooleanSchema(
        description="Wait for the child result; defaults to false",
        default=False,
    ),
    values={"type": "object", "additionalProperties": True},
    tools=ArraySchema(
        StringSchema("Allowed child tool name"),
        description="Task-scoped WorkAgent tools for run, or requested tools for a role",
        nullable=True,
    ),
    required=["action"],
    additional_properties=True,
)
_SUBAGENT_PARAMETERS["properties"]["description"] = StringSchema(
    "Task-scoped WorkAgent description for run, or role description for role mutations",
    nullable=True,
).to_json_schema()


@tool_parameters(_SUBAGENT_PARAMETERS)
class SubagentTool(Tool):
    """Run, inspect, steer, stop, and configure subagents."""

    def __init__(self, manager: "SubagentManager") -> None:
        self._manager = manager
        workspace = getattr(manager, "workspace", None)
        bus = getattr(manager, "bus", None)
        if workspace is not None and bus is not None:
            bind_subagent_browser_bus(workspace, bus)

    @classmethod
    def create(cls, ctx: "ToolContext") -> Tool:
        manager = ctx.subagent_manager
        if manager is None:
            raise RuntimeError("SubagentTool requires an initialized subagent manager")
        return cls(manager)

    @property
    def name(self) -> str:
        return "subagent"

    @property
    def description(self) -> str:
        return (
            "Run and control child agents. Prefer a matching persistent specialist role when one "
            "clearly fits; otherwise omit role to use the permanent general worker. For one-off "
            "work that needs a task-specific identity or capability set, omit role and provide "
            "description, system_prompt, and/or tools to create a non-persistent WorkAgent snapshot. "
            "Do not combine those WorkAgent identity/tool overrides with a persistent role. Use "
            "role.list to discover current built-in, user, and Dream-managed roles. Default long or "
            "independent work to run with wait=false, especially installs/downloads, builds, broad "
            "test suites, environment setup, multi-step debugging, or work likely to take more than "
            "about 10 seconds. Browser automation is a tool capability of eligible workers, not a "
            "separate Agent type. Background results are delivered automatically: do not repeatedly "
            "poll status or sleep-and-check. Use wait=true only for short child work whose result is "
            "required before the current turn can proceed. Use status/steer/stop by task ID when "
            "needed. Role and model settings apply only to the child; children cannot create children."
        )

    @property
    def concurrency_safe(self) -> bool:
        return True

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    async def execute(
        self,
        action: str,
        task: str | None = None,
        label: str | None = None,
        task_id: str | None = None,
        message: str | None = None,
        role: str | None = None,
        model: str | None = None,
        model_preset: str | None = None,
        thinking: str | None = None,
        temperature: float | None = None,
        timeout_seconds: float | None = None,
        context: str | None = None,
        wait: bool = False,
        values: dict[str, Any] | None = None,
        tools: list[str] | None = None,
        description: str | None = None,
        system_prompt: str | None = None,
        disabled: bool | None = None,
        **_: Any,
    ) -> str:
        request = current_request_context()
        if request is None:
            return ToolResult.error("Error: subagent requires an active request context")
        session_key = request.session_key or f"{request.channel}:{request.chat_id}"

        if action == "run":
            if not task or not task.strip():
                return ToolResult.error("Error: run requires a non-empty task")
            runtime = request.runtime
            if runtime is None:
                return ToolResult.error("Error: subagent run requires an active model runtime")
            work_override = description is not None or system_prompt is not None or tools is not None
            if role is not None and work_override:
                return ToolResult.error(
                    "Error: description, system_prompt, and tools are task-scoped WorkAgent "
                    "overrides and cannot be combined with role"
                )
            role_definition: ResolvedSubagentRole | None = None
            selected_role = role or "general"
            if work_override:
                try:
                    role_definition = _work_role_definition(
                        self._manager.role_get("general"),
                        description=description,
                        system_prompt=system_prompt,
                        tools=tools,
                    )
                except ValueError as exc:
                    return ToolResult.error(f"Error: {exc}")
                selected_role = "work"
            method = self._manager.run_inline if wait else self._manager.spawn
            result = await method(
                task=task.strip(),
                runtime=runtime,
                label=label,
                role=selected_role,
                role_definition=role_definition,
                model=model,
                model_preset=model_preset,
                thinking=thinking,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
                context=context,
                origin_channel=request.channel,
                origin_chat_id=request.chat_id,
                session_key=session_key,
                origin_message_id=request.message_id,
                workspace_scope=current_workspace_scope(),
                allowed_tools=set(request.allowed_tools),
                fork_history=_fork_snapshot(request.conversation_history),
            )
            if role_definition is None and not is_tool_error_result(result):
                workspace = getattr(self._manager, "workspace", None)
                if workspace is not None:
                    try:
                        record_role_use(workspace, selected_role)
                    except OSError:
                        # Usage telemetry is advisory input for Dream and must never
                        # turn a successfully launched child into a failed tool call.
                        pass
            return result

        if action == "status":
            if task_id and not self._manager.owns_task(task_id, session_key):
                return ToolResult.error("Error: task not found in the current session")
            return self._json(self._manager.status_snapshot(session_key, task_id))

        if action == "steer":
            if not task_id or not message or not message.strip():
                return ToolResult.error("Error: steer requires task_id and message")
            if not self._manager.owns_task(task_id, session_key):
                return ToolResult.error("Error: task not found in the current session")
            if await self._manager.steer(task_id, message.strip()):
                return f"Steering queued for subagent {task_id}."
            return ToolResult.error("Error: subagent is no longer active")

        if action == "stop":
            if not task_id or not self._manager.owns_task(task_id, session_key):
                return ToolResult.error("Error: task not found in the current session")
            if await self._manager.stop(task_id):
                return f"Stop requested for subagent {task_id}."
            return ToolResult.error("Error: subagent is no longer active")

        try:
            role_values = dict(values or {})
            for key, value in {
                "description": description,
                "system_prompt": system_prompt,
                "model": model,
                "model_preset": model_preset,
                "thinking": thinking,
                "temperature": temperature,
                "timeout_seconds": timeout_seconds,
                "context": context,
                "tools": tools,
                "disabled": disabled,
            }.items():
                if value is not None:
                    role_values[key] = value
            if action == "role.list":
                return self._json(self._manager.role_list())
            if action == "role.get":
                if not role:
                    return ToolResult.error("Error: role.get requires role")
                return self._json(self._manager.role_get(role))
            if action == "role.create":
                if not role or not role_values:
                    return ToolResult.error("Error: role.create requires role and role fields")
                return self._json(self._manager.role_create(role, role_values))
            if action == "role.update":
                if not role or not role_values:
                    return ToolResult.error("Error: role.update requires role and role fields")
                return self._json(self._manager.role_update(role, role_values))
            if action == "role.delete":
                if not role:
                    return ToolResult.error("Error: role.delete requires role")
                return self._json(self._manager.role_delete(role))
            if action == "role.reset":
                if not role:
                    return ToolResult.error("Error: role.reset requires role")
                return self._json(self._manager.role_reset(role))
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")

        return ToolResult.error(f"Error: unknown subagent action '{action}'")
