from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.runtime_control import AgentRuntimeControl
from nanobot.bus.queue import MessageBus
from nanobot.bus.runtime_events import RuntimeEventContext, SessionTurnPersisted
from nanobot.context_management import AgentContextControl
from nanobot.providers.base import ProviderConversationState


def _loop(tmp_path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(
        max_tokens=1000,
        temperature=0.1,
        reasoning_effort=None,
    )
    provider.estimate_prompt_tokens.return_value = (6000, "test-estimate")
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=10_000,
    )


def _fill_session(loop: AgentLoop, count: int = 12):
    session = loop.sessions.get_or_create("cli:direct")
    for i in range(count):
        session.add_message("user" if i % 2 == 0 else "assistant", f"message {i}")
    loop.sessions.save(session)
    return session


def test_context_status_reports_machine_readable_pressure(tmp_path) -> None:
    loop = _loop(tmp_path)
    _fill_session(loop)

    snapshot = AgentContextControl(loop).status("cli:direct")

    assert snapshot.estimated_tokens == 6000
    assert snapshot.input_budget_tokens == 10_000 - 1000 - 1024
    assert snapshot.usage_ratio == round(6000 / snapshot.input_budget_tokens, 6)
    assert snapshot.recommendation == "recommended"
    assert snapshot.should_compact is True
    assert snapshot.can_compact is True


@pytest.mark.asyncio
async def test_manual_compaction_advances_checkpoint_and_clears_provider_state(tmp_path) -> None:
    loop = _loop(tmp_path)
    session = _fill_session(loop)
    session.provider_state = ProviderConversationState(
        kind="test",
        provider="test-provider",
        model="test-model",
        version=1,
        payload={"cursor": "opaque-test-state"},
    )
    loop.sessions.save(session)
    loop.consolidator.archive_session = AsyncMock(return_value="checkpoint summary")

    result = await AgentContextControl(loop).compact("cli:direct")

    assert result["status"] == "compacted"
    saved = loop.sessions.get_or_create("cli:direct")
    assert saved.last_archived == 12
    assert saved.metadata["_last_summary"]["text"] == "checkpoint summary"
    assert saved.provider_state is None
    assert len(saved.messages) == 12


@pytest.mark.asyncio
async def test_main_self_compaction_waits_for_persisted_turn_boundary(tmp_path) -> None:
    loop = _loop(tmp_path)
    _fill_session(loop)
    loop.consolidator.archive_session = AsyncMock(return_value="checkpoint summary")
    control = AgentRuntimeControl(loop)

    result = await control.context_compact("cli:direct")

    assert result["status"] == "scheduled"
    assert loop.sessions.get_or_create("cli:direct").last_archived == 0
    assert control.context_status("cli:direct")["pending_compaction"] is True

    await loop.runtime_events.publish(
        SessionTurnPersisted(
            context=RuntimeEventContext(
                channel="cli",
                chat_id="direct",
                session_key="cli:direct",
            ),
            turn_id="turn-1",
            sender_id="user",
        )
    )

    assert loop.sessions.get_or_create("cli:direct").last_archived == 12
    assert control.context_status("cli:direct")["pending_compaction"] is False


def test_context_tool_is_registered_for_main_only(tmp_path) -> None:
    loop = _loop(tmp_path)

    assert "context" in loop.tool_names
    tool = loop.tools.get("context")
    assert tool is not None
    assert "later model requests" in tool.description
