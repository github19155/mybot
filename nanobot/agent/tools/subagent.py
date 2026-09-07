"""Unified tool for running and controlling in-process subagents."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    NumberSchema,
    StringSchema,
    tool_parameters_schema,
)
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


_SUBAGENT_PARAMETERS = tool_parameters_schema(
    action=StringSchema("Operation to perform", enum=list(_ACTIONS)),
    task=StringSchema("Task brief for run", nullable=True),
    label=StringSchema("Optional short label", nullable=True),
    task_id=StringSchema("Task ID for status/steer/stop", nullable=True),
    message=StringSchema("Steering message", nullable=True),
    role=StringSchema("Role name", nullable=True),
    system_prompt=StringSchema("Role system prompt", nullable=True),
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
        description="Requested tools for a role",
        nullable=True,
    ),
    required=["action"],
    additional_properties=True,
)
_SUBAGENT_PARAMETERS["properties"]["description"] = StringSchema(
    "Role description", nullable=True,
).to_json_schema()


@tool_parameters(_SUBAGENT_PARAMETERS)
class SubagentTool(Tool):
    """Run, inspect, steer, stop, and configure subagents."""

    def __init__(self, manager: "SubagentManager") -> None:
        self._manager = manager

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
            "Run and control child agents. Use run with wait=false for independent work, "
            "then status/steer/stop by task ID. Use wait=true only when this turn needs the result. "
            "Role and model settings apply only to the child; children cannot create children."
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
            method = self._manager.run_inline if wait else self._manager.spawn
            return await method(
                task=task.strip(),
                runtime=runtime,
                label=label,
                role=role or "coder",
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
