"""Slash commands for the Dream background cognition control plane."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from nanobot.bus.events import OutboundMessage
from nanobot.command.router import CommandContext, CommandRouter

if TYPE_CHECKING:
    from nanobot.agent.dream import DreamTriggerController


def _controller(ctx: CommandContext) -> DreamTriggerController:
    from nanobot.agent.dream import DreamTriggerController

    if ctx.loop.model_management is None or ctx.loop.permissions is None:
        raise RuntimeError("Dream commands require runtime and permission management")
    config = ctx.loop.model_management.config_snapshot().agents.defaults.dream
    return DreamTriggerController(ctx.loop.workspace, config, ctx.loop.permissions)


def _metadata(ctx: CommandContext) -> dict[str, Any]:
    return {**dict(ctx.msg.metadata or {}), "render_as": "text"}


async def cmd_dream(ctx: CommandContext) -> OutboundMessage:
    """Queue a manual Dream request for the background worker."""
    from nanobot.agent.dream import DREAM_CONSOLIDATION

    controller = _controller(ctx)
    if not controller.config.enabled:
        content = "Dream is disabled. Enable `agents.defaults.dream.enabled` first."
    else:
        controller.request(DREAM_CONSOLIDATION)
        content = (
            "Dream request queued. The background worker will run it when "
            "unprocessed history is available and runtime admission succeeds."
        )
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata=_metadata(ctx),
    )


def _read_results(controller: DreamTriggerController) -> list[dict[str, object]]:
    try:
        lines = controller.results_path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return []
    results: list[dict[str, object]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            results.append(cast(dict[str, object], value))
    return results


def _format_result(result: dict[str, object]) -> str:
    summary = str(result.get("summary") or "(no summary)").strip()
    run_id = str(result.get("run_id") or "unknown")
    workload = str(result.get("workload") or "unknown")
    revision = result.get("source_revision")
    proposals = result.get("proposals")
    proposal_rows = cast(list[object], proposals) if isinstance(proposals, list) else []
    pending = 0
    awaiting_user = 0
    for raw in proposal_rows:
        if not isinstance(raw, dict):
            continue
        state = str(cast(dict[str, object], raw).get("state") or "")
        if state == "awaiting_user_approval":
            awaiting_user += 1
        elif state == "pending":
            pending += 1
    return "\n".join(
        [
            f"### {workload}",
            f"- Run: `{run_id}`",
            f"- Source revision: {revision}",
            f"- Proposals: {len(proposal_rows)} ({pending} pending, {awaiting_user} awaiting user approval)",
            "",
            summary,
        ]
    )


async def cmd_dream_log(ctx: CommandContext) -> OutboundMessage:
    """Show recent validated Dream audit records."""
    args = ctx.args.strip()
    count = 1
    if args:
        try:
            count = int(args)
        except ValueError:
            count = 0
        if count < 1 or count > 10:
            return OutboundMessage(
                channel=ctx.msg.channel,
                chat_id=ctx.msg.chat_id,
                content="Usage: /dream-log [1-10]",
                metadata=_metadata(ctx),
            )

    results = _read_results(_controller(ctx))
    if not results:
        content = (
            "Dream has no validated audit records yet. Run `/dream` to queue a manual "
            "request, or wait for the adaptive background worker."
        )
    else:
        selected = results[-count:]
        selected.reverse()
        content = "## Dream Audit\n\n" + "\n\n".join(
            _format_result(result) for result in selected
        )
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata=_metadata(ctx),
    )


def register_dream_commands(router: CommandRouter) -> None:
    """Register the canonical Dream control-plane commands."""
    router.exact("/dream", cmd_dream)
    router.exact("/dream-log", cmd_dream_log)
    router.prefix("/dream-log ", cmd_dream_log)
