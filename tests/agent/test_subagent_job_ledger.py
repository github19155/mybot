from __future__ import annotations

import time
from types import SimpleNamespace

from nanobot.agent.durable_subagent import DurableSubagentManager
from nanobot.agent.subagent_job_ledger import SubagentJobLedger
from nanobot.bus.queue import MessageBus


def _status(*, task_id: str = "task-1", state: str = "running") -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id,
        label="durable task",
        task_description="do durable work",
        state=state,
        phase="initializing" if state == "running" else state,
        iteration=2,
        role="general",
        model="test-model",
        model_preset=None,
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
    manager = DurableSubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=1000,
    )
    manager._job_ledger.persist(_status(task_id="restart-me"))

    restarted = DurableSubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=1000,
    )

    snapshots = restarted.status_snapshot("cli:direct", "restart-me")
    assert len(snapshots) == 1
    assert snapshots[0]["state"] == "interrupted"
    assert snapshots[0]["stop_reason"] == "process_restart"
    assert restarted.owns_task("restart-me", "cli:direct") is True


def test_durable_manager_keeps_live_runtime_authoritative(tmp_path) -> None:
    manager = DurableSubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=1000,
    )
    assert manager.get_running_count() == 0
    assert manager.status_snapshot("cli:direct") == []
