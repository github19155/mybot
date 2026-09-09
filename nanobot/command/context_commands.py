"""Slash commands for self context inspection and manual compaction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nanobot.agent.context_management import AgentContextControl, ContextSnapshot
from nanobot.bus.events import OutboundMessage

if TYPE_CHECKING:
    from nanobot.command.router import CommandContext, CommandRouter


def _format_snapshot(snapshot: ContextSnapshot) -> str:
    if snapshot.usage_ratio is None:
        usage = "unknown"
    else:
        usage = f"{snapshot.usage_ratio * 100:.1f}%"
    return "\n".join([
        "## Context",
        f"- Usage: {usage}",
        f"- Estimated input: {snapshot.estimated_tokens} tokens",
        f"- Input budget: {snapshot.input_budget_tokens} tokens",
        f"- Context window: {snapshot.context_window_tokens} tokens",
        f"- Replayable messages: {snapshot.replayable_messages}",
        f"- Archived messages: {snapshot.archived_messages}",
        f"- Unarchived messages: {snapshot.unarchived_messages}",
        f"- Summary checkpoint: {'yes' if snapshot.has_summary else 'no'}",
        f"- Can compact: {'yes' if snapshot.can_compact else 'no'}",
        f"- Recommendation: {snapshot.recommendation}",
    ])


async def cmd_context(ctx: CommandContext) -> OutboundMessage:
    session = ctx.session or ctx.loop.sessions.get_or_create(ctx.key)
    runtime = ctx.runtime or ctx.loop.runtime_for_session(session)
    snapshot = AgentContextControl(ctx.loop).status(ctx.key, runtime=runtime)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=_format_snapshot(snapshot),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_compact(ctx: CommandContext) -> OutboundMessage:
    session = ctx.session or ctx.loop.sessions.get_or_create(ctx.key)
    runtime = ctx.runtime or ctx.loop.runtime_for_session(session)
    result = await AgentContextControl(ctx.loop).compact(ctx.key, runtime=runtime)
    status = result.get("status")
    before = result.get("before") if isinstance(result.get("before"), dict) else {}
    after = result.get("after") if isinstance(result.get("after"), dict) else {}
    if status == "compacted":
        before_pct = before.get("usage_percent")
        after_pct = after.get("usage_percent")
        content = (
            "Context compacted.\n"
            f"- Before: {before_pct if before_pct is not None else 'unknown'}%\n"
            f"- After: {after_pct if after_pct is not None else 'unknown'}%\n"
            "- Full persisted history is retained; later model requests use the checkpoint plus recent replay."
        )
    elif status == "noop":
        content = "Context does not need manual compaction yet."
    else:
        content = "Context compaction failed; the existing session history was left intact."
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def register_context_commands(router: CommandRouter) -> None:
    router.exact("/context", cmd_context)
    router.exact("/compact", cmd_compact)
