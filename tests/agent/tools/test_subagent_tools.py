"""Tests for subagent tool registration and wiring."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.context import TranscriptInput
from nanobot.agent.tools.context import RequestContext
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import GenerationSettings
from nanobot.utils.llm_runtime import LLMRuntime

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def _runtime(provider: MagicMock, model: str = "test-model") -> LLMRuntime:
    provider.generation = GenerationSettings(temperature=0.1, max_tokens=4096)
    return LLMRuntime.capture(provider, model, context_window_tokens=128_000)


@pytest.mark.asyncio
async def test_run_inline_returns_result_without_announcement(tmp_path):
    """Inline subagents return directly instead of injecting a follow-up."""
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus

    provider = MagicMock()
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    manager.runner.run = AsyncMock(return_value=SimpleNamespace(
        stop_reason="done",
        final_content="review result",
        error=None,
        tool_events=[],
    ))
    manager._announce_result = AsyncMock()

    result = await manager.run_inline(
        task="review this",
        session_key="test:c1",
        runtime=_runtime(provider),
    )

    assert result == "review result"
    manager._announce_result.assert_not_awaited()
    assert manager._running_tasks == {}
    assert manager._task_statuses == {}
    assert manager._session_tasks == {}


@pytest.mark.asyncio
async def test_run_inline_returns_structured_error(tmp_path):
    """Inline subagent failures remain tool errors for the parent runner."""
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.tools.registry import is_tool_error_result
    from nanobot.bus.queue import MessageBus

    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    manager.runner.run = AsyncMock(return_value=SimpleNamespace(
        stop_reason="error",
        final_content=None,
        error="subagent failed",
        tool_events=[],
    ))

    result = await manager.run_inline(
        task="review this",
        session_key="test:c1",
        runtime=_runtime(MagicMock()),
    )

    assert result == "subagent failed"
    assert is_tool_error_result(result)
    assert manager._running_tasks == {}
    assert manager._session_tasks == {}


@pytest.mark.asyncio
async def test_subagent_exec_tool_receives_allowed_env_keys(tmp_path):
    """allowed_env_keys from ExecToolConfig must be forwarded to the subagent's ExecTool."""
    from nanobot.agent.subagent import SubagentManager, SubagentStatus
    from nanobot.agent.tools.shell import ExecToolConfig
    from nanobot.bus.queue import MessageBus
    from nanobot.config.schema import ToolsConfig

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        tools_config=ToolsConfig(exec=ExecToolConfig(allowed_env_keys=["GOPATH", "JAVA_HOME"])),
    )
    mgr._announce_result = AsyncMock()

    async def fake_run(spec):
        exec_tool = spec.tools.get("exec")
        assert exec_tool is not None
        assert exec_tool.allowed_env_keys == ["GOPATH", "JAVA_HOME"]
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    status = SubagentStatus(
        task_id="sub-1", label="label", task_description="do task", started_at=time.monotonic()
    )
    await mgr._run_subagent(
        "sub-1",
        "do task",
        "label",
        {"channel": "test", "chat_id": "c1"},
        status,
        _runtime(provider),
    )

    mgr.runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_subagent_uses_configured_max_iterations(tmp_path):
    """Subagents should honor the configured tool-iteration limit."""
    from nanobot.agent.subagent import SubagentManager, SubagentStatus
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        max_iterations=37,
    )
    mgr._announce_result = AsyncMock()

    async def fake_run(spec):
        assert spec.max_iterations == 37
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    status = SubagentStatus(
        task_id="sub-1", label="label", task_description="do task", started_at=time.monotonic()
    )
    await mgr._run_subagent(
        "sub-1",
        "do task",
        "label",
        {"channel": "test", "chat_id": "c1"},
        status,
        _runtime(provider),
    )

    mgr.runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_spawn_forwards_temperature_to_run_spec(tmp_path):
    """A temperature passed to spawn() should reach the AgentRunSpec."""
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    mgr._announce_result = AsyncMock()

    parent_runtime = _runtime(provider)
    seen = {}

    async def fake_run(spec):
        seen["temperature"] = spec.runtime.generation.temperature
        seen["runtime"] = spec.runtime
        return SimpleNamespace(
            stop_reason="done", final_content="done", error=None, tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    await mgr.spawn(task="do task", runtime=parent_runtime, temperature=0.9)
    await asyncio.gather(*mgr._running_tasks.values(), return_exceptions=True)

    assert seen["temperature"] == 0.9
    assert seen["runtime"] is not parent_runtime
    assert parent_runtime.generation.temperature == 0.1


@pytest.mark.asyncio
async def test_background_spawn_waits_for_concurrency_capacity(tmp_path):
    """Background tasks should be accepted and start when capacity becomes available."""
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.tools.subagent import SubagentTool
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        max_concurrent_subagents=1,
    )
    mgr._announce_result = AsyncMock()

    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()

    async def fake_run(spec):
        task = spec.initial_messages[-1]["content"]
        if task == "first task":
            first_entered.set()
            await release_first.wait()
        else:
            second_entered.set()
            await release_second.wait()
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    from nanobot.agent.tools.context import RequestContext, request_context

    tool = SubagentTool(mgr)
    with request_context(RequestContext(
        channel="test",
        chat_id="c1",
        session_key="test:c1",
        runtime=_runtime(provider),
    )):
        first_result = await tool.execute(action="run", task="first task")
        assert "id:" in first_result.lower()
        await asyncio.wait_for(first_entered.wait(), timeout=1.0)

        second_result = await tool.execute(action="run", task="second task")
        assert "id:" in second_result.lower()
        tasks = list(mgr._running_tasks.values())
        await asyncio.sleep(0)
        assert not second_entered.is_set()
        phases = {status.task_description: status.phase for status in mgr._task_statuses.values()}
        assert phases == {"first task": "initializing", "second task": "queued"}

    release_first.set()
    await asyncio.wait_for(second_entered.wait(), timeout=1.0)
    release_second.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)
    assert mgr._running_tasks == {}


@pytest.mark.asyncio
async def test_spawn_tool_waits_for_inline_result():
    from nanobot.agent.tools.context import RequestContext, request_context
    from nanobot.agent.tools.subagent import SubagentTool

    class Manager:
        max_concurrent_subagents = 1

        def __init__(self):
            self.inline = AsyncMock(return_value="review result")
            self.spawn = AsyncMock(return_value="queued")

        def get_running_count(self):
            return 0

        async def run_inline(self, **kwargs):
            return await self.inline(**kwargs)

    manager = Manager()
    tool = SubagentTool(manager)
    runtime = _runtime(MagicMock())
    with request_context(RequestContext(
        channel="test",
        chat_id="c1",
        session_key="test:c1",
        runtime=runtime,
    )):
        result = await tool.execute(action="run", task="review this", wait=True)

    assert result == "review result"
    manager.inline.assert_awaited_once()
    manager.spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_spawn_waits_for_concurrency_capacity(tmp_path):
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.tools.context import RequestContext, request_context
    from nanobot.agent.tools.subagent import SubagentTool
    from nanobot.bus.queue import MessageBus

    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        max_concurrent_subagents=1,
    )
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()

    async def fake_run(spec):
        task = spec.initial_messages[-1]["content"]
        if task == "first":
            first_entered.set()
            await release_first.wait()
        else:
            second_entered.set()
            await release_second.wait()
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
        )

    manager.runner.run = AsyncMock(side_effect=fake_run)
    tool = SubagentTool(manager)
    with request_context(RequestContext(
        channel="test",
        chat_id="c1",
        session_key="test:c1",
        runtime=_runtime(MagicMock()),
    )):
        first = asyncio.create_task(tool.execute(action="run", task="first", wait=True))
        await asyncio.wait_for(first_entered.wait(), timeout=1.0)

        second = asyncio.create_task(tool.execute(action="run", task="second", wait=True))
        await asyncio.sleep(0)

        assert not second.done()
        assert not second_entered.is_set()
        assert manager.get_running_count() == 1
        release_first.set()
        assert await first == "done"
        await asyncio.wait_for(second_entered.wait(), timeout=1.0)
        release_second.set()
        assert await second == "done"

    assert manager.get_running_count() == 0
    assert manager._session_tasks == {}


@pytest.mark.asyncio
async def test_runner_executes_inline_spawn_batch_concurrently(tmp_path):
    """Adjacent blocking consultations should share one concurrent tool batch."""
    from nanobot.agent.hook import AgentHook, AgentHookContext
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.tools.context import RequestContext, request_context
    from nanobot.agent.tools.execution import execute_tool_calls
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.agent.tools.subagent import SubagentTool
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.base import ToolCallRequest

    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        max_concurrent_subagents=2,
    )
    both_entered = asyncio.Event()
    release = asyncio.Event()
    entered: list[str] = []

    async def fake_run(spec):
        entered.append(spec.initial_messages[-1]["content"])
        if len(entered) == 2:
            both_entered.set()
        await release.wait()
        return SimpleNamespace(
            stop_reason="done",
            final_content=spec.initial_messages[-1]["content"],
            error=None,
            tool_events=[],
        )

    manager.runner.run = AsyncMock(side_effect=fake_run)
    tools = ToolRegistry()
    tools.register(SubagentTool(manager))
    runtime = _runtime(MagicMock())
    calls = [
        ToolCallRequest(
            id="subagent-1",
            name="subagent",
            arguments={"action": "run", "task": "first", "wait": True},
        ),
        ToolCallRequest(
            id="subagent-2",
            name="subagent",
            arguments={"action": "run", "task": "second", "wait": True},
        ),
    ]

    with request_context(RequestContext(
        channel="test",
        chat_id="c1",
        session_key="test:c1",
        runtime=runtime,
    )):
        execution = asyncio.create_task(execute_tool_calls(
            tools,
            calls,
            concurrent=True,
            external_lookup_counts={},
            workspace_violation_counts={},
            hook=AgentHook(),
            context=AgentHookContext(iteration=0, messages=[], session_key="test:c1"),
        ))
        await asyncio.wait_for(both_entered.wait(), timeout=1.0)
        release.set()
        results, events = await execution

    assert set(entered) == {"first", "second"}
    assert results == ["first", "second"]
    assert [event["status"] for event in events] == ["ok", "ok"]
    assert manager._running_tasks == {}


@pytest.mark.asyncio
async def test_cancel_by_session_cancels_inline_subagent(tmp_path):
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus

    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    entered = asyncio.Event()

    async def fake_run(spec):
        entered.set()
        await asyncio.Event().wait()

    manager.runner.run = AsyncMock(side_effect=fake_run)
    inline = asyncio.create_task(manager.run_inline(
        task="wait",
        session_key="test:c1",
        runtime=_runtime(MagicMock()),
    ))
    await asyncio.wait_for(entered.wait(), timeout=1.0)

    assert await manager.cancel_by_session("test:c1") == 1
    with pytest.raises(asyncio.CancelledError):
        await inline
    assert manager._running_tasks == {}
    assert manager._task_statuses == {}
    assert manager._session_tasks == {}


def test_subagent_default_max_concurrent_matches_agent_defaults(tmp_path):
    """Direct SubagentManager construction should use the agent default concurrency limit."""
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    mgr = SubagentManager(
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    assert AgentDefaults().max_concurrent_subagents == 16
    assert mgr.max_concurrent_subagents == AgentDefaults().max_concurrent_subagents


def test_subagent_default_max_iterations_matches_agent_defaults(tmp_path):
    """Direct SubagentManager construction should use the agent default limit."""
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    mgr = SubagentManager(
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    assert mgr.max_iterations == AgentDefaults().max_tool_iterations


def test_agent_loop_passes_max_iterations_to_subagents(tmp_path):
    """AgentLoop's configured limit should be shared with spawned subagents."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        max_iterations=42,
    )

    assert loop.subagents.max_iterations == 42


@pytest.mark.asyncio
async def test_agent_loop_syncs_updated_max_iterations_before_run(tmp_path):
    """Runtime max_iterations changes should be reflected before tool execution."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        max_iterations=42,
    )
    loop.tools.get_definitions = MagicMock(return_value=[])

    async def fake_run(spec):
        assert spec.max_iterations == 55
        assert loop.subagents.max_iterations == 55
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
            messages=[],
            usage=None,
            had_injections=False,
            tools_used=[],
        )

    loop.runner.run = AsyncMock(side_effect=fake_run)
    loop.max_iterations = 55

    await loop._run_agent_loop(
        TranscriptInput(history=[], current_message=None),
        runtime=loop.llm_runtime(),
    )

    loop.runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_drain_pending_no_block_when_no_subagents(tmp_path):
    """_drain_pending should not block when no sub-agents are running."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")

    pending_queue: asyncio.Queue = asyncio.Queue()
    injection_callback = None
    terminal_injection_callback = None

    async def fake_runner_run(spec):
        nonlocal injection_callback, terminal_injection_callback
        injection_callback = spec.injection_callback
        terminal_injection_callback = spec.terminal_injection_callback
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
            messages=[],
            usage=None,
            had_injections=False,
            tools_used=[],
            provider_state=None,
        )

    loop.runner.run = AsyncMock(side_effect=fake_runner_run)

    runtime = loop.llm_runtime()
    await loop._run_agent_loop(
        TranscriptInput(history=[{"role": "user", "content": "test"}], current_message=None),
        runtime=runtime,
        session=None,
        request_context=RequestContext(channel="test", chat_id="c1", runtime=runtime),
        pending_queue=pending_queue,
    )

    assert injection_callback is not None
    assert terminal_injection_callback is not None

    # With no sub-agents and an empty queue, both paths return immediately.
    assert await asyncio.wait_for(injection_callback(), timeout=1.0) == []
    assert await asyncio.wait_for(terminal_injection_callback(), timeout=1.0) == []

@pytest.mark.asyncio
async def test_terminal_drain_returns_available_without_waiting(tmp_path):
    """The terminal drain returns already-available messages and never blocks."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.events import InboundMessage
    from nanobot.bus.queue import MessageBus
    from nanobot.session.manager import Session

    loop = AgentLoop(
        bus=MessageBus(),
        provider=MagicMock(),
        workspace=tmp_path,
        model="test-model",
    )
    pending_queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
    session = Session(key="test:no-block")
    terminal_injection_callback = None

    async def fake_runner_run(spec):
        nonlocal terminal_injection_callback
        terminal_injection_callback = spec.terminal_injection_callback
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
            messages=[],
            usage=None,
            had_injections=False,
            tools_used=[],
            provider_state=None,
        )

    loop.runner.run = AsyncMock(side_effect=fake_runner_run)

    await loop._run_agent_loop(
        TranscriptInput(history=[{"role": "user", "content": "test"}], current_message=None),
        runtime=loop.llm_runtime(),
        session=session,
        pending_queue=pending_queue,
    )
    assert terminal_injection_callback is not None

    # A pending subagent result and a user message are both already available:
    # the drain returns them immediately, user input first, and does not await
    # any further messages or running subagent tasks.
    pending_queue.put_nowait(
        InboundMessage(
            sender_id="subagent",
            channel="system",
            chat_id="test:c1",
            content="subagent result",
            metadata={"injected_event": "subagent_result", "subagent_task_id": "sub-1"},
        )
    )
    pending_queue.put_nowait(
        InboundMessage(sender_id="user", channel="test", chat_id="c1", content="user message")
    )

    items = await asyncio.wait_for(terminal_injection_callback(), timeout=1.0)
    assert [item["content"] for item in items] == ["user message", "subagent result"]
    # The queue is fully drained; nothing is left for a later rendezvous.
    assert await asyncio.wait_for(terminal_injection_callback(), timeout=1.0) == []


@pytest.mark.asyncio
async def test_terminal_drain_refills_excess_messages(tmp_path):
    """The terminal drain returns only the cap and puts the rest back."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.events import InboundMessage
    from nanobot.bus.queue import MessageBus
    from nanobot.session.manager import Session

    loop = AgentLoop(
        bus=MessageBus(),
        provider=MagicMock(),
        workspace=tmp_path,
        model="test-model",
    )
    pending_queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
    session = Session(key="test:cap")
    terminal_injection_callback = None

    async def fake_runner_run(spec):
        nonlocal terminal_injection_callback
        terminal_injection_callback = spec.terminal_injection_callback
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
            messages=[],
            usage=None,
            had_injections=False,
            tools_used=[],
            provider_state=None,
        )

    loop.runner.run = AsyncMock(side_effect=fake_runner_run)

    await loop._run_agent_loop(
        TranscriptInput(history=[{"role": "user", "content": "test"}], current_message=None),
        runtime=loop.llm_runtime(),
        session=session,
        pending_queue=pending_queue,
    )
    assert terminal_injection_callback is not None

    for index in range(5):
        pending_queue.put_nowait(
            InboundMessage(sender_id="user", channel="test", chat_id="c1", content=f"msg {index}")
        )

    items = await asyncio.wait_for(terminal_injection_callback(), timeout=1.0)
    assert len(items) == 3
    assert pending_queue.qsize() == 2


