from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import nanobot.agent.loop as loop_module
from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus

SESSION_KEY = "websocket:chat"


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    session = loop.sessions.get_or_create(SESSION_KEY)
    loop.sessions.save(session)
    return loop


def _user(content: str = "main request") -> InboundMessage:
    return InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id="chat",
        content=content,
    )


def _result(task_id: str, content: str | None = None) -> InboundMessage:
    return InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="websocket:chat",
        content=content or f"result for {task_id}",
        session_key_override=SESSION_KEY,
        metadata={
            "injected_event": "subagent_result",
            "subagent_task_id": task_id,
            "origin_message_id": "origin-1",
        },
    )


async def _wait(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=2.0)


async def _cancel_run(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_worker_result_waits_until_current_turn_fully_finishes(
    tmp_path: Path,
) -> None:
    loop = _make_loop(tmp_path)
    main_started = asyncio.Event()
    release_main = asyncio.Event()
    first_turn_finalizing = asyncio.Event()
    release_first_finalize = asyncio.Event()
    result_routed = asyncio.Event()
    result_started = asyncio.Event()

    original_is_result = loop._is_subagent_result_message

    def observed_is_result(msg: InboundMessage) -> bool:
        is_result = original_is_result(msg)
        if is_result:
            result_routed.set()
        return is_result

    loop._is_subagent_result_message = observed_is_result  # type: ignore[method-assign]

    async def process(msg: InboundMessage, **_: object) -> None:
        if msg.sender_id == "user":
            main_started.set()
            await release_main.wait()
            return None
        result_started.set()
        return None

    finalize_calls = 0

    async def finalize(session_key: str) -> None:
        nonlocal finalize_calls
        assert session_key == SESSION_KEY
        finalize_calls += 1
        if finalize_calls == 1:
            first_turn_finalizing.set()
            await release_first_finalize.wait()

    loop._process_message = process  # type: ignore[method-assign]
    loop._publish_next_deferred_automation_turn = finalize  # type: ignore[method-assign]

    run_task = asyncio.create_task(loop.run())
    try:
        await loop.bus.publish_inbound(_user())
        await _wait(main_started)

        await loop.bus.publish_inbound(_result("task-1"))
        await _wait(result_routed)
        assert not result_started.is_set()

        release_main.set()
        await _wait(first_turn_finalizing)
        # delivery.complete(), pending cleanup, and delivery.idle() have already
        # completed here, while the current session lock is still owned.
        assert not result_started.is_set()

        release_first_finalize.set()
        await _wait(result_started)
    finally:
        await _cancel_run(run_task)


@pytest.mark.asyncio
async def test_user_followup_still_uses_pending_queue_while_result_gets_new_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = _make_loop(tmp_path)
    main_started = asyncio.Event()
    release_main = asyncio.Event()
    followup_queued = asyncio.Event()
    result_routed = asyncio.Event()
    result_started = asyncio.Event()

    original_record = loop_module.record_pending_followup

    def observed_record(session, msg: InboundMessage):
        followup_id = original_record(session, msg)
        if msg.sender_id == "user" and msg.content == "follow up":
            followup_queued.set()
        return followup_id

    monkeypatch.setattr(loop_module, "record_pending_followup", observed_record)
    original_is_result = loop._is_subagent_result_message

    def observed_is_result(msg: InboundMessage) -> bool:
        is_result = original_is_result(msg)
        if is_result:
            result_routed.set()
        return is_result

    loop._is_subagent_result_message = observed_is_result  # type: ignore[method-assign]

    async def process(msg: InboundMessage, **_: object) -> None:
        if msg.sender_id == "user" and msg.content == "main request":
            main_started.set()
            await release_main.wait()
            return None
        if msg.sender_id == "subagent":
            result_started.set()
        return None

    loop._process_message = process  # type: ignore[method-assign]
    run_task = asyncio.create_task(loop.run())
    try:
        await loop.bus.publish_inbound(_user())
        await _wait(main_started)

        await loop.bus.publish_inbound(_user("follow up"))
        await loop.bus.publish_inbound(_result("task-followup"))
        await _wait(followup_queued)
        await _wait(result_routed)

        pending = loop._pending_queues[SESSION_KEY]
        queued = pending.get_nowait()
        assert queued.sender_id == "user"
        assert queued.content == "follow up"
        assert pending.empty()
        assert not result_started.is_set()

        release_main.set()
        await _wait(result_started)
    finally:
        await _cancel_run(run_task)


@pytest.mark.asyncio
async def test_idle_worker_result_starts_independent_turn(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    result_started = asyncio.Event()
    release_result = asyncio.Event()
    seen_pending_queue: asyncio.Queue[InboundMessage] | None = None

    async def process(
        msg: InboundMessage,
        *,
        pending_queue: asyncio.Queue[InboundMessage] | None = None,
        **_: object,
    ) -> None:
        nonlocal seen_pending_queue
        assert msg.metadata["subagent_task_id"] == "task-idle"
        seen_pending_queue = pending_queue
        result_started.set()
        await release_result.wait()
        return None

    loop._process_message = process  # type: ignore[method-assign]
    run_task = asyncio.create_task(loop.run())
    try:
        await loop.bus.publish_inbound(_result("task-idle"))
        await _wait(result_started)
        assert seen_pending_queue is not None
        assert loop._pending_queues.get(SESSION_KEY) is seen_pending_queue
        release_result.set()
    finally:
        release_result.set()
        await _cancel_run(run_task)


@pytest.mark.asyncio
async def test_multiple_worker_results_are_serialized_as_distinct_turns(
    tmp_path: Path,
) -> None:
    loop = _make_loop(tmp_path)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    order: list[str] = []

    async def process(msg: InboundMessage, **_: object) -> None:
        task_id = str(msg.metadata["subagent_task_id"])
        order.append(task_id)
        if task_id == "task-1":
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
        return None

    loop._process_message = process  # type: ignore[method-assign]
    first = asyncio.create_task(loop._dispatch(_result("task-1")))
    await _wait(first_started)
    second = asyncio.create_task(loop._dispatch(_result("task-2")))

    assert not second_started.is_set()
    release_first.set()
    await _wait(second_started)
    await asyncio.gather(first, second)

    assert order == ["task-1", "task-2"]


@pytest.mark.asyncio
async def test_worker_result_runs_after_current_turn_error(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    main_started = asyncio.Event()
    release_error = asyncio.Event()
    result_started = asyncio.Event()

    async def process(msg: InboundMessage, **_: object) -> None:
        if msg.sender_id == "user":
            main_started.set()
            await release_error.wait()
            raise RuntimeError("boom")
        result_started.set()
        return None

    loop._process_message = process  # type: ignore[method-assign]
    main_task = asyncio.create_task(loop._dispatch(_user()))
    await _wait(main_started)
    result_task = asyncio.create_task(loop._dispatch(_result("task-error")))

    assert not result_started.is_set()
    release_error.set()
    await _wait(result_started)
    await asyncio.gather(main_task, result_task)


@pytest.mark.asyncio
async def test_duplicate_worker_results_are_consumed_once(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def process(msg: InboundMessage, **_: object) -> None:
        nonlocal calls
        calls += 1
        session = loop.sessions.get_or_create(SESSION_KEY)
        assert loop._persist_subagent_followup(session, msg) is True
        loop.sessions.save(session)
        first_started.set()
        await release_first.wait()
        return None

    loop._process_message = process  # type: ignore[method-assign]
    first = asyncio.create_task(loop._dispatch(_result("task-duplicate")))
    await _wait(first_started)
    duplicate = asyncio.create_task(loop._dispatch(_result("task-duplicate")))

    release_first.set()
    await asyncio.gather(first, duplicate)
    assert calls == 1


@pytest.mark.asyncio
async def test_waiting_result_does_not_recreate_deleted_session(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    process = AsyncMock(return_value=None)
    loop._process_message = process  # type: ignore[method-assign]

    original_get_lock = loop._get_session_lock
    lock = original_get_lock(SESSION_KEY)
    await lock.acquire()
    dispatch_reached_lock = asyncio.Event()

    def observed_get_lock(session_key: str) -> asyncio.Lock:
        assert session_key == SESSION_KEY
        dispatch_reached_lock.set()
        return original_get_lock(session_key)

    loop._get_session_lock = observed_get_lock  # type: ignore[method-assign]
    task = asyncio.create_task(loop._dispatch(_result("task-stale")))
    await _wait(dispatch_reached_lock)

    assert loop.sessions.delete_session(SESSION_KEY) is True
    lock.release()
    await task

    process.assert_not_awaited()
    assert loop.sessions.get_cached(SESSION_KEY) is None
    assert loop.sessions.read_session_snapshot(SESSION_KEY) is None
