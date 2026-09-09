"""Regression coverage for task-scoped WorkAgent launches."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.subagent import SubagentManager
from nanobot.agent.subagent_role_storage import role_usage
from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.agent.tools.subagent import SubagentTool, _work_role_definition
from nanobot.agent.work_agent import run_work_agent
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings
from nanobot.utils.llm_runtime import LLMRuntime


def _runtime() -> LLMRuntime:
    provider = MagicMock()
    provider.generation = GenerationSettings()
    return LLMRuntime.capture(provider, "test/model", context_window_tokens=32_000)


def _general_payload(manager: SubagentManager) -> dict:
    return manager.role_get("general")


@pytest.mark.asyncio
async def test_subagent_tool_dispatches_non_persistent_work_agent(tmp_path, monkeypatch) -> None:
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
    )
    launch = AsyncMock(return_value="WorkAgent queued (id: task-1).")
    monkeypatch.setattr("nanobot.agent.tools.subagent.run_work_agent", launch)
    tool = SubagentTool(manager)
    token = bind_request_context(RequestContext(
        channel="test",
        chat_id="chat-1",
        session_key="test:chat-1",
        runtime=_runtime(),
        allowed_tools=frozenset({"subagent", "read_file", "exec"}),
    ))
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
    assert set(role_definition.tools) == {"read_file", "exec"}
    assert role_usage(tmp_path, "general") == {}
    assert role_usage(tmp_path, "work") == {}


@pytest.mark.asyncio
async def test_work_agent_identity_overrides_cannot_mix_with_persistent_role(tmp_path) -> None:
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
    )
    tool = SubagentTool(manager)
    token = bind_request_context(RequestContext(
        channel="test",
        chat_id="chat-1",
        session_key="test:chat-1",
        runtime=_runtime(),
        allowed_tools=frozenset({"subagent", "read_file"}),
    ))
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
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
    )
    try:
        role_definition = _work_role_definition(
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
        _work_role_definition(
            general,
            description="One-off",
            system_prompt="One-off",
            tools=["subagent"],
        )
    with pytest.raises(ValueError, match="forbidden WorkAgent tools"):
        _work_role_definition(
            general,
            description="One-off",
            system_prompt="One-off",
            tools=["made_up_tool"],
        )


@pytest.mark.asyncio
async def test_run_work_agent_reuses_subagent_execution_with_ephemeral_snapshot(
    tmp_path, monkeypatch
) -> None:
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
    )
    role_definition = _work_role_definition(
        _general_payload(manager),
        description="One-off analyst",
        system_prompt="Analyze this task only.",
        tools=["read_file"],
    )
    run = AsyncMock(return_value="done")
    monkeypatch.setattr(manager, "_run_subagent", run)
    try:
        result = await run_work_agent(
            manager,
            task="analyze",
            runtime=_runtime(),
            role_definition=role_definition,
            wait=True,
            origin_channel="test",
            origin_chat_id="chat-1",
            session_key="test:chat-1",
            allowed_tools={"read_file", "subagent"},
        )
    finally:
        await manager.close()

    assert result == "done"
    run.assert_awaited_once()
    kwargs = run.await_args.kwargs
    assert kwargs["role_definition"] is role_definition
    status = run.await_args.args[4]
    assert status.role == "work"
    assert status.role_snapshot is role_definition
    assert status.model == "test/model"
