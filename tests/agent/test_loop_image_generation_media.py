from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.message import MessageTool
from nanobot.bus.events import OutboundMessage


@pytest.mark.asyncio
async def test_generated_worker_artifact_can_be_delivered_by_main_message_tool(
    tmp_path: Path,
) -> None:
    """Main delivers a Worker-generated artifact through its control-plane message tool."""
    artifact = tmp_path / "generated" / "image.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"generated-image")
    send = AsyncMock()
    tool = MessageTool(
        send_callback=send,
        workspace=tmp_path,
        restrict_to_workspace=True,
    )

    with request_context(RequestContext(
        channel="websocket",
        chat_id="chat-image",
        session_key="websocket:chat-image",
        allowed_tools=frozenset({"message"}),
    )):
        result = await tool.execute(
            content="Generated image",
            media=[str(artifact)],
        )

    assert result == "Message sent to websocket:chat-image with 1 attachments"
    send.assert_awaited_once()
    outbound = send.await_args.args[0]
    assert isinstance(outbound, OutboundMessage)
    assert outbound.channel == "websocket"
    assert outbound.chat_id == "chat-image"
    assert outbound.media == [str(artifact.resolve())]
