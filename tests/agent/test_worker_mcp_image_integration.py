from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.permissions import PermissionManager
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry, is_tool_error_result
from nanobot.agent.work_agent import build_work_role_definition
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config, ToolsConfig


class _ConnectedMCPTool(Tool):
    @property
    def name(self) -> str:
        return "mcp_echo"

    @property
    def description(self) -> str:
        return "Already-connected MCP test tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "mcp-ok"


def _manager(tmp_path: Path, config: Config | None = None) -> SubagentManager:
    config = config or Config()
    return SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        tools_config=config.tools,
        permission_manager=PermissionManager(config),
    )


@pytest.mark.asyncio
async def test_general_reuses_connected_mcp_tool_without_owning_connection(tmp_path):
    manager = _manager(tmp_path)
    system_tools = ToolRegistry()
    connected = _ConnectedMCPTool()
    system_tools.register(connected)
    manager.system_tools = system_tools

    tools = manager._build_tools(
        role="general",
        allowed_tools={*system_tools.tool_names, "read_file"},
    )

    assert tools.get("mcp_echo") is connected
    assert await tools.execute("mcp_echo", {}) == "mcp-ok"
    assert "mcp_echo" in tools.tool_names
    await manager.close()


@pytest.mark.asyncio
async def test_specialist_does_not_inherit_general_external_authority(tmp_path):
    manager = _manager(tmp_path)
    system_tools = ToolRegistry()
    system_tools.register(_ConnectedMCPTool())
    manager.system_tools = system_tools

    tools = manager._build_tools(
        role="coder",
        allowed_tools={"mcp_echo", "read_file", "write_file", "exec"},
    )

    assert "mcp_echo" not in tools.tool_names
    assert is_tool_error_result(await tools.execute("mcp_echo", {}))
    await manager.close()


@pytest.mark.asyncio
async def test_work_agent_can_receive_explicit_connected_mcp_tool(tmp_path):
    manager = _manager(tmp_path)
    system_tools = ToolRegistry()
    connected = _ConnectedMCPTool()
    system_tools.register(connected)
    manager.system_tools = system_tools

    role = build_work_role_definition(
        manager.role_get("general"),
        tools=["mcp_echo"],
    )
    tools = manager._build_tools(
        role="work",
        role_definition=role,
        allowed_tools={"mcp_echo"},
    )

    assert tools.get("mcp_echo") is connected
    assert await tools.execute("mcp_echo", {}) == "mcp-ok"
    await manager.close()


def test_general_and_work_agent_receive_image_generation_but_specialist_does_not(tmp_path):
    config = Config()
    tools_config = ToolsConfig()
    tools_config.image_generation.enabled = True
    config.tools = tools_config
    manager = _manager(tmp_path, config)
    manager._image_generation_provider_configs = MagicMock(  # type: ignore[method-assign]
        return_value={"openrouter": MagicMock()}
    )

    general = manager._build_tools(role="general")
    assert "generate_image" in general.tool_names

    work_role = build_work_role_definition(
        manager.role_get("general"),
        tools=["generate_image"],
    )
    work = manager._build_tools(role="work", role_definition=work_role)
    assert "generate_image" in work.tool_names

    coder = manager._build_tools(role="coder")
    assert "generate_image" not in coder.tool_names
