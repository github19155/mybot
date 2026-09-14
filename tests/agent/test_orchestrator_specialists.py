"""Acceptance tests for Main orchestration and config-owned specialist roles."""

from __future__ import annotations

from importlib.resources import files as pkg_files
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.permissions import PermissionManager
from nanobot.agent.subagent_role_storage import record_role_use, role_usage
from nanobot.agent.subagent_roles import SubagentRoleStore, list_roles, resolve_role
from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.agent.tools.subagent import SubagentTool
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.permission_config import PermissionPolicyConfig


def _config(tmp_path, *, roles: dict[str, object] | None = None) -> Config:
    return Config(
        agents={"defaults": {"workspace": str(tmp_path)}},
        subagentRoles=roles or {},
    )


def _exposed_tool_names(registry) -> set[str]:
    return {item["function"]["name"] for item in registry.get_definitions()}


def test_config_registers_general_as_builtin_role() -> None:
    config = Config()
    partial = Config(subagentRoles={"general": {"thinking": "high"}})

    assert "general" in config.subagent_roles
    assert "general" in partial.subagent_roles
    assert partial.subagent_roles["general"].thinking == "high"


def test_general_is_permanent_full_tool_fallback() -> None:
    default_config = Config()
    general = resolve_role(default_config, "general")
    disabled_override = resolve_role(
        Config(subagentRoles={"general": {"disabled": True}}),
        "general",
    )
    narrow_override = resolve_role(
        Config(subagentRoles={"general": {"tools": ["read_file"]}}),
        "general",
    )
    coder = resolve_role(default_config, "coder")

    assert general.category == "general"
    assert general.source == "builtin"
    assert general.disabled is False
    assert disabled_override.disabled is False
    assert set(narrow_override.tools) == set(general.tools)
    assert {
        "read_file",
        "write_file",
        "exec",
        "web_search",
        "browser_open",
        "browser_status",
    }.issubset(general.tools)
    assert coder.category == "specialist"


def test_custom_specialist_is_resolved_only_from_config(tmp_path) -> None:
    config = _config(tmp_path, roles={
        "release-triage": {
            "description": "Triage recurring releases.",
            "system_prompt": "Check release readiness and report evidence.",
            "tools": ["read_file", "exec", "browser_status"],
        },
    })

    role = resolve_role(config, "release-triage")
    names = {item.name for item in list_roles(config)}

    assert role.source == "config"
    assert role.category == "specialist"
    assert set(role.tools) == {
        "read_file", "exec", "browser_status", "report_progress"
    }
    assert config.subagent_roles["release-triage"].tools == [
        "read_file", "exec", "browser_status"
    ]
    assert {"general", "release-triage"}.issubset(names)
    assert not hasattr(role, "created_by")
    assert not hasattr(role, "version")
    assert not hasattr(role, "status")
    assert not hasattr(role, "capabilities")


def test_role_usage_is_advisory_and_separate_from_role_definition(tmp_path) -> None:
    config = _config(tmp_path, roles={
        "release-triage": {
            "description": "Triage recurring releases.",
            "system_prompt": "Check release readiness.",
            "tools": ["read_file"],
        },
    })

    record_role_use(tmp_path, "release-triage")
    record_role_use(tmp_path, "release-triage")

    usage = role_usage(tmp_path, "release-triage")
    resolved = resolve_role(config, "release-triage")
    assert usage["runs"] == 2
    assert usage["last_used"]
    assert resolved.usage["runs"] == 2


def test_role_store_is_single_persistent_specialist_authority(tmp_path) -> None:
    config = _config(tmp_path)
    store = SubagentRoleStore(config)

    created = store.create("release-triage", {
        "description": "Triage recurring releases.",
        "system_prompt": "Check release readiness.",
        "tools": ["read_file"],
    })
    assert created["source"] == "config"
    assert created["tools"] == ["read_file", "report_progress"]
    assert config.subagent_roles["release-triage"].tools == ["read_file"]

    updated = store.update("release-triage", {
        "system_prompt": "Check release readiness and test evidence.",
        "tools": ["read_file", "exec"],
    })
    assert updated["source"] == "config"
    assert config.subagent_roles["release-triage"].tools == ["read_file", "exec"]
    assert updated["tools"] == ["read_file", "exec", "report_progress"]

    deleted = store.delete("release-triage")
    assert deleted == {"name": "release-triage", "deleted": True}
    with pytest.raises(ValueError, match="Unknown subagent role"):
        store.get("release-triage")

    with pytest.raises(ValueError, match="general.*permanent|permanent.*general"):
        store.delete("general")
    with pytest.raises(ValueError, match="general.*permanent|permanent.*general"):
        store.update("general", {"disabled": True})


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
        permission_manager=PermissionManager(Config()),
    )
    try:
        general_tools = manager._build_tools(role="general")
        coder_tools = manager._build_tools(role="coder")
        general_exposed = _exposed_tool_names(general_tools)
        coder_exposed = _exposed_tool_names(coder_tools)

        assert {"browser_open", "browser_status", "browser_handoff"}.issubset(
            general_exposed
        )
        assert "browser_status" not in coder_exposed
        assert (
            type(general_tools.get("browser_status")).__module__
            == "nanobot.agent.tools.subagent_browser"
        )
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_config_specialist_can_receive_browser_capability(tmp_path, monkeypatch) -> None:
    from nanobot.agent.model_management import ModelManagement
    from nanobot.agent.subagent import SubagentManager

    monkeypatch.setenv("NANOBOT_BROWSER_ENABLED", "true")
    config = _config(tmp_path, roles={
        "ui-checker": {
            "description": "Check recurring UI workflows.",
            "system_prompt": "Inspect the UI and report evidence.",
            "tools": ["read_file", "browser_status", "browser_snapshot"],
        },
    })
    config.permissions.specialists["ui-checker"] = PermissionPolicyConfig(
        capabilities=["workspace.read", "browser.control"],
        ceiling=["workspace.read", "browser.control"],
    )
    management = ModelManagement(config)
    permissions = PermissionManager(management.config_snapshot)
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        model_management=management,
        permission_manager=permissions,
    )
    try:
        tools = manager._build_tools(role="ui-checker")
        assert {"read_file", "browser_status", "browser_snapshot"}.issubset(
            _exposed_tool_names(tools)
        )
    finally:
        await manager.close()


def test_prompts_define_current_orchestration_and_governance() -> None:
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
    assert "Main is the user-facing control plane" in tool_contract
    assert "Prefer a matching active Specialist" in tool_contract
    assert "Use a WorkAgent" in tool_contract
    assert "permanent General" in tool_contract
    assert "`subagent`" in tool_contract
    assert "`role.list`" in tool_contract
    assert "`role.get`" in tool_contract
    assert "Children do not create further Workers" in tool_contract

    assert "## Specialist discovery" in dream
    assert "specialist_candidate" in dream
    assert "Discovery is a proposal only" in dream
    assert "Read-only analysis only" in dream
    assert "User is the highest governance authority" in dream
    assert "high-impact" in dream
    assert "dream_roles" not in dream
    assert "role_candidates.json" not in dream
