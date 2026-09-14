from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.permissions import PermissionManager
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.subagent_roles import resolve_role
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.subagent import SubagentTool, _SUBAGENT_PARAMETERS
from nanobot.agent.work_agent import build_work_role_definition
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config


def _models() -> dict[str, dict[str, object]]:
    return {
        "main": {"displayName": "Main", "provider": "test", "model": "main/model"},
        "role-model": {"displayName": "Role", "provider": "test", "model": "role/model"},
        "task-model": {"displayName": "Task", "provider": "test", "model": "task/model"},
    }


def _manager(tmp_path: Path, *, resolver=None, config: Config | None = None) -> SubagentManager:
    cfg = config or Config()
    management = SimpleNamespace(config=cfg, config_snapshot=lambda: cfg) if config else None
    return SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        permission_manager=PermissionManager(cfg),
        runtime_resolver=resolver,
        model_management=management,
    )


def test_run_model_id_overrides_persistent_role_model(tmp_path: Path) -> None:
    config = Config(
        models=_models(),
        subagentRoles={"coder": {"modelId": "role-model"}},
    )
    role = resolve_role(config, "coder")
    parent_runtime = object()
    selected_runtime = object()
    resolver = MagicMock()
    resolver.resolve_selection.return_value = selected_runtime
    manager = _manager(tmp_path, resolver=resolver, config=config)

    assert manager._resolve_task_runtime(
        parent_runtime,
        role_definition=role,
        model_id="task-model",
    ) is selected_runtime
    resolver.resolve_selection.assert_called_once_with(
        parent_runtime,
        model_id="task-model",
    )


def test_role_model_id_is_fallback_when_run_has_no_model_id(tmp_path: Path) -> None:
    config = Config(
        models=_models(),
        subagentRoles={"coder": {"modelId": "role-model"}},
    )
    role = resolve_role(config, "coder")
    parent_runtime = object()
    selected_runtime = object()
    resolver = MagicMock()
    resolver.resolve_selection.return_value = selected_runtime
    manager = _manager(tmp_path, resolver=resolver, config=config)

    assert manager._resolve_task_runtime(
        parent_runtime,
        role_definition=role,
    ) is selected_runtime
    resolver.resolve_selection.assert_called_once_with(
        parent_runtime,
        model_id="role-model",
    )


def test_current_runtime_is_reused_when_run_and_role_omit_model_id(tmp_path: Path) -> None:
    role = resolve_role(Config(), "coder")
    parent_runtime = object()
    resolver = MagicMock()
    manager = _manager(tmp_path, resolver=resolver)

    assert manager._resolve_task_runtime(parent_runtime, role_definition=role) is parent_runtime
    resolver.resolve_selection.assert_not_called()


def test_work_agent_without_model_id_inherits_current_main_runtime(tmp_path: Path) -> None:
    parent_runtime = object()
    resolver = MagicMock()
    manager = _manager(tmp_path, resolver=resolver)
    role = build_work_role_definition(
        {
            "name": "general",
            "tools": ["read_file"],
            "model_id": "general-tuned",
        }
    )

    assert role.model_id is None
    assert manager._resolve_task_runtime(parent_runtime, role_definition=role) is parent_runtime
    resolver.resolve_selection.assert_not_called()


def test_work_agent_explicit_model_id_uses_run_override(tmp_path: Path) -> None:
    parent_runtime = object()
    selected_runtime = object()
    resolver = MagicMock()
    resolver.resolve_selection.return_value = selected_runtime
    manager = _manager(tmp_path, resolver=resolver)
    role = build_work_role_definition(
        {"name": "general", "tools": ["read_file"], "model_id": "general-tuned"},
    )

    assert role.model_id is None
    assert manager._resolve_task_runtime(
        parent_runtime,
        role_definition=role,
        model_id="task-model",
    ) is selected_runtime
    resolver.resolve_selection.assert_called_once_with(
        parent_runtime,
        model_id="task-model",
    )


@pytest.mark.asyncio
async def test_role_plus_explicit_model_id_is_allowed_and_not_persisted(tmp_path: Path) -> None:
    config = Config(
        models=_models(),
        subagentRoles={"coder": {"modelId": "role-model"}},
    )
    manager = _manager(tmp_path, config=config)
    original = manager.role_get("coder")
    manager.spawn = AsyncMock(return_value="queued (id: task-1)")
    tool = SubagentTool(manager)

    with request_context(RequestContext(
        channel="test",
        chat_id="c1",
        session_key="test:c1",
        runtime=object(),
    )):
        result = await tool.execute(
            action="run",
            task="use a faster model once",
            role="coder",
            model_id="task-model",
        )

    assert "id:" in result
    manager.spawn.assert_awaited_once()
    kwargs = manager.spawn.await_args.kwargs
    assert kwargs["role"] == "coder"
    assert kwargs["model_id"] == "task-model"
    assert manager.role_get("coder")["model_id"] == "role-model"
    assert manager.role_get("coder") == original


def test_subagent_tool_exposes_only_canonical_model_identity() -> None:
    properties = _SUBAGENT_PARAMETERS["properties"]

    assert "model_id" in properties
    assert "model" not in properties
    assert "model_preset" not in properties
