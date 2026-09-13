"""Durable extension of the existing in-process SubagentManager."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.subagent import SubagentManager, SubagentStatus
from nanobot.agent.subagent_job_ledger import (
    DEFAULT_SUBAGENT_HISTORY_LIMIT,
    SubagentJobLedger,
)
from nanobot.providers.base import LLMUsage


class DurableSubagentManager(SubagentManager):
    """Persist existing SubagentStatus lifecycle transitions and recent history.

    This is deliberately not a scheduler and does not resume work. The live
    asyncio task dictionaries remain authoritative while the process is alive.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        root = Path(self.workspace).expanduser().resolve()
        self._job_ledger = SubagentJobLedger(root / "runtime" / "subagent_jobs.db")
        self._restore_durable_history()

    def _restore_durable_history(self) -> None:
        """Load recent terminal rows into the manager's existing bounded history."""
        rows = self._job_ledger.load_recent(limit=DEFAULT_SUBAGENT_HISTORY_LIMIT)
        # load_recent is newest-first; deque history keeps chronological order.
        for row in reversed(rows):
            status = self._status_from_durable_row(row)
            if status.state in ("completed", "failed", "stopped", "interrupted"):
                self._finished.append(status)

    @staticmethod
    def _status_from_durable_row(row: dict[str, Any]) -> SubagentStatus:
        now_ms = int(time.time() * 1000)
        started_at_ms = int(row.get("started_at_ms") or now_ms)
        age_s = max(0.0, (now_ms - started_at_ms) / 1000.0)
        input_tokens = int(row.get("usage_input_tokens") or 0)
        output_tokens = int(row.get("usage_output_tokens") or 0)
        total_tokens = max(
            int(row.get("usage_total_tokens") or 0),
            input_tokens + output_tokens,
        )
        usage = None
        if total_tokens:
            usage = LLMUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                reported_tokens=total_tokens,
                estimated_tokens=0,
            )
        tool_events = [
            item for item in row.get("tool_events", [])
            if isinstance(item, dict)
        ]
        return SubagentStatus(
            task_id=str(row["task_id"]),
            label=str(row.get("label") or ""),
            task_description=str(row.get("task_description") or ""),
            started_at=time.monotonic() - age_s,
            phase=str(row.get("phase") or "interrupted"),
            iteration=int(row.get("iteration") or 0),
            tool_events=tool_events,
            usage=usage,
            stop_reason=row.get("stop_reason"),
            error=row.get("error"),
            role=str(row.get("role") or "general"),
            model=row.get("model"),
            model_preset=row.get("model_preset"),
            origin_channel=row.get("origin_channel"),
            origin_chat_id=row.get("origin_chat_id"),
            session_key=row.get("session_key"),
            origin_message_id=row.get("origin_message_id"),
            started_at_ms=started_at_ms,
            ended_at_ms=row.get("ended_at_ms"),
            state=str(row.get("state") or "interrupted"),
            thinking=row.get("thinking"),
            context=str(row.get("context_mode") or "fresh"),
            timeout_seconds=row.get("timeout_seconds"),
            completion_delivered=bool(row.get("completion_delivered")),
            completion_delivery_error=row.get("completion_delivery_error"),
            final_output=row.get("final_output"),
        )

    def _persist_status(self, status: SubagentStatus | None) -> None:
        if status is None:
            return
        try:
            self._job_ledger.persist(status)
        except Exception as exc:
            # Persistence is observability/durability only; never fail live work.
            logger.warning("Subagent [{}] job ledger write failed: {}", status.task_id, exc)

    async def _run_subagent(self, *args: Any, **kwargs: Any) -> str:
        # The fifth positional parameter is the existing SubagentStatus contract.
        status = args[4] if len(args) > 4 and isinstance(args[4], SubagentStatus) else None
        self._persist_status(status)
        return await super()._run_subagent(*args, **kwargs)

    async def _admit(self, task_id: str) -> None:
        await super()._admit(task_id)
        self._persist_status(self._task_statuses.get(task_id))

    def _record_finished(self, status: SubagentStatus | None) -> None:
        super()._record_finished(status)
        self._persist_status(status)
