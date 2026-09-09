from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.command import register_builtin_commands
from nanobot.command.builtin import build_help_text, builtin_command_palette
from nanobot.command.router import CommandContext, CommandRouter


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


def _fill(loop: AgentLoop) -> None:
    session = loop.sessions.get_or_create("cli:direct")
    for i in range(12):
        session.add_message("user" if i % 2 == 0 else "assistant", f"message {i}")
    loop.sessions.save(session)


def _ctx(loop: AgentLoop, raw: str) -> CommandContext:
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=raw)
    return CommandContext(
        msg=msg,
        session=loop.sessions.get_or_create(msg.session_key),
        key=msg.session_key,
        raw=raw,
        loop=loop,
        is_user_turn=True,
    )


@pytest.mark.asyncio
async def test_context_command_reports_pressure(tmp_path) -> None:
    loop = _loop(tmp_path)
    _fill(loop)
    router = CommandRouter()
    register_builtin_commands(router)

    out = await router.dispatch(_ctx(loop, "/context"))

    assert out is not None
    assert "## Context" in out.content
    assert "Usage: 75.2%" in out.content
    assert "Input budget: 7976 tokens" in out.content
    assert "Recommendation: recommended" in out.content


@pytest.mark.asyncio
async def test_compact_command_uses_existing_consolidator_without_main_model_turn(tmp_path) -> None:
    loop = _loop(tmp_path)
    _fill(loop)
    loop.consolidator.archive_session = AsyncMock(return_value="checkpoint summary")
    router = CommandRouter()
    register_builtin_commands(router)

    out = await router.dispatch(_ctx(loop, "/compact"))

    assert out is not None
    assert "Context compacted." in out.content
    saved = loop.sessions.get_or_create("cli:direct")
    assert saved.last_archived == 12
    assert saved.metadata["_last_summary"]["text"] == "checkpoint summary"


def test_context_commands_appear_in_help_and_palette() -> None:
    commands = {item["command"] for item in builtin_command_palette()}

    assert "/context" in commands
    assert "/compact" in commands
    help_text = build_help_text()
    assert "/context" in help_text
    assert "/compact" in help_text
