"""Dream background cognition control plane.

Dream is a read-only observer. It may produce findings and proposals, but it
cannot mutate Memory, Skills, Specialists, configuration, tools, or external
systems. Runtime owns scheduling and execution; Main owns operational control;
high-impact proposals require explicit User approval.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from loguru import logger

from nanobot.agent.permissions import DREAM_SUBJECT, PermissionManager
from nanobot.agent.tools.filesystem import ReadFileTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.permission_types import CONFIG_WRITE, MODEL_MANAGE, SPECIALIST_MANAGE, WORKSPACE_WRITE

if TYPE_CHECKING:
    from nanobot.agent.memory import MemoryStore
    from nanobot.config.schema import DreamConfig
    from nanobot.utils.llm_runtime import LLMRuntime

DreamWorkload = Literal[
    "dream.consolidation",
    "dream.extraction",
    "dream.governance",
    "dream.housekeeping",
]
DreamImpact = Literal["low", "medium", "high"]

DREAM_CONSOLIDATION: DreamWorkload = "dream.consolidation"
DREAM_EXTRACTION: DreamWorkload = "dream.extraction"
DREAM_GOVERNANCE: DreamWorkload = "dream.governance"
DREAM_HOUSEKEEPING: DreamWorkload = "dream.housekeeping"
DREAM_WORKLOADS = frozenset({
    DREAM_CONSOLIDATION,
    DREAM_EXTRACTION,
    DREAM_GOVERNANCE,
    DREAM_HOUSEKEEPING,
})

# Runtime safety ceilings. Main may tune DreamConfig inside these limits;
# changing the ceilings is a governance/code change, not normal adaptation.
MAX_RUNS_PER_DAY = 24
MAX_ENTRIES_PER_RUN = 100
MAX_RETENTION_DAYS = 90

_ALLOWED_FINDING_KINDS = frozenset({
    "insight",
    "contradiction",
    "uncertainty",
    "pattern",
})
_ALLOWED_PROPOSAL_KINDS = frozenset({
    "memory_write",
    "follow_up",
    "optimization",
    "task_candidate",
    "specialist_candidate",
    "model_evaluation_profile",
    "model_pool_change",
    "consolidation_candidate",
    "deprecation_candidate",
    "archive_candidate",
    "deletion_candidate",
})

_PROPOSAL_CAPABILITIES = {
    "memory_write": WORKSPACE_WRITE,
    "optimization": CONFIG_WRITE,
    "specialist_candidate": SPECIALIST_MANAGE,
    "model_evaluation_profile": MODEL_MANAGE,
    "model_pool_change": MODEL_MANAGE,
    "archive_candidate": WORKSPACE_WRITE,
    "deletion_candidate": WORKSPACE_WRITE,
    "deprecation_candidate": CONFIG_WRITE,
}


@dataclass(frozen=True, slots=True)
class DreamRunContext:
    run_id: str
    workload: DreamWorkload
    source_revision: int
    started_at_ms: int


@dataclass(frozen=True, slots=True)
class DreamTriggerDecision:
    run: bool
    reason: str
    workload: DreamWorkload = DREAM_CONSOLIDATION
    pending_entries: int = 0


@dataclass(frozen=True, slots=True)
class DreamBatch:
    prompt: str
    run: DreamRunContext
    decision: DreamTriggerDecision


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def _string_list(value: object, *, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item)[:500]
        for item in cast(list[object], value)[:limit]
        if str(item).strip()
    ]


def _confidence(value: object) -> float:
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return round(max(0.0, min(1.0, number)), 4)


def _impact(value: object) -> DreamImpact:
    normalized = str(value or "medium").strip().lower()
    if normalized in {"low", "medium", "high"}:
        return cast(DreamImpact, normalized)
    return "medium"


def _proposal_requires_user_approval(
    permissions: PermissionManager,
    kind: str,
    *,
    impact: str,
    proposed_action: object = None,
) -> bool:
    capability = _PROPOSAL_CAPABILITIES.get(str(kind or "").strip().lower())
    if capability is not None and permissions.requires_user_approval((capability,)):
        return True
    if isinstance(proposed_action, dict):
        requested = proposed_action.get("capabilities")
        if isinstance(requested, list) and permissions.requires_user_approval(requested):
            return True
    return str(impact or "").strip().lower() == "high"


class DreamTriggerController:
    """Durable pressure/cooldown/budget gate for Dream jobs.

    Configuration has one source: ``DreamConfig``. Only execution state and
    explicit manual requests are stored under ``memory/``.
    """

    def __init__(
        self,
        workspace: Path,
        config: "DreamConfig",
        permissions: PermissionManager,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self.permissions = permissions
        memory_dir = workspace / "memory"
        self.state_path = memory_dir / ".dream_state.json"
        self.request_path = memory_dir / ".dream_request.json"
        self.results_path = memory_dir / "dream_results.jsonl"

    def _state(self) -> dict[str, object]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return {"attempts_ms": []}
        return cast(dict[str, object], raw) if isinstance(raw, dict) else {"attempts_ms": []}

    def _save_state(self, state: dict[str, object]) -> None:
        _atomic_json(self.state_path, state)

    @staticmethod
    def _attempts(state: dict[str, object], now_ms: int) -> list[int]:
        cutoff = now_ms - 86_400_000
        raw = state.get("attempts_ms")
        if not isinstance(raw, list):
            return []
        return [
            value
            for value in cast(list[object], raw)
            if isinstance(value, int) and not isinstance(value, bool) and value >= cutoff
        ]

    def request(self, workload: DreamWorkload = DREAM_CONSOLIDATION) -> None:
        if workload not in DREAM_WORKLOADS:
            raise ValueError(f"Unsupported Dream workload: {workload}")
        _atomic_json(
            self.request_path,
            {"workload": workload, "requested_at_ms": time.time_ns() // 1_000_000},
        )

    def _manual_request(self) -> DreamWorkload | None:
        try:
            raw = json.loads(self.request_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return None
        if not isinstance(raw, dict):
            return None
        workload = str(cast(dict[str, object], raw).get("workload") or "").strip().lower()
        return cast(DreamWorkload, workload) if workload in DREAM_WORKLOADS else None

    def _consume_manual_request(self) -> None:
        try:
            self.request_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Dream could not consume manual request {}", self.request_path)

    def pressure(self, memory: "MemoryStore") -> tuple[int, int | None]:
        """Return unprocessed history count and latest history activity time."""
        last_cursor = memory.get_last_dream_cursor()
        pending = 0
        for entry in memory.read_unprocessed_history(since_cursor=last_cursor):
            cursor = entry.get("cursor")
            if isinstance(cursor, int) and not isinstance(cursor, bool) and cursor > last_cursor:
                pending += 1
        try:
            latest_activity_ms = memory.history_file.stat().st_mtime_ns // 1_000_000
        except OSError:
            latest_activity_ms = None
        return pending, latest_activity_ms

    def decide(
        self,
        *,
        pending_entries: int,
        last_activity_ms: int | None,
        now_ms: int | None = None,
    ) -> DreamTriggerDecision:
        if not self.config.enabled:
            return DreamTriggerDecision(False, "disabled", pending_entries=pending_entries)

        now = int(now_ms if now_ms is not None else time.time_ns() // 1_000_000)
        state = self._state()
        attempts = self._attempts(state, now)
        manual = self._manual_request()

        if pending_entries <= 0:
            return DreamTriggerDecision(False, "no_pending_history")
        if len(attempts) >= min(self.config.max_runs_per_day, MAX_RUNS_PER_DAY):
            return DreamTriggerDecision(False, "daily_budget", pending_entries=pending_entries)

        last_attempt = max(attempts, default=0)
        if (
            manual is None
            and last_attempt
            and now - last_attempt < self.config.cooldown_minutes * 60_000
        ):
            return DreamTriggerDecision(False, "cooldown", pending_entries=pending_entries)

        idle_ms = max(0, now - last_activity_ms) if last_activity_ms is not None else None
        idle_ready = idle_ms is None or idle_ms >= self.config.idle_minutes * 60_000
        deferred_ready = (
            idle_ms is not None
            and idle_ms >= self.config.max_defer_minutes * 60_000
        )
        pressure_ready = pending_entries >= self.config.pressure_entries

        if manual is not None:
            return DreamTriggerDecision(True, "manual", manual, pending_entries)
        if not idle_ready:
            return DreamTriggerDecision(False, "foreground_active", pending_entries=pending_entries)
        if pressure_ready:
            return DreamTriggerDecision(True, "pressure", DREAM_CONSOLIDATION, pending_entries)
        if deferred_ready:
            return DreamTriggerDecision(True, "deferred", DREAM_CONSOLIDATION, pending_entries)
        return DreamTriggerDecision(False, "below_pressure", pending_entries=pending_entries)

    def admit(self, decision: DreamTriggerDecision, *, now_ms: int | None = None) -> None:
        if not decision.run:
            return
        now = int(now_ms if now_ms is not None else time.time_ns() // 1_000_000)
        state = self._state()
        attempts = self._attempts(state, now)
        attempts.append(now)
        state.update({
            "attempts_ms": attempts[-MAX_RUNS_PER_DAY:],
            "last_reason": decision.reason,
            "last_workload": decision.workload,
            "last_pending_entries": decision.pending_entries,
        })
        self._save_state(state)
        if decision.reason == "manual":
            self._consume_manual_request()

    def prepare(self, memory: "MemoryStore") -> DreamBatch | None:
        pending, latest_activity_ms = self.pressure(memory)
        decision = self.decide(
            pending_entries=pending,
            last_activity_ms=latest_activity_ms,
        )
        if not decision.run:
            return None
        result = memory.build_dream_prompt(
            max_entries=min(self.config.max_entries_per_run, MAX_ENTRIES_PER_RUN),
        )
        if result is None:
            return None
        prompt, cursor = result
        self.admit(decision)
        run = DreamRunContext(
            run_id=uuid.uuid4().hex,
            workload=decision.workload,
            source_revision=cursor,
            started_at_ms=time.time_ns() // 1_000_000,
        )
        contract = (
            "\n\n## Runtime Contract\n"
            f"run_id: {run.run_id}\n"
            f"workload: {run.workload}\n"
            f"source_revision: {run.source_revision}\n"
            "This run is read-only. Return exactly one JSON object matching the "
            "DreamResult contract. Never claim a proposal was executed or written "
            "to canonical state."
        )
        return DreamBatch(prompt + contract, run, decision)

    def store_result(
        self,
        content: str,
        *,
        run: DreamRunContext,
        runtime: "LLMRuntime",
    ) -> dict[str, object]:
        result = parse_dream_result(content, run=run, permissions=self.permissions)
        result["metadata"] = {
            **cast(dict[str, object], result["metadata"]),
            "model_id": runtime.model_id,
            "model": runtime.model,
            "provider": runtime.provider.provider_name,
        }
        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        with self.results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
        self.compact_results()
        return result

    def compact_results(self, *, now_ms: int | None = None) -> None:
        if not self.results_path.exists():
            return
        now = int(now_ms if now_ms is not None else time.time_ns() // 1_000_000)
        cutoff = now - min(self.config.retention_days, MAX_RETENTION_DAYS) * 86_400_000
        kept: list[str] = []
        try:
            for line in self.results_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and int(row.get("created_at_ms") or 0) >= cutoff:
                    kept.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
            self.results_path.write_text(
                "" if not kept else "\n".join(kept) + "\n",
                encoding="utf-8",
            )
        except OSError:
            logger.exception("Dream failed to compact result history")


def build_dream_tools(workspace: Path, permissions: PermissionManager) -> ToolRegistry:
    """Build Dream tools from canonical permission policy."""
    registry = ToolRegistry(
        permission_manager=permissions,
        permission_subject=DREAM_SUBJECT,
    )
    registry.register(ReadFileTool(  # pyright: ignore[reportAbstractUsage]
        workspace=workspace,
        allowed_dir=workspace,
        restrict_to_workspace=True,
    ))
    return registry


def parse_dream_result(
    content: str,
    *,
    run: DreamRunContext,
    permissions: PermissionManager,
) -> dict[str, object]:
    """Validate model output before it becomes a durable Dream audit record."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            text = "\n".join(lines[1:-1]).strip()
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("DreamResult must be a JSON object")
    data = cast(dict[str, object], raw)
    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise ValueError("DreamResult.summary is required")

    findings: list[dict[str, object]] = []
    raw_findings = data.get("findings")
    if isinstance(raw_findings, list):
        for raw_finding in cast(list[object], raw_findings)[:50]:
            if not isinstance(raw_finding, dict):
                continue
            item = cast(dict[str, object], raw_finding)
            kind = str(item.get("kind") or "").strip().lower()
            item_summary = str(item.get("summary") or "").strip()
            if kind not in _ALLOWED_FINDING_KINDS or not item_summary:
                continue
            findings.append({
                "kind": kind,
                "summary": item_summary[:4000],
                "evidence_refs": _string_list(item.get("evidence_refs")),
                "confidence": _confidence(item.get("confidence")),
            })

    proposals: list[dict[str, object]] = []
    raw_proposals = data.get("proposals")
    if isinstance(raw_proposals, list):
        for raw_proposal in cast(list[object], raw_proposals)[:50]:
            if not isinstance(raw_proposal, dict):
                continue
            item = cast(dict[str, object], raw_proposal)
            kind = str(item.get("kind") or "").strip().lower()
            item_summary = str(item.get("summary") or "").strip()
            if kind not in _ALLOWED_PROPOSAL_KINDS or not item_summary:
                continue
            impact = _impact(item.get("impact_level"))
            reversible = bool(item.get("reversible", False))
            proposed_action = item.get("proposed_action")
            if proposed_action is not None and not isinstance(proposed_action, dict):
                proposed_action = {"description": str(proposed_action)[:4000]}
            requires_user_approval = _proposal_requires_user_approval(
                permissions,
                kind,
                impact=impact,
                proposed_action=proposed_action,
            )
            proposals.append({
                "proposal_id": uuid.uuid4().hex,
                "state": "awaiting_user_approval" if requires_user_approval else "pending",
                "kind": kind,
                "summary": item_summary[:4000],
                "rationale": str(item.get("rationale") or "")[:4000],
                "evidence_refs": _string_list(item.get("evidence_refs")),
                "confidence": _confidence(item.get("confidence")),
                "impact_level": impact,
                "reversible": reversible,
                "requires_user_approval": requires_user_approval,
                "proposed_action": proposed_action,
            })

    return {
        "run_id": run.run_id,
        "workload": run.workload,
        "source_revision": run.source_revision,
        "created_at_ms": time.time_ns() // 1_000_000,
        "summary": summary[:8000],
        "findings": findings,
        "proposals": proposals,
        "metadata": {
            "side_effects": "none",
            "operational_authority": "main",
            "high_impact_authority": "user",
        },
    }
