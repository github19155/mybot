"""Tests for the subagent fleet surface — origin fields, budget, steer, snapshot."""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent import SubagentManager
from nanobot.agent.permissions import PermissionManager
from nanobot.agent.subagent import SubagentStatus
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.providers.base import GenerationSettings, LLMProvider, LLMUsage
from nanobot.utils.llm_runtime import LLMRuntime


def _manager(tmp_path: Path, **kw) -> SubagentManager:
    defaults = dict(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        permission_manager=PermissionManager(Config()),
    )
    defaults.update(kw)
    return SubagentManager(**defaults)


def _runtime(*, model: str = "test-model") -> LLMRuntime:
    provider = MagicMock(spec=LLMProvider)
    provider.generation = GenerationSettings(temperature=0.1, max_tokens=4096)
    return LLMRuntime.capture(provider, model, context_window_tokens=128_000)


async def _drain_subagent_tasks(sm: SubagentManager) -> None:
    tasks = list(sm._running_tasks.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Origin fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_status_carries_origin_fields(tmp_path):
    status = SubagentStatus(
        task_id="t1",
        label="test",
        task_description="do",
        started_at=time.monotonic(),
        phase="queued",
        origin_channel="weixin",
        origin_chat_id="chat-1",
        session_key="weixin:chat-1",
        origin_message_id="msg-9",
    )
    assert status.origin_channel == "weixin"
    assert status.origin_chat_id == "chat-1"
    assert status.session_key == "weixin:chat-1"
    assert status.origin_message_id == "msg-9"
    assert status.started_at_ms is None
    assert status.ended_at_ms is None


# ---------------------------------------------------------------------------
# Per-session spawn budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_queues_when_session_budget_exhausted(tmp_path):
    sm = _manager(tmp_path)
    sm.max_concurrent_per_session = 1
    sm._announce_result = AsyncMock()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def run(_spec):
        entered.set()
        await release.wait()
        return SimpleNamespace(
            stop_reason="completed", final_content="done", error=None, tool_events=[],
        )

    sm.runner.run = AsyncMock(side_effect=run)

    result = await sm.spawn(
        "first task",
        origin_channel="weixin",
        origin_chat_id="c1",
        session_key="s1",
        runtime=_runtime(),
    )
    assert isinstance(result, str)
    assert not result.startswith("Error:")
    await asyncio.wait_for(entered.wait(), timeout=1)

    result2 = await sm.spawn(
        "second task",
        origin_channel="weixin",
        origin_chat_id="c1",
        session_key="s1",
        runtime=_runtime(),
    )
    assert isinstance(result2, str)
    assert "queued" in result2.lower()

    release.set()
    await sm.close()


@pytest.mark.asyncio
async def test_spawn_budget_is_per_session(tmp_path):
    sm = _manager(tmp_path)
    sm.max_concurrent_per_session = 1

    r1 = await sm.spawn(
        "task a",
        origin_channel="weixin",
        origin_chat_id="c1",
        session_key="s1",
        runtime=_runtime(),
    )
    assert isinstance(r1, str)
    # A different session key is still admitted.
    r2 = await sm.spawn(
        "task b",
        origin_channel="weixin",
        origin_chat_id="c2",
        session_key="s2",
        runtime=_runtime(),
    )
    assert isinstance(r2, str)
    assert not r2.startswith("Error:")

    await sm.close()


# ---------------------------------------------------------------------------
# steer — bounded queue, drop-oldest, iteration-boundary drain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steer_returns_false_for_unknown_task(tmp_path):
    sm = _manager(tmp_path)
    assert await sm.steer("nope", "hello") is False
    await sm.close()


@pytest.mark.asyncio
async def test_steer_drop_oldest_on_overflow(tmp_path):
    sm = _manager(tmp_path)
    task = asyncio.create_task(asyncio.Event().wait())
    sm._running_tasks["t1"] = task
    sm._task_statuses["t1"] = SubagentStatus(
        task_id="t1", label="test", task_description="task", started_at=time.monotonic(),
        state="running",
    )

    for i in range(7):  # maxsize 5 -> oldest 2 dropped
        assert await sm.steer("t1", f"msg-{i}") is True

    items = await sm._drain_steer_queue("t1", limit=10)
    assert items == ["msg-2", "msg-3", "msg-4", "msg-5", "msg-6"]

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_steer_drain_respects_limit(tmp_path):
    sm = _manager(tmp_path)
    task = asyncio.create_task(asyncio.Event().wait())
    sm._running_tasks["t1"] = task
    sm._task_statuses["t1"] = SubagentStatus(
        task_id="t1", label="test", task_description="task", started_at=time.monotonic(),
        state="running",
    )

    for i in range(3):
        await sm.steer("t1", f"m{i}")
    assert await sm._drain_steer_queue("t1", limit=2) == ["m0", "m1"]
    assert await sm._drain_steer_queue("t1", limit=2) == ["m2"]
    assert await sm._drain_steer_queue("t1", limit=2) == []

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Finished history + fleet_snapshot
# ---------------------------------------------------------------------------


def _finished_status(task_id: str, **overrides) -> SubagentStatus:
    defaults = dict(
        task_id=task_id,
        label="test",
        task_description="do stuff",
        started_at=time.monotonic(),
        started_at_ms=1_000,
        phase="done",
        role="coder",
        model="m",
        origin_channel="weixin",
        origin_chat_id="c1",
        session_key="s1",
        origin_message_id=None,
        ended_at_ms=2_000,
        usage=LLMUsage.reported(input_tokens=10, output_tokens=5),
        state="completed",
    )
    defaults.update(overrides)
    return SubagentStatus(**defaults)


def test_finished_history_bounded_at_50(tmp_path):
    sm = _manager(tmp_path)
    for i in range(60):
        sm._record_finished(_finished_status(f"t{i}", started_at_ms=i))
    assert len(sm._finished) == 50
    assert sm._finished[0].task_id == "t10"
    assert sm._finished[-1].task_id == "t59"
    sm._record_finished(None)
    assert len(sm._finished) == 50


def test_record_finished_skips_incomplete(tmp_path):
    sm = _manager(tmp_path)
    sm._record_finished(_finished_status("t1", phase="queued", state="queued"))
    assert len(sm._finished) == 0


def test_fleet_snapshot_shape_and_ordering(tmp_path):
    sm = _manager(tmp_path)
    # A running status and a finished status (finished has an earlier start).
    running = _finished_status(
        "run-1",
        phase="running",
        state="running",
        started_at_ms=3_000,
        ended_at_ms=None,
        label="Running task",
    )
    finished = _finished_status("fin-1", started_at_ms=1_000, label="Finished task")
    sm._task_statuses["run-1"] = running
    sm._record_finished(finished)

    snap = sm.fleet_snapshot()
    assert snap["subagents"][0]["task_id"] == "run-1"
    assert snap["subagents"][1]["task_id"] == "fin-1"
    running_row = snap["subagents"][0]
    assert running_row["state"] == "running"
    assert running_row["phase"] == "running"
    assert running_row["label"] == "Running task"
    assert running_row["origin"] == {
        "channel": "weixin",
        "chat_id": "c1",
        "session_key": "s1",
        "message_id": None,
    }
    assert running_row["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }
    assert snap["subagents"][1]["state"] == "completed"
    assert snap["subagents"][1]["started_at_ms"] == 1_000
    assert snap["subagents"][1]["ended_at_ms"] is not None  # stamped on record
    assert snap["budget"] == {
        "max_per_session": 8,
        "max_global": 16,
        "running": 0,
        "running_by_session": {},
    }
