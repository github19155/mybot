"""Subagent manager for background task execution."""

import asyncio
import copy
import json
import time
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, NotRequired, TypedDict

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.permissions import WORK_SUBJECT, PermissionManager
from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.agent.subagent_roles import (
    TOOL_MODULES,
    ResolvedSubagentRole,
    SubagentRoleStore,
    list_roles,
    resolve_role,
)
from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.context import (
    RequestContext,
    ToolContext,
    bind_request_context,
    reset_request_context,
)
from nanobot.agent.tools.exec_session import ExecSessionManager
from nanobot.agent.tools.file_state import FileStates
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AgentDefaults, ToolsConfig
from nanobot.llm_usage.context import LLMUsageSource, current_llm_usage_source
from nanobot.providers.base import LLMUsage
from nanobot.security.workspace_access import (
    WorkspaceScope,
    bind_workspace_scope,
    reset_workspace_scope,
    workspace_sandbox_status,
)
from nanobot.utils.llm_runtime import LLMRuntime
from nanobot.utils.prompt_templates import render_template

_THINKING_VALUES = frozenset({
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "adaptive",
})
_FORK_PRIVATE_MESSAGE_KEYS = frozenset({
    "reasoning", "reasoning_content", "reasoning_details", "reasoning_content_blocks",
    "thinking_blocks", "thought_signature", "provider_state", "_provider_state",
    "_reasoning", "_reasoning_delta",
})


def _portable_fork_history(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Copy parent history while removing provider-private state."""
    snapshot: list[dict[str, Any]] = []
    for message in history or []:
        clean = {
            key: copy.deepcopy(value)
            for key, value in message.items()
            if key not in _FORK_PRIVATE_MESSAGE_KEYS
        }
        if clean.get("role") == "tool":
            clean.pop("tool_call_id", None)
        snapshot.append(clean)
    return snapshot


if TYPE_CHECKING:
    from nanobot.agent.model_management import ModelManagement
    from nanobot.agent.model_runtime import ModelRuntimeResolver


class _SubagentOrigin(TypedDict):
    channel: str
    chat_id: str
    session_key: str | None
    llm_usage_source: NotRequired[LLMUsageSource]


@dataclass(slots=True)
class SubagentStatus:
    """Real-time status of a running subagent."""

    task_id: str
    label: str
    task_description: str
    started_at: float
    phase: str = "initializing"
    iteration: int = 0
    tool_events: list[dict[str, str]] = field(default_factory=list)
    usage: LLMUsage | None = None
    stop_reason: str | None = None
    error: str | None = None
    role: str = "coder"
    model: str | None = None
    model_preset: str | None = None
    origin_channel: str | None = None
    origin_chat_id: str | None = None
    session_key: str | None = None
    origin_message_id: str | None = None
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    state: str = "queued"
    thinking: str | None = None
    context: str = "fresh"
    timeout_seconds: float | None = None
    completion_delivered: bool = False
    completion_delivery_error: str | None = None
    final_output: str | None = None
    role_snapshot: ResolvedSubagentRole | None = field(default=None, repr=False)


class _SubagentHook(AgentHook):
    """Hook for subagent execution — logs tool calls and updates status."""

    def __init__(self, task_id: str, status: SubagentStatus | None = None) -> None:
        super().__init__()
        self._task_id = task_id
        self._status = status

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._status is None:
            return
        self._status.iteration = context.iteration
        self._status.tool_events = list(context.tool_events)
        self._status.usage = context.usage
        if context.error:
            self._status.error = str(context.error)


class SubagentManager:
    """Manages background subagent execution."""

    def __init__(
        self,
        *,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int,
        permission_manager: PermissionManager,
        tools_config: ToolsConfig | None = None,
        restrict_to_workspace: bool = False,
        disabled_skills: list[str] | None = None,
        max_iterations: int | None = None,
        max_concurrent_subagents: int | None = None,
        llm_wall_timeout_for_session: Callable[[str | None], float | None] | None = None,
        model_management: "ModelManagement | None" = None,
        runtime_resolver: "ModelRuntimeResolver | None" = None,
    ):
        defaults = AgentDefaults()
        self.workspace = workspace
        self.bus = bus
        self.tools_config = tools_config or ToolsConfig()
        self.max_tool_result_chars = max_tool_result_chars
        self.restrict_to_workspace = restrict_to_workspace
        self.disabled_skills = set(disabled_skills or [])
        self.max_iterations = (
            max_iterations
            if max_iterations is not None
            else defaults.max_tool_iterations
        )
        self.max_concurrent_subagents = (
            max_concurrent_subagents
            if max_concurrent_subagents is not None
            else defaults.max_concurrent_subagents
        )
        self._admission_condition = asyncio.Condition()
        self._admission_queue: deque[str] = deque()
        self._admitted_tasks: set[str] = set()
        self.runner = AgentRunner()
        self._exec_session_manager = ExecSessionManager()
        self._llm_wall_timeout_for_session = llm_wall_timeout_for_session
        self.model_management = model_management
        self.runtime_resolver = runtime_resolver
        self.permissions = permission_manager
        # Bound by the Main-facing SubagentTool when the canonical system
        # registry is loaded. MCPProvider remains the sole connection owner;
        # workers only borrow already-connected wrapper objects from this view.
        self.system_tools: ToolRegistry | None = None
        self._running_tasks: dict[str, asyncio.Task[str]] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._session_tasks: dict[str, set[str]] = {}
        self._finished: deque[SubagentStatus] = deque(maxlen=50)
        self._steer_queues: dict[str, asyncio.Queue[str]] = {}
        self._launch_guidance: dict[str, list[str]] = {}
        self._silent_cancel_tasks: set[str] = set()
        self.max_concurrent_per_session = 8

    def _role_config(self):
        return self.model_management.config_snapshot() if self.model_management is not None else None

    def _resolve_role(self, role: str):
        return resolve_role(self._role_config(), role)

    def _session_admitted_count(self, session_key: str | None) -> int:
        return sum(
            1 for task_id in self._admitted_tasks
            if (status := self._task_statuses.get(task_id)) is not None
            and status.session_key == session_key
        )

    def _select_admission_candidate(self) -> str | None:
        if len(self._admitted_tasks) >= self.max_concurrent_subagents:
            return None
        for task_id in list(self._admission_queue):
            status = self._task_statuses.get(task_id)
            task = self._running_tasks.get(task_id)
            if status is None or (task is not None and task.done()):
                try:
                    self._admission_queue.remove(task_id)
                except ValueError:
                    pass
                continue
            if self._session_admitted_count(status.session_key) < self.max_concurrent_per_session:
                return task_id
        return None

    async def _admit(self, task_id: str) -> None:
        """Wait for a global and per-session slot using fair queued admission."""
        async with self._admission_condition:
            if task_id not in self._admission_queue:
                self._admission_queue.append(task_id)
            while True:
                candidate = self._select_admission_candidate()
                if candidate == task_id:
                    self._admission_queue.remove(task_id)
                    self._admitted_tasks.add(task_id)
                    status = self._task_statuses.get(task_id)
                    if status is not None:
                        status.state = "running"
                        status.phase = "initializing"
                    return
                try:
                    await self._admission_condition.wait()
                except asyncio.CancelledError:
                    try:
                        self._admission_queue.remove(task_id)
                    except ValueError:
                        pass
                    self._admission_condition.notify_all()
                    raise

    async def _release_admission(self, task_id: str) -> None:
        async with self._admission_condition:
            self._admitted_tasks.discard(task_id)
            try:
                self._admission_queue.remove(task_id)
            except ValueError:
                pass
            self._admission_condition.notify_all()

    def runtime_statuses(self) -> Mapping[str, SubagentStatus]:
        """Return the observable task statuses used by runtime-control snapshots."""
        return self._task_statuses

    def _resolve_task_runtime(
        self,
        runtime: LLMRuntime,
        *,
        role_definition: ResolvedSubagentRole,
        model: str | None,
        model_preset: str | None,
    ) -> LLMRuntime:
        if role_definition.disabled:
            raise ValueError(f"Subagent role '{role_definition.name}' is disabled")
        selected_model = model
        selected_preset = model_preset
        if selected_model is None and selected_preset is None:
            selected_model = role_definition.model
            selected_preset = role_definition.model_preset
        if selected_model is None and selected_preset is None:
            return runtime
        if self.runtime_resolver is None:
            raise ValueError("Subagent model selection requires ModelRuntimeResolver")
        return self.runtime_resolver.resolve_selection(
            runtime,
            model=selected_model,
            model_preset=selected_preset,
        )

    def _subagent_tools_config(self) -> ToolsConfig:
        """Build an isolated ToolsConfig while preserving configured worker capabilities."""
        config = self.tools_config.model_copy(deep=True)
        config.restrict_to_workspace = self.restrict_to_workspace
        return config

    def _image_generation_provider_configs(self):
        config = self._role_config()
        if config is None:
            return None
        from nanobot.providers.image_generation import image_gen_provider_configs

        return image_gen_provider_configs(config)

    def _dynamic_mcp_tool_names(self) -> set[str]:
        if self.system_tools is None:
            return set()
        return {name for name in self.system_tools.tool_names if name.startswith("mcp_")}

    def _build_tools(
        self,
        workspace: Path | None = None,
        tools_config: ToolsConfig | None = None,
        *,
        role: str = "coder",
        allowed_tools: set[str] | frozenset[str] | None = None,
        role_definition: ResolvedSubagentRole | None = None,
    ) -> ToolRegistry:
        """Build an isolated worker registry and borrow eligible live MCP wrappers."""
        root = self.workspace if workspace is None else workspace
        role_definition = role_definition or self._resolve_role(role)
        subject = (
            WORK_SUBJECT
            if role_definition.source == "ephemeral"
            else PermissionManager.specialist_subject(role_definition.name)
        )
        registry = ToolRegistry(
            permission_manager=self.permissions,
            permission_subject=subject,
        )
        cfg = tools_config if tools_config is not None else self._subagent_tools_config()
        resolver = self.runtime_resolver
        ctx = ToolContext(
            config=cfg,
            workspace=str(root.resolve()),
            exec_session_manager=self._exec_session_manager,
            file_state_store=FileStates(),
            provider_snapshot_loader=(
                getattr(resolver, "_provider_snapshot_loader", None)
                if resolver is not None
                else None
            ),
            image_generation_provider_configs=self._image_generation_provider_configs(),
            workspace_sandbox=workspace_sandbox_status(
                restrict_to_workspace=cfg.restrict_to_workspace,
                workspace=root,
            ),
        )
        ToolLoader().load(ctx, registry, scope="subagent")

        dynamic_mcp = self._dynamic_mcp_tool_names()
        allowed_names = set(role_definition.tools)
        if role_definition.category == "general":
            allowed_names.update(dynamic_mcp)
        if allowed_tools is not None:
            allowed_names.intersection_update(allowed_tools)
        allowed_names.discard("subagent")

        # MCPProvider owns the live wrappers and their connection/reconnect
        # callbacks. Register the same objects into this task-local registry;
        # permission remains bound to the worker subject, not Main.
        if self.system_tools is not None:
            for name in sorted(dynamic_mcp & allowed_names):
                if not self.permissions.tool_allowed(subject, name):
                    continue
                tool = self.system_tools.get(name)
                if tool is not None:
                    registry.register(tool)

        for name in list(registry.tool_names):
            tool = registry.get(name)
            if name not in allowed_names or (
                name in TOOL_MODULES
                and type(tool).__module__ != f"nanobot.agent.tools.{TOOL_MODULES[name]}"
            ):
                registry.unregister(name)
        return registry

    async def spawn_ephemeral(
        self,
        *,
        role_definition: ResolvedSubagentRole,
        **launch: Any,
    ) -> str:
        """Spawn one non-persistent worker through the normal manager lifecycle."""
        if role_definition.source != "ephemeral":
            raise ValueError("spawn_ephemeral requires an ephemeral role definition")
        return await self.spawn(
            role=role_definition.name,
            role_definition=role_definition,
            **launch,
        )

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        origin_message_id: str | None = None,
        temperature: float | None = None,
        workspace_scope: WorkspaceScope | None = None,
        *,
        runtime: LLMRuntime,
        role: str = "coder",
        model: str | None = None,
        model_preset: str | None = None,
        thinking: str | None = None,
        timeout_seconds: float | None = None,
        context: str | None = None,
        allowed_tools: set[str] | frozenset[str] | None = None,
        fork_history: list[dict[str, Any]] | None = None,
        role_definition: ResolvedSubagentRole | None = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background."""
        fork_history = _portable_fork_history(fork_history)
        try:
            role_config = role_definition or self._resolve_role(role)
            if thinking is not None and thinking not in _THINKING_VALUES:
                raise ValueError(f"Unknown thinking value '{thinking}'")
            effective_thinking = thinking if thinking is not None else role_config.thinking
            effective_timeout = (
                timeout_seconds if timeout_seconds is not None else role_config.timeout_seconds
            )
            effective_context = context if context is not None else role_config.context
            if effective_context not in ("fresh", "fork"):
                raise ValueError("context must be 'fresh' or 'fork'")
            if effective_timeout is not None and effective_timeout <= 0:
                raise ValueError("timeout_seconds must be greater than zero")
            runtime = self._resolve_task_runtime(
                runtime,
                model=model,
                model_preset=model_preset,
                role_definition=role_config,
            )
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")
        effective_temperature = (
            temperature if temperature is not None else role_config.temperature
        )
        if effective_temperature is not None and not 0 <= effective_temperature <= 2:
            return ToolResult.error("Error: temperature must be between 0 and 2")
        if effective_temperature is not None or effective_thinking is not None:
            runtime = runtime.with_generation_overrides(
                temperature=effective_temperature,
                reasoning_effort=effective_thinking,
            )
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin: _SubagentOrigin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
            "session_key": session_key,
            "llm_usage_source": current_llm_usage_source(),
        }

        status = SubagentStatus(
            task_id=task_id,
            label=display_label,
            task_description=task,
            started_at=time.monotonic(),
            started_at_ms=int(time.time() * 1000),
            phase="queued",
            role=role,
            model=runtime.model,
            model_preset=runtime.model_preset,
            thinking=effective_thinking,
            timeout_seconds=effective_timeout,
            context=effective_context,
            role_snapshot=role_config,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            session_key=session_key,
            origin_message_id=origin_message_id,
        )
        self._task_statuses[task_id] = status
        self._launch_guidance[task_id] = []

        bg_task = asyncio.create_task(
            self._run_subagent(
                task_id,
                task,
                display_label,
                origin,
                status,
                runtime,
                origin_message_id,
                workspace_scope,
                allowed_tools=allowed_tools,
                fork_history=fork_history,
                role_definition=role_config,
            )
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task[str]) -> None:
            self._running_tasks.pop(task_id, None)
            self._steer_queues.pop(task_id, None)
            self._launch_guidance.pop(task_id, None)
            self._admitted_tasks.discard(task_id)
            self._record_finished(self._task_statuses.pop(task_id, None))
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)
        await asyncio.sleep(0)
        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        response_state = status.state
        return (
            f"Subagent [{display_label}] queued (id: {task_id}). "
            "I'll notify you when it completes."
            if response_state == "queued"
            else f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."
        )

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: _SubagentOrigin,
        status: SubagentStatus,
        runtime: LLMRuntime,
        origin_message_id: str | None = None,
        workspace_scope: WorkspaceScope | None = None,
        *,
        announce: bool = True,
        allowed_tools: set[str] | frozenset[str] | None = None,
        fork_history: list[dict[str, Any]] | None = None,
        role_definition: ResolvedSubagentRole | None = None,
    ) -> str:
        """Wait for capacity, then execute one subagent task."""
        status.phase = "queued"
        temporary_registration = task_id not in self._task_statuses
        if temporary_registration:
            status.session_key = origin.get("session_key")
            self._task_statuses[task_id] = status
        admitted = False
        try:
            await self._admit(task_id)
            admitted = True
            guidance = self._launch_guidance.get(task_id, [])
            if guidance:
                task = f"{task}\n\nAdditional guidance from the parent:\n" + "\n".join(
                    f"- {item}" for item in guidance
                )
            return await self._run_admitted_subagent(
                task_id,
                task,
                label,
                origin,
                status,
                runtime,
                origin_message_id,
                workspace_scope,
                announce=announce,
                allowed_tools=allowed_tools,
                fork_history=fork_history,
                role_definition=role_definition or status.role_snapshot,
            )
        except asyncio.CancelledError:
            if status.state not in ("completed", "failed"):
                status.state = "stopped"
                status.phase = "stopped"
                status.stop_reason = "cancelled"
                status.final_output = None
                try:
                    await self._exec_session_manager.terminate_by_owner(
                        self._exec_owner_key(task_id, origin)
                    )
                except Exception as exc:
                    logger.warning("Subagent [{}] exec cleanup failed: {}", task_id, exc)
                if announce and task_id not in self._silent_cancel_tasks:
                    await self._deliver_result(
                        task_id,
                        label,
                        task,
                        "Task stopped before producing a successful result.",
                        origin,
                        "stopped",
                        origin_message_id,
                    )
            raise
        finally:
            if admitted:
                await self._release_admission(task_id)
            if temporary_registration:
                self._record_finished(self._task_statuses.pop(task_id, None))

    async def _run_admitted_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: _SubagentOrigin,
        status: SubagentStatus,
        runtime: LLMRuntime,
        origin_message_id: str | None = None,
        workspace_scope: WorkspaceScope | None = None,
        *,
        announce: bool = True,
        allowed_tools: set[str] | frozenset[str] | None = None,
        fork_history: list[dict[str, Any]] | None = None,
        role_definition: ResolvedSubagentRole | None = None,
    ) -> str:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)

        async def _on_checkpoint(payload: dict[str, Any]) -> None:
            status.phase = payload.get("phase", status.phase)
            status.iteration = payload.get("iteration", status.iteration)

        try:
            root = workspace_scope.project_path if workspace_scope is not None else self.workspace
            cfg = None
            if workspace_scope is not None:
                cfg = self._subagent_tools_config()
                cfg.restrict_to_workspace = workspace_scope.restrict_to_workspace
            tools = self._build_tools(
                workspace=root,
                tools_config=cfg,
                role=status.role,
                allowed_tools=allowed_tools,
                role_definition=role_definition or status.role_snapshot,
            )
            system_prompt = self._build_subagent_prompt(
                workspace=root,
                role=status.role,
                role_definition=role_definition or status.role_snapshot,
            )
            if runtime.system_prompt_prefix:
                system_prompt = f"{runtime.system_prompt_prefix}\n\n{system_prompt}"
            messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
            portable_history = _portable_fork_history(fork_history)
            if status.context == "fork" and portable_history:
                messages.extend(portable_history)
            messages.append({"role": "user", "content": task})

            sess_key = origin.get("session_key")
            llm_timeout = (
                status.timeout_seconds
                or (
                    self._llm_wall_timeout_for_session(sess_key)
                    if self._llm_wall_timeout_for_session
                    else None
                )
            )
            request_token = bind_request_context(RequestContext(
                channel=origin["channel"],
                chat_id=origin["chat_id"],
                message_id=origin_message_id,
                session_key=sess_key,
                runtime=runtime,
                allowed_tools=frozenset(tools.tool_names),
                conversation_history=tuple(portable_history),
                exec_owner_session_key=self._exec_owner_key(task_id, origin),
            ))
            token = bind_workspace_scope(workspace_scope) if workspace_scope is not None else None
            try:
                result = await self.runner.run(AgentRunSpec(
                    initial_messages=messages,
                    tools=tools,
                    runtime=runtime,
                    max_iterations=self.max_iterations,
                    max_tool_result_chars=self.max_tool_result_chars,
                    hook=_SubagentHook(task_id, status),
                    max_iterations_message="Task completed but no final response was generated.",
                    finalize_on_max_iterations=False,
                    error_message=None,
                    checkpoint_callback=_on_checkpoint,
                    session_key=sess_key,
                    workspace=root,
                    llm_timeout_s=llm_timeout,
                    llm_usage_source=origin.get(
                        "llm_usage_source",
                        current_llm_usage_source(),
                    ),
                    injection_callback=partial(self._drain_steer_queue, task_id),
                    terminal_injection_callback=partial(self._drain_steer_queue, task_id),
                ))
            finally:
                if token is not None:
                    reset_workspace_scope(token)
                reset_request_context(request_token)
            status.phase = "done"
            status.stop_reason = result.stop_reason

            if result.stop_reason == "error":
                final_result = result.error or "Error: subagent execution failed."
                final_status = "error"
                status.state = "failed"
            else:
                final_result = result.final_content or "Task completed but no final response was generated."
                final_status = "ok"
                status.state = "completed"
                logger.info("Subagent [{}] completed successfully", task_id)
            status.final_output = final_result[:16_000]
            if announce:
                await self._deliver_result(
                    task_id,
                    label,
                    task,
                    final_result,
                    origin,
                    final_status,
                    origin_message_id,
                )
            return final_result

        except Exception as e:
            status.phase = "error"
            status.state = "failed"
            status.error = str(e)
            logger.exception("Subagent [{}] failed", task_id)
            final_result = f"Error: {e}"
            status.final_output = final_result[:16_000]
            if announce:
                await self._deliver_result(
                    task_id,
                    label,
                    task,
                    final_result,
                    origin,
                    "error",
                    origin_message_id,
                )
            return final_result

    async def _deliver_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: _SubagentOrigin,
        status: str,
        origin_message_id: str | None = None,
    ) -> None:
        """Deliver one terminal notification without changing child outcome."""
        child_status = self._task_statuses.get(task_id)
        if child_status is not None and child_status.completion_delivered:
            return
        try:
            await self._announce_result(
                task_id,
                label,
                task,
                result,
                origin,
                status,
                origin_message_id,
            )
            if child_status is not None:
                child_status.completion_delivered = True
        except Exception as exc:
            if child_status is not None:
                child_status.completion_delivery_error = str(exc)
            logger.warning("Subagent [{}] completion delivery failed: {}", task_id, exc)

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: _SubagentOrigin,
        status: str,
        origin_message_id: str | None = None,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = {
            "ok": "completed successfully",
            "stopped": "stopped",
        }.get(status, "failed")

        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            task=task,
            result=result,
        )

        override = origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}"
        metadata: dict[str, Any] = {
            "injected_event": "subagent_result",
            "subagent_task_id": task_id,
        }
        if origin_message_id:
            metadata["origin_message_id"] = origin_message_id
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
            session_key_override=override,
            metadata=metadata,
        )

        await self.bus.publish_inbound(msg)
        logger.debug(
            "Subagent [{}] announced result to {}:{}",
            task_id,
            origin["channel"],
            origin["chat_id"],
        )

    def _build_subagent_prompt(
        self,
        workspace: Path | None = None,
        *,
        role: str = "coder",
        role_definition: ResolvedSubagentRole | None = None,
    ) -> str:
        """Build a focused system prompt for the subagent."""
        from nanobot.agent.skills import SkillsLoader

        role_definition = role_definition or self._resolve_role(role)
        agent_workspace = self.workspace.expanduser().resolve()
        project_workspace = workspace.expanduser().resolve() if workspace else agent_workspace
        skills_summary = SkillsLoader(
            self.workspace,
            disabled_skills=self.disabled_skills,
        ).build_skills_summary(workspace=project_workspace)
        history_log = (
            str(agent_workspace / "memory" / "history.jsonl")
            if agent_workspace != project_workspace
            else "memory/history.jsonl"
        )
        return render_template(
            "agent/subagent_system.md",
            role=role,
            role_description=role_definition.description,
            role_system_prompt=role_definition.system_prompt,
            workspace=str(project_workspace),
            agent_workspace=str(agent_workspace),
            history_log=history_log,
            skills_summary=skills_summary or "",
        )

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        task_ids = [
            tid for tid in self._session_tasks.get(session_key, [])
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        ]
        tasks = [self._running_tasks[tid] for tid in task_ids]
        self._silent_cancel_tasks.update(task_ids)
        try:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._exec_session_manager.terminate_by_owner(session_key)
            return len(tasks)
        finally:
            self._silent_cancel_tasks.difference_update(task_ids)

    async def close(self) -> None:
        """Cancel running subagents and close their shared exec sessions."""
        tasks = [task for task in self._running_tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._exec_session_manager.close_all()

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._admitted_tasks)

    def get_running_count_by_session(self, session_key: str) -> int:
        """Return the number of currently running subagents for a session."""
        return self._session_admitted_count(session_key)

    async def steer(self, task_id: str, message: str) -> bool:
        """Queue a steer message for a running subagent.

        Bounded per-task queue (maxsize 5); overflow drops the oldest message.
        Returns False when no live task with that id exists.
        """
        task = self._running_tasks.get(task_id)
        status = self._task_statuses.get(task_id)
        if (
            task is None
            or task.done()
            or status is None
            or status.state not in ("queued", "running")
        ):
            return False
        if status.state == "queued":
            guidance = self._launch_guidance.setdefault(task_id, [])
            if len(guidance) >= 5:
                guidance.pop(0)
            guidance.append(message)
            return True
        queue = self._steer_queues.get(task_id)
        if queue is None:
            new_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=5)
            self._steer_queues[task_id] = new_queue
            queue = new_queue
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(message)
        if task.done():
            self._steer_queues.pop(task_id, None)
            return False
        return True

    async def stop(self, task_id: str) -> bool:
        """Stop one queued or running task and release its admission slot."""
        task = self._running_tasks.get(task_id)
        status = self._task_statuses.get(task_id)
        if (
            task is None
            or task.done()
            or status is None
            or status.state not in ("queued", "running")
        ):
            return False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    @staticmethod
    def _exec_owner_key(task_id: str, origin: _SubagentOrigin) -> str:
        base = origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}"
        return f"{base}:subagent:{task_id}"

    def owns_task(self, task_id: str, session_key: str | None) -> bool:
        status = self._task_statuses.get(task_id)
        if status is None:
            status = next((item for item in self._finished if item.task_id == task_id), None)
        return status is not None and status.session_key == session_key

    def status_snapshot(
        self,
        session_key: str | None,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        now_ms = int(time.time() * 1000)
        statuses = [*self._task_statuses.values(), *self._finished]
        if task_id is not None:
            statuses = [status for status in statuses if status.task_id == task_id]
        statuses = [status for status in statuses if status.session_key == session_key]
        return [self._status_to_snapshot(status, now_ms) for status in statuses]

    def _role_store(self) -> SubagentRoleStore:
        if self.model_management is None:
            raise ValueError("Role mutations require configured model management")
        return SubagentRoleStore(self.model_management.config)

    def role_list(self) -> list[dict[str, Any]]:
        return self._role_store().list() if self.model_management else [
            role.as_dict() for role in list_roles(None)
        ]

    def role_get(self, name: str) -> dict[str, Any]:
        return (
            self._role_store().get(name)
            if self.model_management
            else resolve_role(None, name).as_dict()
        )

    def role_create(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        return self._role_store().create(name, values)

    def role_update(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        return self._role_store().update(name, values)

    def role_delete(self, name: str) -> dict[str, Any]:
        return self._role_store().delete(name)

    def role_reset(self, name: str) -> dict[str, Any]:
        return self._role_store().reset(name)

    async def _drain_steer_queue(self, task_id: str, limit: int = 3) -> list[str]:
        """Injection callback: drain queued steer messages for one task."""
        queue = self._steer_queues.get(task_id)
        if queue is None:
            return []
        items: list[str] = []
        while len(items) < limit and not queue.empty():
            items.append(queue.get_nowait())
        return items

    def _record_finished(self, status: SubagentStatus | None) -> None:
        """Retain a finished status in the bounded history (done/error only)."""
        if status is None or status.state not in ("completed", "failed", "stopped"):
            return
        status.ended_at_ms = int(time.time() * 1000)
        self._finished.append(status)

    def fleet_snapshot(self) -> dict[str, Any]:
        """Snapshot of the subagent fleet per the /api/subagents contract."""
        now_ms = int(time.time() * 1000)
        rows = [
            self._status_to_snapshot(s, now_ms)
            for s in (*self._task_statuses.values(), *self._finished)
        ]
        rows.sort(key=lambda row: row["started_at_ms"], reverse=True)
        return {
            "subagents": rows,
            "budget": {
                "max_per_session": self.max_concurrent_per_session,
                "max_global": self.max_concurrent_subagents,
                "running": len(self._admitted_tasks),
                "running_by_session": {
                    key: self.get_running_count_by_session(key)
                    for key in sorted(self._session_tasks)
                },
            },
        }

    @staticmethod
    def _status_to_snapshot(status: SubagentStatus, now_ms: int) -> dict[str, Any]:
        started_at_ms = status.started_at_ms
        if started_at_ms is None:
            started_at_ms = now_ms - int((time.monotonic() - status.started_at) * 1000)
        usage = status.usage
        return {
            "task_id": status.task_id,
            "label": status.label,
            "task": status.task_description,
            "state": status.state,
            "phase": status.phase,
            "iteration": status.iteration,
            "role": status.role,
            "model": status.model,
            "origin": {
                "channel": status.origin_channel,
                "chat_id": status.origin_chat_id,
                "session_key": status.session_key,
                "message_id": status.origin_message_id,
            },
            "started_at_ms": started_at_ms,
            "ended_at_ms": status.ended_at_ms,
            "error": status.error,
            "stop_reason": status.stop_reason,
            "thinking": status.thinking,
            "context": status.context,
            "output": status.final_output,
            "completion_delivered": status.completion_delivered,
            "completion_delivery_error": status.completion_delivery_error,
            "usage": {
                "input_tokens": usage.input_tokens if usage else 0,
                "output_tokens": usage.output_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
            "tool_events": [
                {"name": e.get("name", ""), "summary": e.get("detail", "")}
                for e in status.tool_events[-20:]
            ],
        }
