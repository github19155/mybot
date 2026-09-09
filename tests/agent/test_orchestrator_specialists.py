"""Acceptance tests for Main orchestration and evolving subagent roles."""

from __future__ import annotations

import json
from importlib.resources import files as pkg_files
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.memory import MemoryStore
from nanobot.agent.subagent_role_storage import (
    candidate_entries,
    dream_role_entries,
    record_role_use,
    role_usage,
    write_dream_role,
)
from nanobot.agent.subagent_roles import SubagentRoleStore, list_roles, resolve_role
from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.agent.tools.dream_roles import DreamRoleTool
from nanobot.agent.tools.registry import is_tool_error_result
from nanobot.agent.tools.subagent import SubagentTool
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config


def _config(tmp_path) -> Config:
    return Config(agents={"defaults": {"workspace": str(tmp_path)}})


def _write_dream_role(tmp_path, name: str, **overrides) -> None:
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
    write_dream_role(tmp_path, name, payload)


def test_config_registers_general_as_builtin_role() -> None:
    config = Config()
    partial = Config(subagentRoles={"general": {"thinking": "high"}})

    assert "general" in config.subagent_roles
    assert "general" in partial.subagent_roles
    assert partial.subagent_roles["general"].thinking == "high"


def test_general_is_permanent_all_capability_fallback() -> None:
    general = resolve_role(None, "general")
    disabled_override = resolve_role(
        Config(subagentRoles={"general": {"disabled": True}}),
        "general",
    )
    coder = resolve_role(None, "coder")

    assert general.category == "general"
    assert general.source == "builtin"
    assert general.permissions == "read-write-exec"
    assert general.disabled is False
    assert disabled_override.disabled is False
    assert {
        "read_file",
        "write_file",
        "exec",
        "web_search",
        "browser_open",
        "browser_status",
    }.issubset(general.tools)

    # Focused specialists keep Browser opt-in; General deliberately remains all-capability.
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


def test_dream_state_cannot_shadow_builtin_general(tmp_path) -> None:
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

    assert store.get("legacy-helper")["status"] == "cold"
    assert "legacy-helper" in dream_role_entries(config)

    deleted = store.delete("legacy-helper")
    assert deleted == {"name": "legacy-helper", "deleted": True}
    assert "legacy-helper" not in dream_role_entries(config)

    with pytest.raises(ValueError, match="general.*permanent|permanent.*general"):
        store.delete("general")
    with pytest.raises(ValueError, match="general.*permanent|permanent.*general"):
        store.update("general", {"disabled": True})


@pytest.mark.asyncio
async def test_dream_role_manager_requires_repeated_evidence(tmp_path) -> None:
    tool = DreamRoleTool(tmp_path, config=_config(tmp_path))
    values = {
        "description": "Triage recurring release readiness work.",
        "system_prompt": "Check release readiness and report evidence.",
        "tools": ["read_file", "exec"],
        "evolution": ["v1: created from repeated release-readiness work"],
    }

    first = await tool.execute(
        action="observe",
        role="release-triage",
        responsibility="release readiness triage",
        evidence="release readiness review for project alpha",
    )
    blocked = await tool.execute(action="create", role="release-triage", values=values)
    second = await tool.execute(
        action="observe",
        role="release-triage",
        responsibility="release readiness triage",
        evidence="separate release readiness review for project beta",
    )
    created = await tool.execute(action="create", role="release-triage", values=values)

    assert json.loads(first)["evidence_count"] == 1
    assert is_tool_error_result(blocked)
    assert json.loads(second)["evidence_count"] == 2
    created_payload = json.loads(created)
    assert created_payload["version"] == 1
    assert created_payload["created_by"] == "dream"
    assert "release-triage" not in candidate_entries(tmp_path)
    assert resolve_role(_config(tmp_path), "release-triage").source == "dream"


@pytest.mark.asyncio
async def test_dream_role_manager_versions_updates_and_never_exposes_delete(tmp_path) -> None:
    tool = DreamRoleTool(tmp_path, config=_config(tmp_path))
    await tool.execute(
        action="observe",
        role="release-triage",
        responsibility="release readiness triage",
        evidence="release review one",
    )
    await tool.execute(
        action="observe",
        role="release-triage",
        responsibility="release readiness triage",
        evidence="release review two",
    )
    await tool.execute(
        action="create",
        role="release-triage",
        values={
            "description": "Triage recurring releases.",
            "system_prompt": "Check release readiness.",
            "tools": ["read_file"],
        },
    )

    updated = json.loads(await tool.execute(
        action="update",
        role="release-triage",
        values={
            "description": "Triage recurring releases with tests.",
            "system_prompt": "Check release readiness and test evidence.",
            "tools": ["read_file", "exec"],
            "evolution": ["v2: add test execution after repeated gaps"],
        },
    ))
    cold = json.loads(await tool.execute(action="mark_cold", role="release-triage"))
    active = json.loads(await tool.execute(action="activate", role="release-triage"))

    assert updated["version"] == 2
    assert cold["version"] == 3 and cold["status"] == "cold"
    assert active["version"] == 4 and active["status"] == "active"
    assert "delete" not in DreamRoleTool.parameters["properties"]["action"]["enum"]
    assert "disable" not in DreamRoleTool.parameters["properties"]["action"]["enum"]


@pytest.mark.asyncio
async def test_dream_generic_file_tools_cannot_write_role_state(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    tools = store.build_dream_tools(config=_config(tmp_path))

    assert "dream_roles" in tools.tool_names
    result = await tools.execute(
        "write_file",
        {"path": "agents/roles.json", "content": '{"owned": true}\n'},
    )

    assert "outside allowed directory" in result
    assert not (tmp_path / "agents" / "roles.json").exists()


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


@pytest.mark.asyncio
async def test_dream_specialist_can_receive_browser_capability(tmp_path, monkeypatch) -> None:
    from nanobot.agent.model_management import ModelManagement
    from nanobot.agent.subagent import SubagentManager

    monkeypatch.setenv("NANOBOT_BROWSER_ENABLED", "true")
    _write_dream_role(
        tmp_path,
        "ui-checker",
        tools=["read_file", "browser_status", "browser_snapshot"],
    )
    config = _config(tmp_path)
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        model_management=ModelManagement(config),
    )
    try:
        tools = manager._build_tools(role="ui-checker")

        assert {"read_file", "browser_status", "browser_snapshot"}.issubset(
            tools.tool_names
        )
        assert (
            type(tools.get("browser_status")).__module__
            == "nanobot.agent.tools.subagent_browser"
        )
    finally:
        await manager.close()


def test_prompts_define_orchestration_and_specialist_evolution() -> None:
    identity = (
        pkg_files("nanobot") / "templates" / "agent" / "identity.md"
    ).read_text(encoding="utf-8")
    tool_contract = (
        pkg_files("nanobot") / "templates" / "agent" / "tool_contract.md"
    ).read_text(encoding="utf-8")
    dream = (
        pkg_files("nanobot") / "templates" / "agent" / "dream.md"
    ).read_text(encoding="utf-8")

    assert "Main Agent / Orchestrator" in identity
    assert "permanent `general` worker" not in identity
    assert "Main owns the conversation" in tool_contract
    assert "permanent `general`" in tool_contract
    assert "Browser is a worker capability" in tool_contract
    assert "status=cold" in tool_contract
    assert "Specialist deletion is user-governed" in tool_contract
    assert "role.list" in tool_contract

    assert "## Specialist discovery & evolution" in dream
    assert "agents/role_candidates.json" in dream
    assert "agents/role_usage.json" in dream
    assert "dream_roles" in dream
    assert "_manifest" not in dream
    assert "Do NOT optimize from one noisy run" in dream
    assert "Never delete a specialist role automatically" in dream
    assert "Browser Agent" in dream and "not a reason" in dream
