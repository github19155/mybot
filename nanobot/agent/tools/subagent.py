"""Unified tool for running and controlling in-process subagents."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

from nanobot.agent.permissions import PermissionManager
from nanobot.agent.subagent_role_storage import record_role_use
from nanobot.agent.subagent_roles import (
    ALL_SUBAGENT_TOOL_NAMES,
    TOOL_MODULES,
    is_subagent_tool_name,
)
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext, current_request_context
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.registry import is_tool_error_result
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    NumberSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.agent.tools.subagent_browser import bind_subagent_browser_bus
from nanobot.agent.work_agent import (
    build_work_role_definition,
    has_work_override,
    run_work_agent,
)
from nanobot.security.workspace_access import current_workspace_scope

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


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
        if clean.get("role") == "tool":
            clean.pop("tool_call_id", None)
        snapshot.append(clean)
    return snapshot


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
    model_id=StringSchema("Canonical model ID for a per-run override or role mutation", nullable=True),
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
    values={"type": "object", "additionalProperties": True},
    tools=ArraySchema(
        StringSchema("Allowed child tool name"),
        description="Task-scoped WorkAgent tools for run, or requested tools for a role",
        nullable=True,
    ),
    required=["action"],
    additional_properties=False,
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
        self._capability_loader = ToolLoader()
        workspace = getattr(manager, "workspace", None)
        bus = getattr(manager, "bus", None)
        if workspace is not None and bus is not None:
            bind_subagent_browser_bus(workspace, bus)

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        manager = ctx.subagent_manager
        if manager is None:
            raise RuntimeError("SubagentTool requires an initialized subagent manager")
        if ctx.registry is not None:
            manager.system_tools = ctx.registry
        return cls(manager)

    @property
    def name(self) -> str:
        return "subagent"

    @property
    def description(self) -> str:
        return (
            "Run and control child agents. Prefer a matching active persistent specialist when one "
            "clearly fits. Omit role with no per-task overrides to use the permanent general worker. "
            "Omit role and provide any per-task override (description, system_prompt, tools, model_id, "
            "thinking, temperature, timeout_seconds, or context) to create a temporary WorkAgent "
            "snapshot that is destroyed after the task and never persisted. When role is provided, "
            "model_id is a per-run runtime override and may temporarily replace that role's configured "
            "model_id without changing persistent role configuration. Do not combine WorkAgent "
            "identity/tool overrides (description, system_prompt, tools) with a persistent role. Use "
            "role.list or role.get to inspect declared, permission-allowed, and currently available "
            "worker capabilities. Connected MCP tools are reused by workers; discovery never connects "
            "an MCP server. Every run is asynchronous: successful dispatch returns immediately with "
            "a task identifier. Background results are delivered automatically, so do not poll or "
            "sleep-and-check. Browser automation is a worker capability, not a separate Agent type. "
            "Children cannot create children."
        )

    @property
    def concurrency_safe(self) -> bool:
        return True

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def _dynamic_mcp_catalog(self) -> set[str]:
        registry = getattr(self._manager, "system_tools", None)
        if registry is None:
            return set()
        return {name for name in registry.tool_names if name.startswith("mcp_")}

    def _available_worker_tools_without_activation(self) -> set[str]:
        """Return loadable worker tools plus already-connected MCP tools without activation."""
        config_builder = getattr(self._manager, "_subagent_tools_config", None)
        workspace = getattr(self._manager, "workspace", None)
        if not callable(config_builder) or workspace is None:
            return self._dynamic_mcp_catalog()

        config = config_builder()
        config_snapshot_loader = getattr(self._manager, "_role_config", None)
        config_snapshot = (
            config_snapshot_loader() if callable(config_snapshot_loader) else None
        )
        models = config_snapshot.models if config_snapshot is not None else {}
        resolver = getattr(self._manager, "runtime_resolver", None)
        provider_configs_loader = getattr(
            self._manager,
            "_image_generation_provider_configs",
            None,
        )
        try:
            image_provider_configs = (
                provider_configs_loader() if callable(provider_configs_loader) else None
            )
        except Exception:
            image_provider_configs = None

        ctx = ToolContext(
            config=config,
            workspace=str(workspace),
            bus=getattr(self._manager, "bus", None),
            subagent_manager=self._manager,
            exec_session_manager=getattr(self._manager, "_exec_session_manager", None),
            models=models,
            provider_snapshot_loader=(
                getattr(resolver, "_provider_snapshot_loader", None)
                if resolver is not None
                else None
            ),
            image_generation_provider_configs=image_provider_configs,
        )
        catalog_modules = set(TOOL_MODULES.values())
        enabled_modules: set[str] = set()
        for tool_cls in self._capability_loader.discover():
            if "subagent" not in getattr(tool_cls, "_scopes", {"core"}):
                continue
            module = tool_cls.__module__.rsplit(".", 1)[-1]
            if module not in catalog_modules:
                continue
            try:
                if tool_cls.enabled(ctx):
                    enabled_modules.add(module)
            except Exception:
                continue

        available = {
            name
            for name, module in TOOL_MODULES.items()
            if module in enabled_modules
        }
        if "image_analyze" in available and ctx.provider_snapshot_loader is None:
            available.discard("image_analyze")
        if "generate_image" in available:
            model_id = config.image_generation.model_id
            image_model = models.get(model_id) if model_id is not None else None
            if (
                image_model is None
                or not image_model.capabilities.image_generation
                or not image_provider_configs
                or image_model.provider not in image_provider_configs
            ):
                available.discard("generate_image")
        available.update(self._dynamic_mcp_catalog())
        return available

    def _role_view(
        self,
        role_data: dict[str, Any],
        request_allowed_tools: set[str],
        available_catalog: set[str],
    ) -> dict[str, Any]:
        """Separate declaration, permission and current no-side-effect availability."""
        view = dict(role_data)
        declared = list(dict.fromkeys(str(name) for name in view.pop("tools", ()) if name))
        role_name = str(view.get("name") or "").strip().lower()
        dynamic_mcp = self._dynamic_mcp_catalog()
        if role_name == "general":
            declared = list(dict.fromkeys((*declared, *sorted(dynamic_mcp))))
        subject = PermissionManager.specialist_subject(role_name)
        allowed = [
            name
            for name in declared
            if is_subagent_tool_name(name)
            and name in request_allowed_tools
            and self._manager.permissions.tool_allowed(subject, name)
        ]
        available = [name for name in allowed if name in available_catalog]
        view["declared_tools"] = declared
        view["allowed_tools"] = allowed
        view["available_tools"] = available
        view["worker_tool_catalog"] = sorted({*ALL_SUBAGENT_TOOL_NAMES, *dynamic_mcp})
        return view

    async def execute(
        self,
        action: str,
        task: str | None = None,
        label: str | None = None,
        task_id: str | None = None,
        message: str | None = None,
        role: str | None = None,
        model_id: str | None = None,
        thinking: str | None = None,
        temperature: float | None = None,
        timeout_seconds: float | None = None,
        context: str | None = None,
        values: dict[str, Any] | None = None,
        tools: list[str] | None = None,
        description: str | None = None,
        system_prompt: str | None = None,
        disabled: bool | None = None,
    ) -> str:
        request = current_request_context()
        if request is None:
            return ToolResult.error("Error: subagent requires an active request context")
        session_key = request.session_key or f"{request.channel}:{request.chat_id}"
        request_allowed_tools = set(request.allowed_tools)

        if action == "run":
            if not task or not task.strip():
                return ToolResult.error("Error: run requires a non-empty task")
            runtime = request.runtime
            if runtime is None:
                return ToolResult.error("Error: subagent run requires an active model runtime")

            identity_override = any(
                value is not None for value in (description, system_prompt, tools)
            )
            if role is not None and identity_override:
                return ToolResult.error(
                    "Error: description, system_prompt, and tools are task-scoped WorkAgent "
                    "overrides and cannot be combined with role"
                )

            fork_history = _fork_snapshot(request.conversation_history)
            work_override = role is None and has_work_override(
                description=description,
                system_prompt=system_prompt,
                tools=tools,
                model_id=model_id,
                thinking=thinking,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
                context=context,
            )
            launch = dict(
                task=task.strip(),
                runtime=runtime,
                label=label,
                model_id=model_id,
                thinking=thinking,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
                context=context,
                origin_channel=request.channel,
                origin_chat_id=request.chat_id,
                session_key=session_key,
                origin_message_id=request.message_id,
                workspace_scope=current_workspace_scope(),
                allowed_tools=request_allowed_tools,
                fork_history=fork_history,
            )

            if work_override:
                try:
                    role_definition = build_work_role_definition(
                        self._manager.role_get("general"),
                        description=description,
                        system_prompt=system_prompt,
                        tools=tools,
                    )
                except ValueError as exc:
                    return ToolResult.error(f"Error: {exc}")
                return await run_work_agent(
                    self._manager,
                    role_definition=role_definition,
                    **launch,
                )

            selected_role = role or "general"
            result = await self._manager.spawn(role=selected_role, **launch)
            if not is_tool_error_result(result):
                workspace = getattr(self._manager, "workspace", None)
                if workspace is not None:
                    try:
                        record_role_use(workspace, selected_role)
                    except OSError:
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
                "model_id": model_id,
                "thinking": thinking,
                "temperature": temperature,
                "timeout_seconds": timeout_seconds,
                "context": context,
                "tools": tools,
                "disabled": disabled,
            }.items():
                if value is not None:
                    role_values[key] = value
            available_catalog = self._available_worker_tools_without_activation()
            if action == "role.list":
                return self._json([
                    self._role_view(item, request_allowed_tools, available_catalog)
                    for item in self._manager.role_list()
                ])
            if action == "role.get":
                if not role:
                    return ToolResult.error("Error: role.get requires role")
                return self._json(self._role_view(
                    self._manager.role_get(role),
                    request_allowed_tools,
                    available_catalog,
                ))
            if action == "role.create":
                if not role or not role_values:
                    return ToolResult.error("Error: role.create requires role and role fields")
                return self._json(self._role_view(
                    self._manager.role_create(role, role_values),
                    request_allowed_tools,
                    available_catalog,
                ))
            if action == "role.update":
                if not role or not role_values:
                    return ToolResult.error("Error: role.update requires role and role fields")
                return self._json(self._role_view(
                    self._manager.role_update(role, role_values),
                    request_allowed_tools,
                    available_catalog,
                ))
            if action == "role.delete":
                if not role:
                    return ToolResult.error("Error: role.delete requires role")
                return self._json(self._manager.role_delete(role))
            if action == "role.reset":
                if not role:
                    return ToolResult.error("Error: role.reset requires role")
                return self._json(self._role_view(
                    self._manager.role_reset(role),
                    request_allowed_tools,
                    available_catalog,
                ))
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")

        return ToolResult.error(f"Error: unknown subagent action '{action}'")
