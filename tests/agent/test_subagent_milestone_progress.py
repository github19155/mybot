from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from nanobot.agent.permissions import MAIN_SUBJECT
from nanobot.agent.subagent_roles import TOOL_MODULES, resolve_role
from nanobot.agent.tools.context import RequestContext, ToolContext, request_context
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.report_progress import ReportProgressTool
from nanobot.agent.tools.subagent_browser import bind_subagent_browser_bus
from nanobot.agent.work_agent import build_work_role_definition
from nanobot.bus.outbound_events import ProgressEvent
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ToolsConfig


@pytest.mark.asyncio
async def test_report_progress_publishes_semantic_progress_event() -> None:
    bus = MessageBus()
    tool = ReportProgressTool(bus)
    request = RequestContext(
        channel="weixin",
        chat_id="user-1",
        message_id="msg-1",
        session_key="weixin:user-1",
    )

    with request_context(request):
        result = await tool.execute(message="  ✅ image   analysis complete ")

    outbound = await bus.consume_outbound()
    assert result == "Milestone reported (1/3); continue the task."
    assert outbound.channel == "weixin"
    assert outbound.chat_id == "user-1"
    assert outbound.content == "✅ image analysis complete"
    assert outbound.metadata["message_id"] == "msg-1"
    assert outbound.metadata["subagent_milestone"] is True
    assert isinstance(outbound.event, ProgressEvent)
    assert outbound.event.content == outbound.content


@pytest.mark.asyncio
async def test_report_progress_deduplicates_and_caps_each_child_run() -> None:
    bus = MessageBus()
    tool = ReportProgressTool(bus)
    request = RequestContext(channel="cli", chat_id="direct")

    with request_context(request):
        assert "1/3" in await tool.execute(message="stage one complete")
        assert "Duplicate" in await tool.execute(message="stage one complete")
        assert "2/3" in await tool.execute(message="stage two complete")
        assert "3/3" in await tool.execute(message="stage three complete")
        assert "limit reached" in await tool.execute(message="stage four complete")

    assert bus.outbound_size == 3


@pytest.mark.asyncio
async def test_report_progress_delivery_failure_does_not_fail_child() -> None:
    class BrokenBus:
        async def publish_outbound(self, _msg: object) -> None:
            raise RuntimeError("transport down")

    tool = ReportProgressTool(cast(MessageBus, BrokenBus()))
    request = RequestContext(channel="test", chat_id="chat")

    with request_context(request):
        result = await tool.execute(message="analysis complete")

    assert result == "Milestone delivery failed; continue the task and still produce the final result."


@pytest.mark.asyncio
async def test_report_progress_uses_parent_bus_binding(tmp_path: Path) -> None:
    bus = MessageBus()
    bind_subagent_browser_bus(tmp_path, bus)
    tool = ReportProgressTool.create(
        ToolContext(config=ToolsConfig(), workspace=str(tmp_path))
    )
    request = RequestContext(channel="test", chat_id="bound")

    with request_context(request):
        result = await tool.execute(message="bound route works")

    outbound = await bus.consume_outbound()
    assert "1/3" in result
    assert outbound.chat_id == "bound"


def test_milestone_tool_is_internal_to_main_but_available_to_workers() -> None:
    registry = ToolRegistry(permission_subject=MAIN_SUBJECT)
    registry.register(ReportProgressTool(MessageBus()))

    assert "report_progress" in registry.tool_names
    assert all(
        schema.get("function", {}).get("name") != "report_progress"
        for schema in registry.get_definitions()
    )
    assert TOOL_MODULES["report_progress"] == "report_progress"
    assert "report_progress" in resolve_role(None, "general").tools
    assert "report_progress" in resolve_role(None, "researcher").tools

    work = build_work_role_definition(
        {"tools": ["read_file"]},
        tools=[],
    )
    assert work.tools == ("report_progress",)
