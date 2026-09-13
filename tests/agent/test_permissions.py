from __future__ import annotations

import pytest

from nanobot.agent.permissions import (
    PermissionDeniedError,
    PermissionManager,
    current_permission_allowed,
)
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import Config
from nanobot.permission_config import PermissionPolicyConfig
from nanobot.permission_types import (
    BROWSER_CONTROL,
    GOAL_MUTATE,
    MAIN_SUBJECT,
    SPECIALIST_MANAGE,
    WORKSPACE_READ,
    WORKSPACE_WRITE,
)


class _WriteTool(Tool):
    _scopes = {"core", "orchestrator"}

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "test write tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self, **kwargs):
        return "wrote"


class _ReadTool(Tool):
    _scopes = {"core", "orchestrator"}

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "test read tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self, **kwargs):
        return "read"


def test_policy_rejects_unknown_capability() -> None:
    with pytest.raises(ValueError, match="unknown capabilities"):
        PermissionPolicyConfig(capabilities=["made.up"], ceiling=["made.up"])


def test_policy_rejects_grant_above_ceiling() -> None:
    with pytest.raises(ValueError, match="exceed privilege ceiling"):
        PermissionPolicyConfig(
            capabilities=[WORKSPACE_WRITE],
            ceiling=[WORKSPACE_READ],
        )


def test_specialist_default_is_low_privilege() -> None:
    permissions = PermissionManager(Config())
    subject = permissions.specialist_subject("new-role")

    assert permissions.allowed(subject, WORKSPACE_READ)
    assert not permissions.allowed(subject, WORKSPACE_WRITE)
    assert permissions.decision(subject, WORKSPACE_WRITE).within_ceiling


def test_temporary_grant_never_exceeds_ceiling() -> None:
    config = Config()
    permissions = PermissionManager(config)

    assert not permissions.allowed(MAIN_SUBJECT, GOAL_MUTATE)
    with permissions.grant_scope(MAIN_SUBJECT, (GOAL_MUTATE,)):
        assert permissions.allowed(MAIN_SUBJECT, GOAL_MUTATE)
        assert current_permission_allowed(GOAL_MUTATE)
    assert not permissions.allowed(MAIN_SUBJECT, GOAL_MUTATE)

    config.permissions.main.ceiling.remove(BROWSER_CONTROL)
    with pytest.raises(PermissionDeniedError, match="exceeds privilege ceiling"):
        with permissions.grant_scope(MAIN_SUBJECT, (BROWSER_CONTROL,)):
            pass


def test_tool_registry_hides_and_blocks_denied_capability() -> None:
    config = Config()
    config.permissions.main.capabilities.remove(WORKSPACE_WRITE)
    permissions = PermissionManager(config)
    registry = ToolRegistry(
        permission_manager=permissions,
        permission_subject=MAIN_SUBJECT,
    )
    registry.register(_ReadTool())
    registry.register(_WriteTool())

    schemas = registry.get_definitions()
    names = {schema["function"]["name"] for schema in schemas}
    assert "read_file" in names
    assert "write_file" not in names

    tool, _, error = registry.prepare_call("write_file", {})
    assert tool is None
    assert error is not None
    assert "permission denied" in error


def test_capability_approval_policy_is_config_driven() -> None:
    permissions = PermissionManager(Config())

    assert permissions.requires_user_approval((SPECIALIST_MANAGE,))
    assert not permissions.requires_user_approval((WORKSPACE_WRITE,))


def test_registry_subset_preserves_permission_authority() -> None:
    config = Config()
    config.permissions.main.capabilities.remove(WORKSPACE_WRITE)
    permissions = PermissionManager(config)
    registry = ToolRegistry(permission_manager=permissions, permission_subject=MAIN_SUBJECT)
    registry.register(_ReadTool())
    registry.register(_WriteTool())

    subset = registry.without(("read_file",))

    assert subset.permission_manager is permissions
    assert subset.permission_subject == MAIN_SUBJECT
    assert subset.get_definitions() == []
    _tool, _params, error = subset.prepare_call("write_file", {})
    assert error is not None and "permission denied" in error
