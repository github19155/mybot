from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nanobot.agent.memory import MemoryStore
from nanobot.agent.permissions import PermissionManager
from nanobot.bus.events import InboundMessage
from nanobot.command import register_builtin_commands
from nanobot.command.builtin import (
    build_help_text,
    builtin_command_palette,
    cmd_dream_prompt,
)
from nanobot.command.dream_commands import cmd_dream, cmd_dream_log
from nanobot.command.router import CommandContext, CommandRouter
from nanobot.config.schema import Config


def _make_control_ctx(
    tmp_path,
    raw: str = "/dream",
    *,
    args: str = "",
    enabled: bool = True,
) -> CommandContext:
    config = Config()
    config.agents.defaults.dream.enabled = enabled
    msg = InboundMessage(
        channel="cli",
        sender_id="u1",
        chat_id="direct",
        content=raw,
    )
    loop = SimpleNamespace(
        workspace=tmp_path,
        model_management=SimpleNamespace(config_snapshot=lambda: config),
        permissions=PermissionManager(lambda: config),
    )
    return CommandContext(
        msg=msg,
        session=None,
        key=msg.session_key,
        raw=raw,
        args=args,
        loop=loop,
    )


def _make_dream_prompt_ctx(
    tmp_path,
    raw: str = "/dream-prompt",
    args: str = "",
) -> CommandContext:
    msg = InboundMessage(
        channel="cli",
        sender_id="u1",
        chat_id="direct",
        content=raw,
    )
    loop = SimpleNamespace(context=SimpleNamespace(memory=MemoryStore(tmp_path)))
    return CommandContext(
        msg=msg,
        session=None,
        key=msg.session_key,
        raw=raw,
        args=args,
        loop=loop,
    )


@pytest.mark.asyncio
async def test_dream_queues_manual_background_request(tmp_path) -> None:
    ctx = _make_control_ctx(tmp_path)

    out = await cmd_dream(ctx)

    request_path = tmp_path / "memory" / ".dream_request.json"
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    assert payload["workload"] == "dream.consolidation"
    assert isinstance(payload["requested_at_ms"], int)
    assert "request queued" in out.content.lower()
    assert "background worker" in out.content.lower()


@pytest.mark.asyncio
async def test_dream_disabled_does_not_queue_request(tmp_path) -> None:
    ctx = _make_control_ctx(tmp_path, enabled=False)

    out = await cmd_dream(ctx)

    assert "disabled" in out.content.lower()
    assert not (tmp_path / "memory" / ".dream_request.json").exists()


@pytest.mark.asyncio
async def test_dream_log_reports_no_audit_records(tmp_path) -> None:
    out = await cmd_dream_log(_make_control_ctx(tmp_path, raw="/dream-log"))

    assert "no validated audit records" in out.content.lower()
    assert "`/dream`" in out.content


@pytest.mark.asyncio
async def test_dream_log_reads_validated_result_records(tmp_path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir(parents=True)
    rows = [
        {
            "run_id": "run-old",
            "workload": "dream.consolidation",
            "source_revision": 7,
            "summary": "Older summary.",
            "proposals": [],
        },
        {
            "run_id": "run-new",
            "workload": "dream.governance",
            "source_revision": 12,
            "summary": "Found a governance candidate.",
            "proposals": [
                {"state": "pending"},
                {"state": "awaiting_user_approval"},
            ],
        },
    ]
    (memory / "dream_results.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    out = await cmd_dream_log(_make_control_ctx(tmp_path, raw="/dream-log"))

    assert "## Dream Audit" in out.content
    assert "run-new" in out.content
    assert "Found a governance candidate." in out.content
    assert "2 (1 pending, 1 awaiting user approval)" in out.content
    assert "run-old" not in out.content


@pytest.mark.asyncio
async def test_dream_log_can_show_recent_count(tmp_path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir(parents=True)
    (memory / "dream_results.jsonl").write_text(
        "\n".join(
            json.dumps({
                "run_id": f"run-{index}",
                "workload": "dream.consolidation",
                "source_revision": index,
                "summary": f"summary-{index}",
                "proposals": [],
            })
            for index in range(3)
        ) + "\n",
        encoding="utf-8",
    )

    out = await cmd_dream_log(
        _make_control_ctx(tmp_path, raw="/dream-log 2", args="2")
    )

    assert "run-2" in out.content
    assert "run-1" in out.content
    assert "run-0" not in out.content


@pytest.mark.asyncio
async def test_dream_log_rejects_invalid_count(tmp_path) -> None:
    out = await cmd_dream_log(
        _make_control_ctx(tmp_path, raw="/dream-log 11", args="11")
    )

    assert out.content == "Usage: /dream-log [1-10]"


def test_dream_commands_expose_control_plane_surface_only() -> None:
    palette = builtin_command_palette()
    commands = {item["command"]: item for item in palette}

    assert "/dream" in commands
    assert "Queue a manual Dream" in str(commands["/dream"]["description"])
    assert "/dream-log" in commands
    assert "validated Dream findings and proposals" in str(
        commands["/dream-log"]["description"]
    )
    assert "/dream-restore" not in commands
    assert "/dream-restore" not in build_help_text()

    router = CommandRouter()
    register_builtin_commands(router)
    registered = router._registered_commands()
    assert "/dream" in registered
    assert "/dream-log" in registered
    assert "/dream-restore" not in registered


@pytest.mark.asyncio
async def test_dream_prompt_reports_default_prompt(tmp_path) -> None:
    out = await cmd_dream_prompt(_make_dream_prompt_ctx(tmp_path))

    assert "Dream memory instructions: nanobot default" in out.content
    assert "prompts/dream.md" in out.content
    assert str(tmp_path) not in out.content
    assert "/dream-prompt init" in out.content


@pytest.mark.asyncio
async def test_dream_prompt_init_copies_default_prompt(tmp_path) -> None:
    ctx = _make_dream_prompt_ctx(tmp_path, "/dream-prompt init", "init")

    out = await cmd_dream_prompt(ctx)

    prompt_file = tmp_path / "prompts" / "dream.md"
    assert "Created Dream memory instructions" in out.content
    assert "prompts/dream.md" in out.content
    assert str(tmp_path) not in out.content
    assert "fully replaces nanobot's default Dream guide" in out.content
    assert prompt_file.read_text(encoding="utf-8") == MemoryStore.default_dream_prompt() + "\n"


@pytest.mark.asyncio
async def test_dream_prompt_init_does_not_overwrite_existing_prompt(tmp_path) -> None:
    prompt_file = tmp_path / "prompts" / "dream.md"
    prompt_file.parent.mkdir()
    prompt_file.write_text("custom", encoding="utf-8")
    ctx = _make_dream_prompt_ctx(tmp_path, "/dream-prompt init", "init")

    out = await cmd_dream_prompt(ctx)

    assert "already exist" in out.content
    assert "prompts/dream.md" in out.content
    assert str(tmp_path) not in out.content
    assert prompt_file.read_text(encoding="utf-8") == "custom"


@pytest.mark.asyncio
async def test_dream_prompt_init_recreates_empty_prompt(tmp_path) -> None:
    prompt_file = tmp_path / "prompts" / "dream.md"
    prompt_file.parent.mkdir()
    prompt_file.write_text("  \n", encoding="utf-8")
    ctx = _make_dream_prompt_ctx(tmp_path, "/dream-prompt init", "init")

    out = await cmd_dream_prompt(ctx)

    assert "Created Dream memory instructions" in out.content
    assert prompt_file.read_text(encoding="utf-8") == MemoryStore.default_dream_prompt() + "\n"


def test_dream_prompt_command_in_help_and_palette() -> None:
    palette = builtin_command_palette()
    dream_prompt = next(item for item in palette if item["command"] == "/dream-prompt")

    assert dream_prompt["arg_hint"] == "[init]"
    assert dream_prompt["lifecycle"] == "side_channel"
    assert dream_prompt["accepts_args"] is True
    assert "/dream-prompt [init]" in build_help_text()
