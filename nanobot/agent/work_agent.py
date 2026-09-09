"""Task-scoped WorkAgent launch helpers built on the existing subagent runtime."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.subagent import SubagentStatus
from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.registry import is_tool_error_result
from nanobot.llm_usage.context import current_llm_usage_source

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.subagent_roles import ResolvedSubagentRole
    from nanobot.security.workspace_access import WorkspaceScope
    from nanobot.utils.llm_runtime import LLMRuntime


_THINKING_VALUES = frozenset({
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "adaptive",
})


def _prepare_runtime(
    manager: "SubagentManager",
    runtime: "LLMRuntime",
    *,
    model: str | None,
    model_preset: str | None,
    thinking: str | None,
    temperature: float | None,
    timeout_seconds: float | None,
    context: str | None,
) -> tuple["LLMRuntime", str | None, float | None, float | None, str] | ToolResult:
    """Resolve task settings against General without persisting a WorkAgent role."""
    try:
        base = manager._resolve_role("general")
        if thinking is not None and thinking not in _THINKING_VALUES:
            raise ValueError(f"Unknown thinking value '{thinking}'")
        effective_thinking = thinking if thinking is not None else base.thinking
        effective_timeout = timeout_seconds if timeout_seconds is not None else base.timeout_seconds
        effective_context = context if context is not None else base.context
        if effective_context not in ("fresh", "fork"):
            raise ValueError("context must be 'fresh' or 'fork'")
        if effective_timeout is not None and effective_timeout <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        runtime = manager._resolve_task_runtime(
            runtime,
            role="general",
            model=model,
            model_preset=model_preset,
        )
    except ValueError as exc:
        return ToolResult.error(f"Error: {exc}")

    effective_temperature = temperature if temperature is not None else base.temperature
    if effective_temperature is not None and not 0 <= effective_temperature <= 2:
        return ToolResult.error("Error: temperature must be between 0 and 2")
    if effective_temperature is not None or effective_thinking is not None:
        runtime = runtime.with_generation_overrides(
            temperature=effective_temperature,
            reasoning_effort=effective_thinking,
        )
    return (
        runtime,
        effective_thinking,
        effective_temperature,
        effective_timeout,
        effective_context,
    )


def _status(
    *,
    task_id: str,
    task: str,
    label: str,
    runtime: "LLMRuntime",
    role_definition: "ResolvedSubagentRole",
    thinking: str | None,
    timeout_seconds: float | None,
    context: str,
    origin_channel: str,
    origin_chat_id: str,
    session_key: str | None,
    origin_message_id: str | None,
) -> SubagentStatus:
    return SubagentStatus(
        task_id=task_id,
        label=label,
        task_description=task,
        started_at=time.monotonic(),
        started_at_ms=int(time.time() * 1000),
        phase="queued",
        role="work",
        model=runtime.model,
        model_preset=runtime.model_preset,
        thinking=thinking,
        timeout_seconds=timeout_seconds,
        context=context,
        role_snapshot=role_definition,
        origin_channel=origin_channel,
        origin_chat_id=origin_chat_id,
        session_key=session_key,
        origin_message_id=origin_message_id,
    )


async def run_work_agent(
    manager: "SubagentManager",
    *,
    task: str,
    runtime: "LLMRuntime",
    role_definition: "ResolvedSubagentRole",
    wait: bool,
    label: str | None = None,
    model: str | None = None,
    model_preset: str | None = None,
    thinking: str | None = None,
    temperature: float | None = None,
    timeout_seconds: float | None = None,
    context: str | None = None,
    origin_channel: str = "cli",
    origin_chat_id: str = "direct",
    session_key: str | None = None,
    origin_message_id: str | None = None,
    workspace_scope: "WorkspaceScope | None" = None,
    allowed_tools: set[str] | frozenset[str] | None = None,
    fork_history: list[dict[str, Any]] | None = None,
) -> str:
    """Launch one ephemeral WorkAgent while reusing SubagentManager execution and tracking."""
    prepared = _prepare_runtime(
        manager,
        runtime,
        model=model,
        model_preset=model_preset,
        thinking=thinking,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        context=context,
    )
    if is_tool_error_result(prepared):
        return prepared
    runtime, effective_thinking, _effective_temperature, effective_timeout, effective_context = prepared

    task_id = str(uuid.uuid4())[:8]
    display_label = label or task[:30] + ("..." if len(task) > 30 else "")
    origin = {
        "channel": origin_channel,
        "chat_id": origin_chat_id,
        "session_key": session_key,
        "llm_usage_source": current_llm_usage_source(),
    }
    status = _status(
        task_id=task_id,
        task=task,
        label=display_label,
        runtime=runtime,
        role_definition=role_definition,
        thinking=effective_thinking,
        timeout_seconds=effective_timeout,
        context=effective_context,
        origin_channel=origin_channel,
        origin_chat_id=origin_chat_id,
        session_key=session_key,
        origin_message_id=origin_message_id,
    )
    manager._task_statuses[task_id] = status

    if wait:
        logger.info("Running inline WorkAgent [{}]: {}", task_id, display_label)
        inline_task = asyncio.create_task(
            manager._run_subagent(
                task_id,
                task,
                display_label,
                origin,
                status,
                runtime,
                origin_message_id,
                workspace_scope,
                announce=False,
                allowed_tools=allowed_tools,
                fork_history=fork_history,
                role_definition=role_definition,
            )
        )
        manager._running_tasks[task_id] = inline_task
        if session_key:
            manager._session_tasks.setdefault(session_key, set()).add(task_id)
        try:
            result = await inline_task
            if status.phase == "error" or status.stop_reason == "error":
                return ToolResult.error(result)
            return result
        finally:
            manager._running_tasks.pop(task_id, None)
            manager._steer_queues.pop(task_id, None)
            manager._record_finished(manager._task_statuses.pop(task_id, None))
            if session_key and (ids := manager._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del manager._session_tasks[session_key]

    manager._launch_guidance[task_id] = []
    bg_task = asyncio.create_task(
        manager._run_subagent(
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
            role_definition=role_definition,
        )
    )
    manager._running_tasks[task_id] = bg_task
    if session_key:
        manager._session_tasks.setdefault(session_key, set()).add(task_id)

    def _cleanup(_: asyncio.Task[str]) -> None:
        manager._running_tasks.pop(task_id, None)
        manager._steer_queues.pop(task_id, None)
        manager._launch_guidance.pop(task_id, None)
        manager._admitted_tasks.discard(task_id)
        manager._record_finished(manager._task_statuses.pop(task_id, None))
        if session_key and (ids := manager._session_tasks.get(session_key)):
            ids.discard(task_id)
            if not ids:
                del manager._session_tasks[session_key]

    bg_task.add_done_callback(_cleanup)
    await asyncio.sleep(0)
    logger.info("Spawned WorkAgent [{}]: {}", task_id, display_label)
    if status.state == "queued":
        return (
            f"WorkAgent [{display_label}] queued (id: {task_id}). "
            "I'll notify you when it completes."
        )
    return f"WorkAgent [{display_label}] started (id: {task_id}). I'll notify you when it completes."
