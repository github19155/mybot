"""Tests for token-bounded session history replay."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse
from nanobot.session.manager import Session


def _make_loop(tmp_path: Path, context_window_tokens: int = 200_000) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=context_window_tokens,
    )


def _populated_session(turns: int) -> Session:
    session = Session(key="test:populated")
    for index in range(turns):
        session.add_message("user", f"msg-{index}")
        session.add_message("assistant", f"reply-{index}")
    return session


def _tool_round(call_id: str) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": "x", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "name": "x", "content": "ok"},
    ]


def _declared_tool_call_ids(messages: list[dict]) -> set[str]:
    return {
        str(tool_call["id"])
        for message in messages
        for tool_call in message.get("tool_calls") or []
        if isinstance(tool_call, dict) and tool_call.get("id")
    }


def _tool_result_ids(messages: list[dict]) -> set[str]:
    return {
        str(message["tool_call_id"])
        for message in messages
        if message.get("role") == "tool" and message.get("tool_call_id")
    }


def _assert_no_orphan_tool_results(messages: list[dict]) -> None:
    declared = _declared_tool_call_ids(messages)
    assert _tool_result_ids(messages) <= declared


def _contains_content(messages: list[dict], content: str) -> bool:
    return any(message.get("content") == content for message in messages)


def test_default_history_has_no_message_count_limit() -> None:
    session = _populated_session(1_001)

    history = session.get_history()

    assert len(history) == 2_002
    assert history[0]["content"] == "msg-0"
    assert history[-1]["content"] == "reply-1000"


def test_explicit_message_limit_still_starts_at_user_turn() -> None:
    history = _populated_session(30).get_history(max_messages=25)

    assert len(history) <= 25
    assert history[0]["role"] == "user"


@pytest.mark.asyncio
async def test_process_message_hands_complete_replay_to_runner(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, context_window_tokens=32_768)
    loop.provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="ok", tool_calls=[], usage=None)
    )
    loop.tools.get_definitions = MagicMock(return_value=[])

    session = loop.sessions.get_or_create("cli:test")
    with patch.object(session, "get_history", wraps=session.get_history) as get_history:
        result = await loop._process_message(
            InboundMessage(channel="cli", sender_id="user", chat_id="test", content="hello")
        )

    assert result is not None
    assert get_history.call_args.kwargs == {"extend_to_user": False}


@pytest.mark.asyncio
async def test_runner_checkpoint_keeps_current_user_as_replay_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compaction must replace accepted history without swallowing the live user delta."""
    loop = _make_loop(tmp_path, context_window_tokens=32_768)
    loop.provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="ok", tool_calls=[], usage=None)
    )
    loop.tools.get_definitions = MagicMock(return_value=[])

    session = loop.sessions.get_or_create("cli:test")
    session.add_message("user", "old")
    session.add_message("assistant", "old answer")
    session.add_message("user", "long older turn")
    older_ids = {f"older-{index}" for index in range(70)}
    for call_id in sorted(older_ids):
        session.messages.extend(_tool_round(call_id))
    session.add_message("assistant", "older final")

    def estimate_prompt_tokens(_provider, _model, messages, _tools):
        # Force pressure specifically while the accepted old tool history is present.
        # This keeps the regression deterministic as prompts/tool registries evolve.
        has_old_tool_turn = bool(
            (_declared_tool_call_ids(messages) | _tool_result_ids(messages)) & older_ids
        )
        return (100_000 if has_old_tool_turn else 100, "test-counter")

    monkeypatch.setattr(
        "nanobot.agent.context_governance.estimate_prompt_tokens_chain",
        estimate_prompt_tokens,
    )

    result = await loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="test",
            content="new question",
        )
    )

    assert result is not None
    assert result.content == "ok"

    current_requests = [
        call.kwargs["messages"]
        for call in loop.provider.chat_with_retry.await_args_list
        if "messages" in call.kwargs
        and _contains_content(call.kwargs["messages"], "new question")
    ]
    assert len(current_requests) == 1
    sent_messages = current_requests[0]

    # Product boundary: the live user delta reaches the model, while the accepted
    # oversized tool turn is represented by the checkpoint instead of replayed raw.
    assert _contains_content(sent_messages, "new question")
    assert not _contains_content(sent_messages, "long older turn")
    assert not (_declared_tool_call_ids(sent_messages) & older_ids)
    assert not (_tool_result_ids(sent_messages) & older_ids)
    _assert_no_orphan_tool_results(sent_messages)

    # Compaction changes replay state, not the durable transcript. The complete old
    # tool exchange remains persisted and paired after the turn is saved.
    assert _contains_content(session.messages, "long older turn")
    assert older_ids <= _declared_tool_call_ids(session.messages)
    assert older_ids <= _tool_result_ids(session.messages)
    _assert_no_orphan_tool_results(session.messages)

    new_question_index = next(
        index
        for index, message in enumerate(session.messages)
        if message.get("content") == "new question"
    )
    assert 0 <= session.last_archived < new_question_index
    assert session.metadata.get("_last_summary", {}).get("text")

    # The saved checkpoint semantically separates archived history from the current
    # turn without depending on a fixed continuation message count or role layout.
    replay = session.get_history()
    assert _contains_content(replay, "new question")
    assert not _contains_content(replay, "long older turn")
    assert not (_declared_tool_call_ids(replay) & older_ids)
    assert not (_tool_result_ids(replay) & older_ids)
    _assert_no_orphan_tool_results(replay)
