from __future__ import annotations

import json

import pytest

from nanobot.agent.permissions import PermissionManager
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.agent.tools.filesystem import FileToolsConfig
from nanobot.agent.tools.subagent import SubagentTool
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config, ToolsConfig


def _manager(tmp_path, config: Config | None = None) -> SubagentManager:
    config = config or Config()
    return SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        permission_manager=PermissionManager(config),
        tools_config=config.tools,
    )


@pytest.mark.asyncio
async def test_role_get_separates_declared_allowed_and_available_tools(tmp_path) -> None:
    manager = _manager(tmp_path)
    tool = SubagentTool(manager)
    token = bind_request_context(RequestContext(
        channel="test",
        chat_id="chat",
        session_key="test:chat",
        allowed_tools=frozenset({"subagent", "read_file", "exec", "image_analyze"}),
    ))
    try:
        payload = json.loads(await tool.execute(action="role.get", role="researcher"))
    finally:
        reset_request_context(token)
        await manager.close()

    assert "tools" not in payload
    assert "read_file" in payload["declared_tools"]
    assert "exec" in payload["declared_tools"]
    assert "read_file" in payload["allowed_tools"]
    assert "exec" not in payload["allowed_tools"]
    assert "read_file" in payload["available_tools"]
    assert "image_analyze" not in payload["available_tools"]
    assert "generate_image" in payload["worker_tool_catalog"]


@pytest.mark.asyncio
async def test_role_get_does_not_report_config_disabled_tools_as_available(tmp_path) -> None:
    config = Config(tools=ToolsConfig(file=FileToolsConfig(enable=False)))
    manager = _manager(tmp_path, config)
    tool = SubagentTool(manager)
    token = bind_request_context(RequestContext(
        channel="test",
        chat_id="chat",
        session_key="test:chat",
        allowed_tools=frozenset({"read_file", "web_search"}),
    ))
    try:
        payload = json.loads(await tool.execute(action="role.get", role="researcher"))
    finally:
        reset_request_context(token)
        await manager.close()

    assert "read_file" in payload["declared_tools"]
    assert "read_file" in payload["allowed_tools"]
    assert "read_file" not in payload["available_tools"]


def test_generate_image_uses_image_generate_permission_not_generic_tool_use() -> None:
    manager = PermissionManager(Config())

    assert manager.tool_capability("generate_image") == "image.generate"
