"""Durable lifecycle ledger for existing Subagent status monitoring."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_SUBAGENT_JOB_RETENTION_DAYS = 30
DEFAULT_SUBAGENT_HISTORY_LIMIT = 50
_ACTIVE_STATES = ("queued", "running")
_TERMINAL_STATES = ("completed", "failed", "stopped", "interrupted")


class SubagentJobLedger:
    """Persist Subagent lifecycle snapshots without becoming a second scheduler."""

    def __init__(
        self,
        path: Path,
        *,
        retention_days: int = DEFAULT_SUBAGENT_JOB_RETENTION_DAYS,
    ) -> None:
        self.path = path.expanduser().resolve()
        self.retention_days = max(1, int(retention_days))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        now_ms = int(time.time() * 1000)
        self.recover_interrupted(now_ms=now_ms)
        self.cleanup(now_ms=now_ms)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    task_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    task_description TEXT NOT NULL,
                    state TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    iteration INTEGER NOT NULL DEFAULT 0,
                    role TEXT NOT NULL,
                    model TEXT,
                    model_preset TEXT,
                    thinking TEXT,
                    context_mode TEXT NOT NULL,
                    timeout_seconds REAL,
                    origin_channel TEXT,
                    origin_chat_id TEXT,
                    session_key TEXT,
                    origin_message_id TEXT,
                    started_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    ended_at_ms INTEGER,
                    stop_reason TEXT,
                    error TEXT,
                    completion_delivered INTEGER NOT NULL DEFAULT 0,
                    completion_delivery_error TEXT,
                    final_output TEXT,
                    usage_input_tokens INTEGER NOT NULL DEFAULT 0,
                    usage_output_tokens INTEGER NOT NULL DEFAULT 0,
                    usage_total_tokens INTEGER NOT NULL DEFAULT 0,
                    tool_events_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_subagent_jobs_session_started "
                "ON jobs(session_key, started_at_ms DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_subagent_jobs_state_updated "
                "ON jobs(state, updated_at_ms)"
            )

    def persist(self, status: Any) -> None:
        """Upsert one existing SubagentStatus-like object."""
        now_ms = int(time.time() * 1000)
        state = str(getattr(status, "state", "queued") or "queued")
        started_at_ms = getattr(status, "started_at_ms", None) or now_ms
        ended_at_ms = getattr(status, "ended_at_ms", None)
        if state in _TERMINAL_STATES and ended_at_ms is None:
            ended_at_ms = now_ms
            try:
                status.ended_at_ms = ended_at_ms
            except Exception:
                pass

        usage = getattr(status, "usage", None)
        tool_events = list(getattr(status, "tool_events", None) or [])[-20:]
        tool_events_json = json.dumps(tool_events, ensure_ascii=False, separators=(",", ":"))

        values = (
            str(getattr(status, "task_id")),
            str(getattr(status, "label", "") or ""),
            str(getattr(status, "task_description", "") or ""),
            state,
            str(getattr(status, "phase", "initializing") or "initializing"),
            int(getattr(status, "iteration", 0) or 0),
            str(getattr(status, "role", "general") or "general"),
            getattr(status, "model", None),
            getattr(status, "model_preset", None),
            getattr(status, "thinking", None),
            str(getattr(status, "context", "fresh") or "fresh"),
            getattr(status, "timeout_seconds", None),
            getattr(status, "origin_channel", None),
            getattr(status, "origin_chat_id", None),
            getattr(status, "session_key", None),
            getattr(status, "origin_message_id", None),
            int(started_at_ms),
            now_ms,
            int(ended_at_ms) if ended_at_ms is not None else None,
            getattr(status, "stop_reason", None),
            getattr(status, "error", None),
            1 if bool(getattr(status, "completion_delivered", False)) else 0,
            getattr(status, "completion_delivery_error", None),
            getattr(status, "final_output", None),
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
            int(getattr(usage, "total_tokens", 0) or 0),
            tool_events_json,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    task_id, label, task_description, state, phase, iteration,
                    role, model, model_preset, thinking, context_mode, timeout_seconds,
                    origin_channel, origin_chat_id, session_key, origin_message_id,
                    started_at_ms, updated_at_ms, ended_at_ms, stop_reason, error,
                    completion_delivered, completion_delivery_error, final_output,
                    usage_input_tokens, usage_output_tokens, usage_total_tokens,
                    tool_events_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(task_id) DO UPDATE SET
                    label=excluded.label,
                    task_description=excluded.task_description,
                    state=excluded.state,
                    phase=excluded.phase,
                    iteration=excluded.iteration,
                    role=excluded.role,
                    model=excluded.model,
                    model_preset=excluded.model_preset,
                    thinking=excluded.thinking,
                    context_mode=excluded.context_mode,
                    timeout_seconds=excluded.timeout_seconds,
                    origin_channel=excluded.origin_channel,
                    origin_chat_id=excluded.origin_chat_id,
                    session_key=excluded.session_key,
                    origin_message_id=excluded.origin_message_id,
                    started_at_ms=excluded.started_at_ms,
                    updated_at_ms=excluded.updated_at_ms,
                    ended_at_ms=excluded.ended_at_ms,
                    stop_reason=excluded.stop_reason,
                    error=excluded.error,
                    completion_delivered=excluded.completion_delivered,
                    completion_delivery_error=excluded.completion_delivery_error,
                    final_output=excluded.final_output,
                    usage_input_tokens=excluded.usage_input_tokens,
                    usage_output_tokens=excluded.usage_output_tokens,
                    usage_total_tokens=excluded.usage_total_tokens,
                    tool_events_json=excluded.tool_events_json
                """,
                values,
            )

    def recover_interrupted(self, *, now_ms: int | None = None) -> int:
        """Convert stale active rows from a previous process into interrupted history."""
        current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET state='interrupted',
                    phase='interrupted',
                    stop_reason='process_restart',
                    ended_at_ms=COALESCE(ended_at_ms, ?),
                    updated_at_ms=?
                WHERE state IN ('queued', 'running')
                """,
                (current_ms, current_ms),
            )
            return max(0, int(cursor.rowcount or 0))

    def cleanup(self, *, now_ms: int | None = None) -> int:
        """Delete terminal history older than the configured retention window."""
        current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        cutoff_ms = current_ms - self.retention_days * 24 * 60 * 60 * 1000
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM jobs
                WHERE state IN ('completed', 'failed', 'stopped', 'interrupted')
                  AND COALESCE(ended_at_ms, updated_at_ms, started_at_ms) < ?
                """,
                (cutoff_ms,),
            )
            return max(0, int(cursor.rowcount or 0))

    def owns_task(self, task_id: str, session_key: str | None) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE task_id=? AND session_key IS ? LIMIT 1",
                (task_id, session_key),
            ).fetchone()
        return row is not None

    def load_recent(
        self,
        *,
        limit: int = DEFAULT_SUBAGENT_HISTORY_LIMIT,
        session_key: str | None = None,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent durable rows for the existing status/fleet views."""
        clauses: list[str] = []
        params: list[Any] = []
        if session_key is not None:
            clauses.append("session_key IS ?")
            params.append(session_key)
        if task_id is not None:
            clauses.append("task_id=?")
            params.append(task_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM jobs{where} ORDER BY started_at_ms DESC LIMIT ?",
                params,
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                parsed = json.loads(str(item.pop("tool_events_json", "[]") or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = []
            item["tool_events"] = parsed if isinstance(parsed, list) else []
            result.append(item)
        return result
