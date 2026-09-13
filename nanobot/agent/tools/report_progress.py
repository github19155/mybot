"""User-visible semantic milestone reporting for delegated work."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.agent.tools.schema import StringSchema, tool_parameters_schema
from nanobot.agent.tools.subagent_browser import subagent_bus_for_workspace
from nanobot.bus.events import OutboundMessage
from nanobot.bus.outbound_events import ProgressEvent

if TYPE_CHECKING:
    from nanobot.agent.tools.context import ToolContext
    from nanobot.bus.queue import MessageBus


_MAX_MILESTONES = 3
_PARAMETERS = tool_parameters_schema(
    message=StringSchema(
        "Short completed milestone for the user, without a leading checkmark or generic status text",
        min_length=1,
        max_length=180,
    ),
    required=["message"],
)


def _normalize_message(message: str) -> str:
    normalized = " ".join(message.split()).strip()
    while normalized.startswith(("✅", "✓", "✔")):
        normalized = normalized[1:].lstrip()
    return normalized


@tool_parameters(_PARAMETERS)
class ReportProgressTool(Tool):
    """Report a bounded, meaningful milestone while a subagent keeps working."""

    # Core registration keeps the name in Main's internal allowed-tool snapshot,
    # while ToolRegistry's Main orchestrator gate keeps it hidden from the Main
    # model. The subagent scope exposes the actual worker-facing capability.
    _scopes = {"core", "subagent"}

    def __init__(self, bus: MessageBus | None) -> None:
        self._bus = bus
        self._reported: set[str] = set()
        self._count = 0

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(ctx.bus or subagent_bus_for_workspace(ctx.workspace))

    @property
    def name(self) -> str:
        return "report_progress"

    @property
    def description(self) -> str:
        return (
            "Report one concise, user-visible milestone after a meaningful stage of delegated work "
            "has actually completed. Use only for useful stage boundaries on longer tasks, not for "
            "routine tool calls, plans, heartbeats, or 'still working' updates. At most three unique "
            "milestones are delivered per child run. Keep working after reporting; the final answer "
            "is delivered separately. Progress delivery is best-effort and must never block the task."
        )

    @property
    def concurrency_safe(self) -> bool:
        return False

    async def execute(self, message: str, **_: Any) -> str:
        request = current_request_context()
        if request is None:
            return "Milestone progress unavailable without an active request; continue the task."

        normalized = _normalize_message(message)
        if not normalized:
            return "Milestone was empty after normalization; continue the task without reporting it."

        fingerprint = normalized.casefold()
        if fingerprint in self._reported:
            return "Duplicate milestone skipped; continue the task."
        if self._count >= _MAX_MILESTONES:
            return "Milestone limit reached; continue the task and reserve remaining detail for the final result."

        # Reserve the slot before the await so concurrent tool execution cannot
        # duplicate or exceed the per-run cap. Delivery itself stays best-effort.
        self._reported.add(fingerprint)
        self._count += 1

        if self._bus is None:
            return "Milestone recorded but no outbound route is available; continue the task."

        content = f"✅ {normalized}"
        metadata: dict[str, Any] = {
            "subagent_milestone": True,
        }
        if request.message_id:
            metadata["message_id"] = request.message_id

        try:
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=request.channel,
                    chat_id=request.chat_id,
                    content=content,
                    metadata=metadata,
                    event=ProgressEvent(content=content),
                )
            )
        except Exception as exc:
            logger.warning("Subagent milestone delivery failed: {}", exc)
            return "Milestone delivery failed; continue the task and still produce the final result."

        return f"Milestone reported ({self._count}/{_MAX_MILESTONES}); continue the task."
