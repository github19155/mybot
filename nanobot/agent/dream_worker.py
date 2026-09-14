"""Native background execution loop for Dream."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.dream import build_dream_tools
from nanobot.agent.dream_runtime import DreamRuntimeController, resolve_dream_runtime
from nanobot.llm_usage.context import llm_usage_source

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


async def _silent_progress(*_args: Any, **_kwargs: Any) -> None:
    return None


def _completed(response: object | None) -> bool:
    metadata = getattr(response, "metadata", None)
    return isinstance(metadata, dict) and metadata.get("_stop_reason") == "completed"


async def run_dream_worker(agent: "AgentLoop") -> None:
    """Run Dream as one lowest-priority background worker.

    The worker continuously reloads Dream policy, while Dream's own runtime
    domain owns model selection. Failures remain isolated from foreground work.
    """
    if agent.model_management is None or agent.permissions is None:
        logger.info("Dream disabled: runtime management is unavailable")
        return

    while True:
        config = agent.model_management.config_snapshot()
        dream_config = config.agents.defaults.dream
        poll_seconds = dream_config.poll_interval_seconds

        try:
            if dream_config.enabled:
                controller = DreamRuntimeController(
                    agent.workspace,
                    dream_config,
                    agent.permissions,
                )
                batch = controller.prepare(agent.context.memory)
                if batch is not None:
                    runtime = await resolve_dream_runtime(
                        batch.run.workload,
                        config=dream_config,
                        runtime_resolver=agent.runtime_resolver,
                        fleet_recommend=agent.model_management.fleet_recommend,
                    )
                    with llm_usage_source("dream"):
                        response = await agent.process_direct(
                            batch.prompt,
                            session_key=f"dream:{batch.run.run_id}",
                            channel="dream",
                            ephemeral=True,
                            tools=build_dream_tools(agent.workspace, agent.permissions),
                            on_progress=_silent_progress,
                            runtime=runtime,
                        )
                    if not _completed(response) or not getattr(response, "content", None):
                        logger.warning(
                            "Dream run {} did not complete; source revision {} remains pending",
                            batch.run.run_id,
                            batch.run.source_revision,
                        )
                    else:
                        controller.store_result(
                            response.content,
                            run=batch.run,
                            runtime=runtime,
                        )
                        agent.context.memory.set_last_dream_cursor(batch.run.source_revision)
                        agent.context.memory.compact_history()
                        logger.info(
                            "Dream run {} completed at source revision {}",
                            batch.run.run_id,
                            batch.run.source_revision,
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Dream background run failed")

        await asyncio.sleep(poll_seconds)