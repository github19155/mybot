"""Regression coverage for task-scoped WorkAgent launches."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.permissions import PermissionManager
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.subagent_role_storage import role_usage
from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.agent.tools.subagent import SubagentTool
from nanobot.agent.work_agent import build_work_role_definition, run_work_agent
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.providers.base import GenerationSettings
from nanobot.utils.llm_runtime import LLMRuntime


def _runtime(*, prefix: str | None = None) -> LLMRuntime:
    provider = MagicMock()
    provider.generation = GenerationSettings()
    return LLMRuntime.capture(
        provider,
        "test/model",
        context_window_tokens=32_000,
        system_prompt_prefix=prefix,
    )


def _manager(tmp_path, **kwargs) -> SubagentManager:
    permissions = PermissionManager(Config())
    return SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        permission_manager=permissions,
        **kwargs,
    )


def _general_payload(manager: SubagentManager) -> dict:
    return manager.role_get("general")


def _request(runtime: LLMRuntime | None = None) -> RequestContext:
    return RequestContext(
        channel="test",
        chat_id="chat-1",
        session_key="test:chat-1",
        runtime=runtime or _runtime(),
        allowed_tools=frozenset({"subagent", "read_file", "exec"}),
    )


@pytest.mark.asyncio
async def test_subagent_tool_dispatches_non_persistent_work_agent(tmp_path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    launch = AsyncMock(return_value="WorkAgent queued (id: task-1).")
    monkeypatch.setattr("nanobot.agent.tools.subagent.run_work_agent", launch)
    tool = SubagentTool(manager)
    token = bind_request_context(_request())
    try:
        result = await tool.execute(
            action="run",
            task="inspect one failure",
            description="One-off failure inspector",
            system_prompt="Inspect the assigned failure and return evidence only.",
            tools=["read_file", "exec"],
        )
    finally:
        reset_request_context(token)
        await manager.close()

    assert "workagent" in result.lower()
    launch.assert_awaited_once()
    role_definition = launch.await_args.kwargs["role_definition"]
    assert role_definition.name == "work"
    assert role_definition.category == "work"
    assert role_definition.source == "ephemeral"
    assert role_definition.system_prompt == "Inspect the assigned failure and return evidence only."
    assert set(role_definition.tools) == {"read_file", "exec", "report_progress"}
    assert role_usage(tmp_path, "general") == {}
    assert role_usage(tmp_path, "work") == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"model_id": "temporary-model"},
        {"thinking": "high"},
        {"temperature": 0.7},
        {"timeout_seconds": 45.0},
        {"context": "fork"},
    ],
)
async def test_any_runtime_override_without_role_dispatches_work_agent(
    tmp_path, monkeypatch, override
) -> None:
    manager = _manager(tmp_path)
    launch = AsyncMock(return_value="WorkAgent queued (id: task-1).")
    monkeypatch.setattr("nanobot.agent.tools.subagent.run_work_agent", launch)
    tool = SubagentTool(manager)
    token = bind_request_context(_request())
    try:
        result = await tool.execute(action="run", task="one-off task", **override)
    finally:
        reset_request_context(token)
        await manager.close()

    assert "workagent" in result.lower()
    launch.assert_awaited_once()
    assert launch.await_args.kwargs["role_definition"].category == "work"
    for key, value in override.items():
        assert launch.await_args.kwargs[key] == value


@pytest.mark.asyncio
async def test_no_role_and_no_override_remains_general(tmp_path) -> None:
    class Manager:
        def __init__(self) -> None:
            self.workspace = tmp_path
            self.bus = MessageBus()
            self.spawn = AsyncMock(return_value="Subagent queued (id: task-1).")

    manager = Manager()
    tool = SubagentTool(manager)  # type: ignore[arg-type]
    token = bind_request_context(_request())
    try:
        result = await tool.execute(action="run", task="mixed task")
    finally:
        reset_request_context(token)

    assert "queued" in result.lower()
    manager.spawn.assert_awaited_once()
    assert manager.spawn.await_args.kwargs["role"] == "general"
    assert role_usage(tmp_path, "general")["runs"] == 1


@pytest.mark.asyncio
async def test_work_agent_identity_overrides_cannot_mix_with_persistent_role(tmp_path) -> None:
    manager = _manager(tmp_path)
    tool = SubagentTool(manager)
    token = bind_request_context(_request())
    try:
        result = await tool.execute(
            action="run",
            task="inspect",
            role="debugger",
            system_prompt="Replace the debugger identity for this task.",
        )
    finally:
        reset_request_context(token)
        await manager.close()

    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert "cannot be combined with role" in result


@pytest.mark.asyncio
async def test_work_agent_tools_remain_parent_bounded_and_non_recursive(tmp_path) -> None:
    manager = _manager(tmp_path)
    try:
        role_definition = build_work_role_definition(
            _general_payload(manager),
            description="One-off code reader",
            system_prompt="Read only the assigned source evidence.",
            tools=["read_file", "exec"],
        )
        tools = manager._build_tools(
            role="work",
            role_definition=role_definition,
            allowed_tools={"read_file", "subagent"},
        )

        assert set(tools.tool_names) == {"read_file"}
        assert "subagent" not in tools.tool_names
        assert "exec" not in tools.tool_names
    finally:
        await manager.close()


def test_work_agent_rejects_unknown_or_recursive_tools() -> None:
    general = {
        "description": "General",
        "system_prompt": "General",
        "tools": ["read_file"],
    }

    with pytest.raises(ValueError, match="forbidden WorkAgent tools"):
        build_work_role_definition(general, tools=["subagent"])
    with pytest.raises(ValueError, match="forbidden WorkAgent tools"):
        build_work_role_definition(general, tools=["made_up_tool"])


def test_work_agent_snapshot_does_not_inherit_general_runtime_tuning() -> None:
    role = build_work_role_definition({
        "description": "General tuned prompt",
        "system_prompt": "General tuned system prompt",
        "tools": ["read_file", "exec"],
        "model_id": "general-model",
        "thinking": "high",
        "temperature": 1.2,
        "timeout_seconds": 999,
        "context": "fork",
    })

    assert role.system_prompt != "General tuned system prompt"
    assert role.model_id is None
    assert role.thinking is None
    assert role.temperature is None
    assert role.timeout_seconds is None
    assert role.context == "fresh"


@pytest.mark.asyncio
async def test_run_work_agent_always_uses_manager_spawn_lifecycle(tmp_path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    role_definition = build_work_role_definition(
        _general_payload(manager),
        description="One-off analyst",
        system_prompt="Analyze this task only.",
        tools=["read_file"],
    )
    runtime = _runtime(prefix="MODEL PREFIX")
    run = AsyncMock(return_value="done")
    monkeypatch.setattr(manager, "_run_subagent", run)
    try:
        result = await run_work_agent(
            manager,
            task="background analyze",
            runtime=runtime,
            role_definition=role_definition,
            origin_channel="test",
            origin_chat_id="chat-1",
            session_key="test:chat-1",
            allowed_tools={"read_file", "subagent"},
        )
        await asyncio.sleep(0)
    finally:
        await manager.close()

    assert "Subagent" in result
    run.assert_awaited_once()
    status = run.await_args.args[4]
    child_runtime = run.await_args.args[5]
    assert status.role == "work"
    assert status.role_snapshot is role_definition
    assert status.model_id is None
    assert child_runtime is runtime
    assert child_runtime.system_prompt_prefix == "MODEL PREFIX"
    assert run.await_args.kwargs["role_definition"] is role_definition


@pytest.mark.asyncio
async def test_work_agents_dispatch_concurrently_without_inline_path(tmp_path, monkeypatch) -> None:
    manager = _manager(tmp_path, max_concurrent_subagents=2)
    role_definition = build_work_role_definition(_general_payload(manager), tools=["read_file"])
    both_started = asyncio.Event()
    release = asyncio.Event()
    started = 0

    async def run(*_args, **_kwargs) -> str:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await release.wait()
        return "done"

    monkeypatch.setattr(manager, "_run_subagent", run)

    first = await run_work_agent(
        manager,
        task="first",
        runtime=_runtime(),
        role_definition=role_definition,
        origin_channel="test",
        origin_chat_id="chat-1",
        session_key="test:chat-1",
        allowed_tools={"read_file"},
    )
    second = await run_work_agent(
        manager,
        task="second",
        runtime=_runtime(),
        role_definition=role_definition,
        origin_channel="test",
        origin_chat_id="chat-1",
        session_key="test:chat-1",
        allowed_tools={"read_file"},
    )
    try:
        assert "id:" in first
        assert "id:" in second
        await asyncio.wait_for(both_started.wait(), timeout=1.0)
        assert started == 2
    finally:
        release.set()
        await manager.close()
