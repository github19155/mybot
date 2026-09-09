"""Regression coverage for deterministic self-requested context compaction."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest

from nanobot.agent.tools.runtime_control import AgentRuntimeControl
from nanobot.bus.runtime_events import (
    RuntimeEventBus,
    RuntimeEventContext,
    SessionTurnPersisted,
    SessionTurnStarted,
)


@dataclass(frozen=True)
class _Snapshot:
    can_compact: bool = True

    def as_dict(self) -> dict[str, object]:
        return {"can_compact": self.can_compact}


class _FakeContextControl:
    def __init__(self) -> None:
        self.compact_calls: list[tuple[str, object | None]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    def status(self, session_key: str, *, runtime: object | None = None) -> _Snapshot:
        return _Snapshot()

    async def compact(
        self,
        session_key: str,
        *,
        runtime: object | None = None,
    ) -> dict[str, object]:
        self.compact_calls.append((session_key, runtime))
        self.started.set()
        if self.block:
            await self.release.wait()
        return {"status": "compacted", "reason": "manual_compaction"}


def _control() -> tuple[AgentRuntimeControl, _FakeContextControl, RuntimeEventBus]:
    events = RuntimeEventBus()
    target = SimpleNamespace(runtime_events=events)
    control = AgentRuntimeControl(cast(Any, target))
    fake = _FakeContextControl()
    setattr(control, "_AgentRuntimeControl__context", fake)
    return control, fake, events


def _event_context(session_key: str = "websocket:test") -> RuntimeEventContext:
    return RuntimeEventContext(
        channel="websocket",
        chat_id="test",
        session_key=session_key,
    )


@pytest.mark.asyncio
async def test_context_compact_waits_for_persisted_turn_boundary() -> None:
    control, fake, events = _control()
    runtime_marker = object()

    result = await control.context_compact(
        "websocket:test",
        runtime=cast(Any, runtime_marker),
    )

    assert result["status"] == "scheduled"
    assert result["applies_to"] == "next_turn"
    assert fake.compact_calls == []
    assert control.context_status("websocket:test")["pending_compaction"] is True

    await events.publish(
        SessionTurnPersisted(
            context=_event_context(),
            turn_id="turn-1",
            sender_id="user",
        )
    )

    assert fake.compact_calls == [("websocket:test", runtime_marker)]
    assert control.context_status("websocket:test")["pending_compaction"] is False


@pytest.mark.asyncio
async def test_persisted_event_waits_until_compaction_finishes() -> None:
    control, fake, events = _control()
    fake.block = True
    await control.context_compact("websocket:test")

    publishing = asyncio.create_task(
        events.publish(
            SessionTurnPersisted(
                context=_event_context(),
                turn_id="turn-1",
                sender_id="user",
            )
        )
    )
    await fake.started.wait()

    assert publishing.done() is False
    fake.release.set()
    await publishing
    assert control.context_status("websocket:test")["pending_compaction"] is False


@pytest.mark.asyncio
async def test_next_turn_started_drains_leftover_pending_compaction() -> None:
    control, fake, events = _control()
    await control.context_compact("websocket:test")

    await events.publish(SessionTurnStarted(context=_event_context()))

    assert fake.compact_calls == [("websocket:test", None)]
    assert control.context_status("websocket:test")["pending_compaction"] is False


@pytest.mark.asyncio
async def test_duplicate_compaction_requests_are_coalesced() -> None:
    control, fake, events = _control()

    first = await control.context_compact("websocket:test")
    second = await control.context_compact("websocket:test")

    assert first["status"] == "scheduled"
    assert second["status"] == "scheduled"
    assert second["reason"] == "already_pending"

    await events.publish(
        SessionTurnPersisted(
            context=_event_context(),
            turn_id="turn-1",
            sender_id="user",
        )
    )
    assert fake.compact_calls == [("websocket:test", None)]
