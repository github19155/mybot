"""Main-agent self context inspection and compaction tool."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context

if TYPE_CHECKING:
    from nanobot.agent.tools.context import ToolContext
    from nanobot.agent.tools.runtime_control import RuntimeControl


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["status", "compact"]},
    },
    "required": ["action"],
    "additionalProperties": False,
})
class ContextControlTool(Tool):
    """Inspect or compact only the current Main session."""

    _scopes = {"core"}

    def __init__(self, control: RuntimeControl) -> None:
        self._control = control

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.runtime_control is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.runtime_control is None:
            raise RuntimeError("context requires runtime control")
        return cls(ctx.runtime_control)

    @property
    def name(self) -> str:
        return "context"

    @property
    def description(self) -> str:
        return (
            "Inspect or compact your current conversation context. "
            "status returns machine-readable token pressure, replay/history counts, and a recommendation. "
            "compact creates a summary checkpoint using the existing session Consolidator; it never edits the "
            "current AgentRunner request in flight, so the reduced context takes effect on later model requests. "
            "For long tasks, consider compacting at >=60%, prefer it at >=75%, and strongly prefer it at >=85%."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, **kwargs: Any) -> str | ToolResult:
        request = current_request_context()
        if request is None or not request.session_key:
            return ToolResult.error("context is only available inside a persisted Main session")
        action = kwargs.get("action")
        if action == "status":
            result = self._control.context_status(
                request.session_key,
                runtime=request.runtime,
            )
            return json.dumps(result, ensure_ascii=False)
        if action == "compact":
            result = await self._control.context_compact(
                request.session_key,
                runtime=request.runtime,
            )
            text = json.dumps(result, ensure_ascii=False)
            return ToolResult.error(text) if result.get("status") == "error" else text
        return ToolResult.error("unknown context action")
