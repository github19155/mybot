"""Acceptance tests for Main orchestration and evolving subagent roles."""

from __future__ import annotations

import json
from importlib.resources import files as pkg_files
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.subagent_roles import (
    SubagentRoleStore,
    list_roles,
    record_role_use,
    resolve_role,
    role_usage,
)
from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.agent.tools.subagent import SubagentTool
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config


def _config(tmp_path) -> Config:
    return Config(agents={"defaults": {"workspace": str(tmp_path)}})


def _write_dream_role(tmp_path, name: str, **overrides) -> None:
    root = tmp_path / "skills" / ".agents"
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": name,
        "description": f"Handle recurring {name} work.",
        "system_prompt": f"Own the {name} responsibility and report concise evidence.",
        "tools": ["read_file"],
        "status": "active",
        "version": 1,
        "created_by": "dream",
    }
    payload.update(overrides)
    (root / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_general_is_permanent_capable_fallback() -> None:
    general = resolve_role(None, "general")
    coder = resolve_role(None, "coder")

    assert general.category == "general"
    assert general.source == "builtin"
    assert general.permissions == "read-write-exec"
    assert {"read_file", "write_file", "exec", "browser_status"}.issubset(general.tools)

    # Browser is available to workers as a capability, but specialist builtins
    # keep it opt-in instead of all receiving it by default.
    assert "browser_status" not in coder.tools


def test_dream_specialist_is_discovered_with_runtime_metadata(tmp_path) -> None:
    _write_dream_role(
        tmp_path,
        "release-triage",
        tools=["read_file", "exec", "browser_status"],
        version=3,
        evolution=["v3: add browser status for release UI checks"],
    )
    config = _config(tmp_path)

    role = resolve_role(config, "release-triage")
    names = {item.name for item in list_roles(config)}

    assert role.source == "dream"
    assert role.category == "specialist"
    assert role.status == "active"
    assert role.version == 3
    assert role.created_by == "dream"
    assert role.permissions == "read-write-exec"
    assert {"read_file", "exec", "browser_status"}.issubset(role.tools)
    assert {"general", "release-triage"}.issubset(names)


def test_dream_role_file_cannot_shadow_builtin_general(tmp_path) -> None:
    _write_dream_role(
        tmp_path,
        "general",
        description="Dream should not replace the fallback",
        system_prompt="Pretend to be a narrower worker.",
        tools=["read_file"],
        status="cold",
        version=99,
    )

    role = resolve_role(_config(tmp_path), "general")

    assert role.source == "builtin"
    assert role.category == "general"
    assert role.status == "active"
    assert role.version == 1
    assert "browser_status" in role.tools
    assert role.description != "Dream should not replace the fallback"


def test_role_usage_is_advisory_and_persisted(tmp_path) -> None:
    _write_dream_role(tmp_path, "release-triage")
    config = _config(tmp_path)

    record_role_use(tmp_path, "release-triage")
    record_role_use(tmp_path, "release-triage")

    usage = role_usage(tmp_path, "release-triage")
    resolved = resolve_role(config, "release-triage")
    assert usage["runs"] == 2
    assert usage["last_used"]
    assert resolved.usage["runs"] == 2


def test_cold_dream_role_is_retained_until_user_deletes_it(tmp_path) -> None:
    _write_dream_role(tmp_path, "legacy-helper", status="cold", version=4)
    config = _config(tmp_path)
    store = SubagentRoleStore(config)
    role_path = tmp_path / "skills" / ".agents" / "legacy-helper.json"

    assert store.get("legacy-helper")["status"] == "cold"
    assert role_path.exists()

    deleted = store.delete("legacy-helper")
    assert deleted == {"name": "legacy-helper", "deleted": True}
    assert not role_path.exists()

    with pytest.raises(ValueError, match="general.*permanent|permanent.*general"):
        store.delete("general")


@pytest.mark.asyncio
async def test_subagent_tool_uses_general_when_role_is_omitted(tmp_path) -> None:
    class Manager:
        def __init__(self) -> None:
            self.workspace = tmp_path
            self.bus = MessageBus()
            self.spawn = AsyncMock(return_value="Subagent queued (id: task-1).")
            self.run_inline = AsyncMock(return_value="done")

    manager = Manager()
    tool = SubagentTool(manager)  # type: ignore[arg-type]
    token = bind_request_context(RequestContext(
        channel="test",
        chat_id="chat-1",
        session_key="test:chat-1",
        runtime=MagicMock(),
        allowed_tools=frozenset({"subagent", "read_file"}),
    ))
    try:
        result = await tool.execute(action="run", task="handle a mixed operational task")
    finally:
        reset_request_context(token)

    assert "queued" in result.lower()
    manager.spawn.assert_awaited_once()
    assert manager.spawn.await_args.kwargs["role"] == "general"
    assert role_usage(tmp_path, "general")["runs"] == 1


@pytest.mark.asyncio
async def test_general_worker_can_load_browser_capability(tmp_path, monkeypatch) -> None:
    from nanobot.agent.subagent import SubagentManager

    monkeypatch.setenv("NANOBOT_BROWSER_ENABLED", "true")
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
    )
    try:
        general_tools = manager._build_tools(role="general")
        coder_tools = manager._build_tools(role="coder")

        assert {"browser_open", "browser_status", "browser_handoff"}.issubset(
            general_tools.tool_names
        )
        assert "browser_status" not in coder_tools.tool_names
        assert (
            type(general_tools.get("browser_status")).__module__
            == "nanobot.agent.tools.subagent_browser"
        )
    finally:
        await manager.close()


def test_prompts_define_orchestration_and_specialist_evolution() -> None:
    tool_contract = (
        pkg_files("nanobot") / "templates" / "agent" / "tool_contract.md"
    ).read_text(encoding="utf-8")
    dream = (
        pkg_files("nanobot") / "templates" / "agent" / "dream.md"
    ).read_text(encoding="utf-8")

    assert "## Main Orchestrator Contract" in tool_contract
    assert "permanent `general` worker" in tool_contract
    assert "Browser is a worker capability" in tool_contract
    assert "status=cold" in tool_contract

    assert "## Specialist discovery & evolution" in dream
    assert "skills/.agents/_usage.json" in dream
    assert "Do NOT optimize from one noisy run" in dream
    assert "Never delete a specialist role automatically" in dream
    assert "Browser Agent" in dream and "not a reason" in dream
