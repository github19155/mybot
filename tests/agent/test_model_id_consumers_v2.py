from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from nanobot.agent.permissions import PermissionManager
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.subagent import _SUBAGENT_PARAMETERS
from nanobot.agent.work_agent import build_work_role_definition
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config


def _manager(tmp_path: Path, *, resolver=None) -> SubagentManager:
    return SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        permission_manager=PermissionManager(Config()),
        runtime_resolver=resolver,
    )


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


def test_work_agent_explicit_model_id_uses_canonical_selection(tmp_path: Path) -> None:
    parent_runtime = object()
    selected_runtime = object()
    resolver = MagicMock()
    resolver.resolve_selection.return_value = selected_runtime
    manager = _manager(tmp_path, resolver=resolver)
    role = build_work_role_definition(
        {"name": "general", "tools": ["read_file"], "model_id": "general-tuned"},
        model_id="work-model",
    )

    assert manager._resolve_task_runtime(parent_runtime, role_definition=role) is selected_runtime
    resolver.resolve_selection.assert_called_once_with(
        parent_runtime,
        model_id="work-model",
    )


def test_subagent_tool_exposes_only_canonical_model_identity() -> None:
    properties = _SUBAGENT_PARAMETERS["properties"]

    assert "model_id" in properties
    assert "model" not in properties
    assert "model_preset" not in properties
