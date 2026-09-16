from __future__ import annotations

import sqlite3
import time
from types import SimpleNamespace

from nanobot.agent.durable_subagent import DurableSubagentManager
from nanobot.agent.permissions import PermissionManager
from nanobot.agent.subagent_job_ledger import SubagentJobLedger
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config


def _status(*, task_id: str = "task-1", state: str = "running") -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id,
        label="durable task",
        task_description="do durable work",
        state=state,
        phase="initializing" if state == "running" else state,
        iteration=2,
        role="general",
        model_id="test-model",
        thinking=None,
        context="fresh",
        timeout_seconds=None,
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
        origin_message_id="msg-1",
        started_at_ms=int(time.time() * 1000) - 1000,
        ended_at_ms=None,
        stop_reason=None,
        error=None,
        completion_delivered=False,
        completion_delivery_error=None,
        final_output=None,
        usage=None,
        tool_events=[],
    )


def _manager(tmp_path) -> DurableSubagentManager:
    return DurableSubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=1000,
        permission_manager=PermissionManager(Config()),
    )


def test_ledger_marks_stale_active_jobs_interrupted_on_restart(tmp_path) -> None:
    path = tmp_path / "runtime" / "subagent_jobs.db"
    first = SubagentJobLedger(path)
    first.persist(_status())

    restarted = SubagentJobLedger(path)
    rows = restarted.load_recent(session_key="cli:direct")

    assert len(rows) == 1
    assert rows[0]["task_id"] == "task-1"
    assert rows[0]["state"] == "interrupted"
    assert rows[0]["phase"] == "interrupted"
    assert rows[0]["stop_reason"] == "process_restart"
    assert rows[0]["ended_at_ms"] is not None
    assert rows[0]["model_id"] == "test-model"
    assert "model_preset" not in rows[0]


def test_terminal_history_is_cleaned_after_retention_window(tmp_path) -> None:
    path = tmp_path / "runtime" / "subagent_jobs.db"
    ledger = SubagentJobLedger(path, retention_days=30)
    status = _status(state="completed")
    status.phase = "done"
    status.final_output = "ok"
    ledger.persist(status)
    row = ledger.load_recent()[0]

    deleted = ledger.cleanup(now_ms=int(row["ended_at_ms"]) + 31 * 24 * 60 * 60 * 1000)

    assert deleted == 1
    assert ledger.load_recent() == []


def test_durable_manager_restores_interrupted_job_into_existing_status_view(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager._job_ledger.persist(_status(task_id="restart-me"))

    restarted = _manager(tmp_path)

    snapshots = restarted.status_snapshot("cli:direct", "restart-me")
    assert len(snapshots) == 1
    assert snapshots[0]["state"] == "interrupted"
    assert snapshots[0]["stop_reason"] == "process_restart"
    assert restarted.owns_task("restart-me", "cli:direct") is True


def test_durable_manager_keeps_live_runtime_authoritative(tmp_path) -> None:
    manager = _manager(tmp_path)
    assert manager.get_running_count() == 0
    assert manager.status_snapshot("cli:direct") == []


def test_ledger_migrates_legacy_model_columns_and_preserves_rows(tmp_path) -> None:
    path = tmp_path / "runtime" / "subagent_jobs.db"
    path.parent.mkdir(parents=True)
    now_ms = int(time.time() * 1000)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE jobs (
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
                tool_events_json TEXT NOT NULL DEFAULT '[]',
                legacy_marker TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO jobs VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                "legacy-task", "legacy label", "legacy description", "completed",
                "done", 1, "general", "upstream/model", "legacy-id", None,
                "fresh", None, "cli", "direct", "cli:direct", "legacy-msg",
                now_ms - 1000, now_ms, now_ms, None, None, 1, None, "legacy output",
                3, 4, 7, "[]", "keep me",
            ),
        )

    ledger = SubagentJobLedger(path)
    legacy = ledger.load_recent(task_id="legacy-task")[0]
    assert legacy["model_id"] == "legacy-id"
    assert legacy["legacy_marker"] == "keep me"
    assert "model" not in legacy
    assert "model_preset" not in legacy

    ledger.persist(_status(task_id="new-task"))
    assert ledger.load_recent(task_id="new-task")[0]["model_id"] == "test-model"

    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    assert "model_id" in columns
    assert {"model", "model_preset"}.isdisjoint(columns)
