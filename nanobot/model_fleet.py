"""Runtime model-fleet telemetry, scoring, retention, and request admission.

The fleet identity is a concrete model offering (configured provider identity +
model), not a model family name.  This keeps the same nominal model served by
different providers fully independent for health, cost, speed, and scheduling.
"""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, AsyncIterator, Literal, Mapping, Sequence

from loguru import logger

FleetPriority = Literal["main", "worker", "background"]
RateLimitScope = Literal["provider", "model"]


@dataclass(frozen=True, slots=True)
class ModelOffering:
    """Stable, non-secret identity and declared facts for one provider/model route."""

    offering_id: str
    provider: str
    model: str
    preset_name: str | None = None
    pools: tuple[str, ...] = ()
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    cached_input_cost_per_million: float | None = None
    max_concurrent_requests: int | None = None
    provider_max_concurrent_requests: int | None = None
    rate_limit_scope: RateLimitScope = "provider"
    supports_vision: bool = False
    context_window_tokens: int | None = None


@dataclass(slots=True)
class _AdmissionWaiter:
    sequence: int
    priority: FleetPriority
    provider: str
    offering_id: str


@dataclass(slots=True)
class _PoolState:
    configured_limit: int | None = None
    adaptive_limit: int | None = None
    active: int = 0
    cooldown_until: float = 0.0
    success_streak: int = 0
    recent_429: int = 0

    @property
    def effective_limit(self) -> int | None:
        if self.adaptive_limit is None:
            return self.configured_limit
        if self.configured_limit is None:
            return self.adaptive_limit
        return min(self.configured_limit, self.adaptive_limit)


class ModelAdmissionController:
    """Shared request-level provider/offering admission with adaptive 429 pressure."""

    _RECOVERY_STEP_SUCCESSES = 8
    _UNLIMITED_RECOVERY_SUCCESSES = 32

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._providers: dict[str, _PoolState] = {}
        self._offerings: dict[str, _PoolState] = {}
        self._waiters: deque[_AdmissionWaiter] = deque()
        self._sequence = 0

    def configure(self, offering: ModelOffering) -> None:
        provider = self._providers.setdefault(offering.provider, _PoolState())
        provider.configured_limit = offering.provider_max_concurrent_requests
        model = self._offerings.setdefault(offering.offering_id, _PoolState())
        model.configured_limit = offering.max_concurrent_requests

    @staticmethod
    def _priority_rank(priority: FleetPriority) -> int:
        return {"main": 0, "worker": 1, "background": 2}[priority]

    def _candidate(self) -> _AdmissionWaiter | None:
        """Pick priority first while preserving FIFO within each class.

        A bounded fairness guard lets a lower-priority waiter through after eight
        newer higher-priority admissions would otherwise pass it indefinitely.
        """
        if not self._waiters:
            return None
        oldest = self._waiters[0]
        eligible = sorted(
            self._waiters,
            key=lambda item: (self._priority_rank(item.priority), item.sequence),
        )
        best = eligible[0]
        if best.sequence - oldest.sequence >= 8:
            return oldest
        return best

    @staticmethod
    def _has_capacity(state: _PoolState) -> bool:
        limit = state.effective_limit
        return limit is None or state.active < limit

    def _can_enter(self, waiter: _AdmissionWaiter, now: float) -> bool:
        if self._candidate() is not waiter:
            return False
        provider = self._providers.setdefault(waiter.provider, _PoolState())
        offering = self._offerings.setdefault(waiter.offering_id, _PoolState())
        return (
            provider.cooldown_until <= now
            and offering.cooldown_until <= now
            and self._has_capacity(provider)
            and self._has_capacity(offering)
        )

    async def acquire(
        self,
        offering: ModelOffering,
        *,
        priority: FleetPriority,
    ) -> None:
        self.configure(offering)
        async with self._condition:
            self._sequence += 1
            waiter = _AdmissionWaiter(
                sequence=self._sequence,
                priority=priority,
                provider=offering.provider,
                offering_id=offering.offering_id,
            )
            self._waiters.append(waiter)
            try:
                while True:
                    now = time.monotonic()
                    if self._can_enter(waiter, now):
                        self._waiters.remove(waiter)
                        self._providers[offering.provider].active += 1
                        self._offerings[offering.offering_id].active += 1
                        self._condition.notify_all()
                        return
                    cooldowns = (
                        self._providers[offering.provider].cooldown_until,
                        self._offerings[offering.offering_id].cooldown_until,
                    )
                    future = [value - now for value in cooldowns if value > now]
                    timeout = min(future) if future else None
                    if timeout is None:
                        await self._condition.wait()
                    else:
                        try:
                            await asyncio.wait_for(self._condition.wait(), timeout=timeout)
                        except TimeoutError:
                            pass
            except asyncio.CancelledError:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                self._condition.notify_all()
                raise

    async def release(self, offering: ModelOffering) -> None:
        async with self._condition:
            provider = self._providers.setdefault(offering.provider, _PoolState())
            model = self._offerings.setdefault(offering.offering_id, _PoolState())
            provider.active = max(0, provider.active - 1)
            model.active = max(0, model.active - 1)
            self._condition.notify_all()

    @staticmethod
    def _reduce(state: _PoolState) -> None:
        current = state.effective_limit
        if current is None:
            learned = max(1, state.active - 1) if state.active > 1 else 1
        else:
            learned = max(1, current // 2)
        state.adaptive_limit = learned
        state.success_streak = 0
        state.recent_429 += 1

    @classmethod
    def _recover(cls, state: _PoolState) -> None:
        if state.adaptive_limit is None:
            return
        state.success_streak += 1
        if state.configured_limit is None:
            if state.success_streak >= cls._UNLIMITED_RECOVERY_SUCCESSES:
                state.adaptive_limit = None
                state.success_streak = 0
            elif state.success_streak % cls._RECOVERY_STEP_SUCCESSES == 0:
                state.adaptive_limit += 1
            return
        if state.success_streak >= cls._RECOVERY_STEP_SUCCESSES:
            state.adaptive_limit = min(state.configured_limit, state.adaptive_limit + 1)
            state.success_streak = 0

    async def note_rate_limit(
        self,
        offering: ModelOffering,
        *,
        retry_after_s: float | None,
    ) -> None:
        self.configure(offering)
        cooldown = max(1.0, float(retry_after_s or 1.0))
        until = time.monotonic() + cooldown
        async with self._condition:
            target = (
                self._providers[offering.provider]
                if offering.rate_limit_scope == "provider"
                else self._offerings[offering.offering_id]
            )
            target.cooldown_until = max(target.cooldown_until, until)
            self._reduce(target)
            self._condition.notify_all()

    async def note_success(self, offering: ModelOffering) -> None:
        self.configure(offering)
        async with self._condition:
            self._recover(self._providers[offering.provider])
            self._recover(self._offerings[offering.offering_id])
            self._condition.notify_all()

    async def snapshot(self, offering: ModelOffering) -> dict[str, object]:
        self.configure(offering)
        async with self._condition:
            now = time.monotonic()
            provider = self._providers[offering.provider]
            model = self._offerings[offering.offering_id]
            return {
                "provider_active": provider.active,
                "provider_limit": provider.effective_limit,
                "offering_active": model.active,
                "offering_limit": model.effective_limit,
                "queued": sum(1 for item in self._waiters if item.offering_id == offering.offering_id),
                "cooldown_seconds": round(max(
                    provider.cooldown_until - now,
                    model.cooldown_until - now,
                    0.0,
                ), 3),
                "recent_429": provider.recent_429 + model.recent_429,
            }


class ModelFleetStore:
    """Small SQLite persistence layer for content-free fleet telemetry."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(path) if path is not None else ":memory:",
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS offerings (
                    offering_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    preset_name TEXT,
                    pools_json TEXT NOT NULL DEFAULT '[]',
                    input_cost_per_million REAL,
                    output_cost_per_million REAL,
                    cached_input_cost_per_million REAL,
                    supports_vision INTEGER NOT NULL DEFAULT 0,
                    context_window_tokens INTEGER,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offering_id TEXT NOT NULL,
                    started_at_ms INTEGER NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cache_read_tokens INTEGER,
                    generation_ms INTEGER,
                    ttft_ms INTEGER,
                    finish_reason TEXT NOT NULL,
                    error_status_code INTEGER,
                    error_kind TEXT,
                    estimated_cost REAL,
                    FOREIGN KEY(offering_id) REFERENCES offerings(offering_id)
                );
                CREATE INDEX IF NOT EXISTS calls_offering_time
                    ON calls(offering_id, started_at_ms);
                CREATE TABLE IF NOT EXISTS quality_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offering_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    outcome REAL NOT NULL,
                    weight REAL NOT NULL,
                    evidence TEXT,
                    created_at_ms INTEGER NOT NULL,
                    FOREIGN KEY(offering_id) REFERENCES offerings(offering_id)
                );
                CREATE INDEX IF NOT EXISTS quality_offering_time
                    ON quality_events(offering_id, created_at_ms);
                CREATE TABLE IF NOT EXISTS score_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offering_id TEXT NOT NULL,
                    calculated_at_ms INTEGER NOT NULL,
                    quality REAL,
                    speed REAL,
                    reliability REAL,
                    cost REAL,
                    confidence REAL NOT NULL,
                    trend REAL NOT NULL,
                    samples INTEGER NOT NULL,
                    metrics_json TEXT NOT NULL,
                    FOREIGN KEY(offering_id) REFERENCES offerings(offering_id)
                );
                CREATE INDEX IF NOT EXISTS scores_offering_time
                    ON score_snapshots(offering_id, calculated_at_ms);
                CREATE TABLE IF NOT EXISTS aggregates (
                    offering_id TEXT NOT NULL,
                    bucket_kind TEXT NOT NULL,
                    bucket_start_ms INTEGER NOT NULL,
                    calls INTEGER NOT NULL,
                    successes INTEGER NOT NULL,
                    rate_limits INTEGER NOT NULL,
                    avg_duration_ms REAL,
                    avg_ttft_ms REAL,
                    avg_generation_ms REAL,
                    total_cost REAL,
                    PRIMARY KEY(offering_id, bucket_kind, bucket_start_ms)
                );
                """
            )

    def upsert_offering(self, offering: ModelOffering) -> None:
        now_ms = time.time_ns() // 1_000_000
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO offerings(
                    offering_id, provider, model, preset_name, pools_json,
                    input_cost_per_million, output_cost_per_million,
                    cached_input_cost_per_million, supports_vision,
                    context_window_tokens, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(offering_id) DO UPDATE SET
                    provider=excluded.provider,
                    model=excluded.model,
                    preset_name=COALESCE(excluded.preset_name, offerings.preset_name),
                    pools_json=excluded.pools_json,
                    input_cost_per_million=excluded.input_cost_per_million,
                    output_cost_per_million=excluded.output_cost_per_million,
                    cached_input_cost_per_million=excluded.cached_input_cost_per_million,
                    supports_vision=excluded.supports_vision,
                    context_window_tokens=excluded.context_window_tokens,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (
                    offering.offering_id,
                    offering.provider,
                    offering.model,
                    offering.preset_name,
                    json.dumps(list(offering.pools)),
                    offering.input_cost_per_million,
                    offering.output_cost_per_million,
                    offering.cached_input_cost_per_million,
                    int(offering.supports_vision),
                    offering.context_window_tokens,
                    now_ms,
                ),
            )

    @staticmethod
    def _cost(offering: ModelOffering, usage: Any | None) -> float | None:
        if usage is None:
            return None
        input_rate = offering.input_cost_per_million
        output_rate = offering.output_cost_per_million
        cache_rate = offering.cached_input_cost_per_million
        if input_rate is None and output_rate is None and cache_rate is None:
            return None
        cache = int(getattr(usage, "cache_read_tokens", 0) or 0)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        uncached = max(0, input_tokens - cache)
        return (
            uncached * float(input_rate or 0.0)
            + cache * float(cache_rate if cache_rate is not None else (input_rate or 0.0))
            + output_tokens * float(output_rate or 0.0)
        ) / 1_000_000.0

    def record_call(
        self,
        offering: ModelOffering,
        *,
        started_at_ms: int,
        duration_ms: int,
        response: Any,
        usage: Any | None,
    ) -> None:
        self.upsert_offering(offering)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO calls(
                    offering_id, started_at_ms, duration_ms, input_tokens,
                    output_tokens, cache_read_tokens, generation_ms, ttft_ms,
                    finish_reason, error_status_code, error_kind, estimated_cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    offering.offering_id,
                    started_at_ms,
                    duration_ms,
                    getattr(usage, "input_tokens", None),
                    getattr(usage, "output_tokens", None),
                    getattr(usage, "cache_read_tokens", None),
                    getattr(usage, "generation_ms", None),
                    getattr(usage, "ttft_ms", None),
                    str(getattr(response, "finish_reason", "unknown") or "unknown"),
                    getattr(response, "error_status_code", None),
                    getattr(response, "error_kind", None),
                    self._cost(offering, usage),
                ),
            )

    def record_quality(
        self,
        offering_id: str,
        *,
        dimension: str,
        outcome: float,
        weight: float,
        evidence: str | None,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO quality_events(
                    offering_id, dimension, outcome, weight, evidence, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    offering_id,
                    dimension,
                    max(-1.0, min(1.0, float(outcome))),
                    max(0.01, min(10.0, float(weight))),
                    (evidence or "")[:500] or None,
                    time.time_ns() // 1_000_000,
                ),
            )

    def offering_rows(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute("SELECT * FROM offerings ORDER BY offering_id"))

    def calls_for(self, offering_id: str, *, since_ms: int) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute(
                "SELECT * FROM calls WHERE offering_id=? AND started_at_ms>=? ORDER BY started_at_ms",
                (offering_id, since_ms),
            ))

    def quality_for(self, offering_id: str, *, since_ms: int) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute(
                "SELECT * FROM quality_events WHERE offering_id=? AND created_at_ms>=? ORDER BY created_at_ms",
                (offering_id, since_ms),
            ))

    def save_score(self, offering_id: str, score: Mapping[str, Any]) -> None:
        metrics = dict(score.get("metrics", {}))
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO score_snapshots(
                    offering_id, calculated_at_ms, quality, speed, reliability,
                    cost, confidence, trend, samples, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    offering_id,
                    int(score["calculated_at_ms"]),
                    score.get("quality"),
                    score.get("speed"),
                    score.get("reliability"),
                    score.get("cost"),
                    float(score["confidence"]),
                    float(score["trend"]),
                    int(score["samples"]),
                    json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                ),
            )

    def latest_scores(self) -> dict[str, sqlite3.Row]:
        query = """
            SELECT s.* FROM score_snapshots s
            JOIN (
                SELECT offering_id, MAX(calculated_at_ms) AS calculated_at_ms
                FROM score_snapshots GROUP BY offering_id
            ) latest
            ON latest.offering_id=s.offering_id
               AND latest.calculated_at_ms=s.calculated_at_ms
        """
        with self._lock:
            return {row["offering_id"]: row for row in self._connection.execute(query)}

    def _aggregate_window(self, *, cutoff_ms: int, kind: str, bucket_ms: int) -> None:
        rows = list(self._connection.execute(
            "SELECT DISTINCT offering_id FROM calls WHERE started_at_ms < ?",
            (cutoff_ms,),
        ))
        for item in rows:
            offering_id = item[0]
            buckets = list(self._connection.execute(
                """
                SELECT (started_at_ms / ?) * ? AS bucket,
                       COUNT(*) AS calls,
                       SUM(CASE WHEN finish_reason NOT IN ('error','cancelled') THEN 1 ELSE 0 END) AS successes,
                       SUM(CASE WHEN error_status_code=429 THEN 1 ELSE 0 END) AS rate_limits,
                       AVG(duration_ms), AVG(ttft_ms), AVG(generation_ms), SUM(estimated_cost)
                FROM calls
                WHERE offering_id=? AND started_at_ms < ?
                GROUP BY bucket
                """,
                (bucket_ms, bucket_ms, offering_id, cutoff_ms),
            ))
            for bucket in buckets:
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO aggregates(
                        offering_id, bucket_kind, bucket_start_ms, calls, successes,
                        rate_limits, avg_duration_ms, avg_ttft_ms, avg_generation_ms, total_cost
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (offering_id, kind, *bucket),
                )

    def maintenance(
        self,
        *,
        raw_calls_days: int,
        hourly_days: int,
        score_full_days: int,
    ) -> None:
        now_ms = time.time_ns() // 1_000_000
        day_ms = 86_400_000
        raw_cutoff = now_ms - raw_calls_days * day_ms
        hourly_cutoff = now_ms - hourly_days * day_ms
        score_cutoff = now_ms - score_full_days * day_ms
        with self._lock, self._connection:
            # Aggregate before deleting raw rows. Daily is retained indefinitely;
            # hourly is pruned after its configured window.
            self._aggregate_window(cutoff_ms=raw_cutoff, kind="daily", bucket_ms=day_ms)
            self._aggregate_window(cutoff_ms=raw_cutoff, kind="hourly", bucket_ms=3_600_000)
            self._connection.execute("DELETE FROM calls WHERE started_at_ms < ?", (raw_cutoff,))
            self._connection.execute(
                "DELETE FROM aggregates WHERE bucket_kind='hourly' AND bucket_start_ms < ?",
                (hourly_cutoff,),
            )
            # Keep full recent scores; older history becomes one snapshot/day.
            self._connection.execute(
                """
                DELETE FROM score_snapshots
                WHERE calculated_at_ms < ? AND id NOT IN (
                    SELECT MAX(id) FROM score_snapshots
                    WHERE calculated_at_ms < ?
                    GROUP BY offering_id, calculated_at_ms / ?
                )
                """,
                (score_cutoff, score_cutoff, day_ms),
            )


class ModelFleetManager:
    """Main-visible fleet platform backed by passive real-request telemetry."""

    _WINDOW_DAYS = 7

    def __init__(self, store: ModelFleetStore, *, config: Any) -> None:
        self.store = store
        self.admission = ModelAdmissionController()
        self._offerings: dict[str, ModelOffering] = {}
        self._config = config
        self._maintenance_lock = threading.Lock()
        self._last_maintenance_ms = 0

    def bind_offering(self, offering: ModelOffering) -> ModelOffering:
        self._offerings[offering.offering_id] = offering
        self.admission.configure(offering)
        self.store.upsert_offering(offering)
        return offering

    async def record_call(
        self,
        offering: ModelOffering,
        *,
        started_at_ms: int,
        duration_ms: int,
        response: Any,
        usage: Any | None,
    ) -> None:
        await asyncio.to_thread(
            self.store.record_call,
            offering,
            started_at_ms=started_at_ms,
            duration_ms=duration_ms,
            response=response,
            usage=usage,
        )

    async def note_response(self, offering: ModelOffering, response: Any) -> None:
        status = getattr(response, "error_status_code", None)
        retryable_429 = False
        if status == 429:
            try:
                from nanobot.providers.base import LLMProvider
                retryable_429 = LLMProvider._is_retryable_429_response(response)
            except Exception:
                retryable_429 = bool(getattr(response, "error_should_retry", True))
        if retryable_429:
            retry_after = (
                getattr(response, "error_retry_after_s", None)
                or getattr(response, "retry_after", None)
            )
            await self.admission.note_rate_limit(offering, retry_after_s=retry_after)
        elif getattr(response, "finish_reason", None) not in {"error", "cancelled"}:
            await self.admission.note_success(offering)

    @asynccontextmanager
    async def slot(
        self,
        offering: ModelOffering,
        *,
        priority: FleetPriority,
    ) -> AsyncIterator[None]:
        await self.admission.acquire(offering, priority=priority)
        try:
            yield
        finally:
            await self.admission.release(offering)

    def record_quality(
        self,
        offering_id: str,
        *,
        dimension: str,
        outcome: float,
        weight: float = 1.0,
        evidence: str | None = None,
    ) -> None:
        if offering_id not in self._offerings:
            raise ValueError("Unknown model offering")
        self.store.record_quality(
            offering_id,
            dimension=dimension,
            outcome=outcome,
            weight=weight,
            evidence=evidence,
        )

    @staticmethod
    def _score_quality(rows: Sequence[sqlite3.Row], now_ms: int) -> tuple[float | None, int]:
        if not rows:
            return None, 0
        weighted = 0.0
        total = 0.0
        for row in rows:
            age_days = max(0.0, (now_ms - int(row["created_at_ms"])) / 86_400_000.0)
            decay = 0.5 ** (age_days / 14.0)
            weight = float(row["weight"]) * decay
            weighted += ((float(row["outcome"]) + 1.0) * 5.0) * weight
            total += weight
        return (weighted / total if total else None), len(rows)

    @staticmethod
    def _speed_score(calls: Sequence[sqlite3.Row]) -> float | None:
        durations = [float(row["duration_ms"]) for row in calls if row["duration_ms"] is not None]
        if not durations:
            return None
        p50 = median(durations)
        # Monotonic, interpretable mapping: ~9 at 0.5s, 6.7 at 2.5s, 5 at 5s.
        return round(10.0 / (1.0 + p50 / 5000.0), 3)

    @staticmethod
    def _reliability_score(calls: Sequence[sqlite3.Row]) -> float | None:
        if not calls:
            return None
        success = sum(row["finish_reason"] not in {"error", "cancelled"} for row in calls)
        rate_limits = sum(row["error_status_code"] == 429 for row in calls)
        base = success / len(calls)
        penalty = min(0.25, rate_limits / max(1, len(calls)) * 0.5)
        return round(max(0.0, min(10.0, (base - penalty) * 10.0)), 3)

    @staticmethod
    def _cost_score(calls: Sequence[sqlite3.Row]) -> float | None:
        costs = [float(row["estimated_cost"]) for row in calls if row["estimated_cost"] is not None]
        if not costs:
            return None
        avg = sum(costs) / len(costs)
        # This is an absolute convenience score only; recommendation re-normalizes
        # costs among current candidates so different task sizes do not dominate.
        return round(10.0 / (1.0 + avg * 20.0), 3)

    @staticmethod
    def _trend(calls: Sequence[sqlite3.Row]) -> float:
        if len(calls) < 8:
            return 0.0
        split = len(calls) // 2
        old = calls[:split]
        new = calls[split:]
        old_rate = sum(row["finish_reason"] not in {"error", "cancelled"} for row in old) / len(old)
        new_rate = sum(row["finish_reason"] not in {"error", "cancelled"} for row in new) / len(new)
        return round((new_rate - old_rate) * 10.0, 3)

    async def refresh_scores(self) -> dict[str, dict[str, object]]:
        now_ms = time.time_ns() // 1_000_000
        since_ms = now_ms - self._WINDOW_DAYS * 86_400_000
        output: dict[str, dict[str, object]] = {}
        for offering_id in list(self._offerings):
            calls, quality_rows = await asyncio.gather(
                asyncio.to_thread(self.store.calls_for, offering_id, since_ms=since_ms),
                asyncio.to_thread(self.store.quality_for, offering_id, since_ms=since_ms),
            )
            quality, quality_samples = self._score_quality(quality_rows, now_ms)
            samples = len(calls)
            confidence = 1.0 - math.exp(-(samples + quality_samples * 3) / 30.0)
            score: dict[str, object] = {
                "calculated_at_ms": now_ms,
                "quality": quality,
                "speed": self._speed_score(calls),
                "reliability": self._reliability_score(calls),
                "cost": self._cost_score(calls),
                "confidence": round(confidence, 4),
                "trend": self._trend(calls),
                "samples": samples,
                "quality_samples": quality_samples,
                "metrics": {
                    "window_days": self._WINDOW_DAYS,
                    "rate_limits": sum(row["error_status_code"] == 429 for row in calls),
                    "errors": sum(row["finish_reason"] == "error" for row in calls),
                },
            }
            await asyncio.to_thread(self.store.save_score, offering_id, score)
            output[offering_id] = score
        await self._maybe_maintenance(now_ms)
        return output

    async def _maybe_maintenance(self, now_ms: int) -> None:
        if now_ms - self._last_maintenance_ms < 86_400_000:
            return
        with self._maintenance_lock:
            if now_ms - self._last_maintenance_ms < 86_400_000:
                return
            self._last_maintenance_ms = now_ms
        fleet_cfg = getattr(self._config, "model_fleet", None)
        retention = getattr(fleet_cfg, "retention", None)
        await asyncio.to_thread(
            self.store.maintenance,
            raw_calls_days=int(getattr(retention, "raw_calls_days", 30)),
            hourly_days=int(getattr(retention, "hourly_days", 180)),
            score_full_days=int(getattr(retention, "score_full_days", 30)),
        )

    async def status(self, *, refresh: bool = True) -> dict[str, object]:
        if refresh:
            await self.refresh_scores()
        scores = self.store.latest_scores()
        rows = {row["offering_id"]: row for row in self.store.offering_rows()}
        offerings: list[dict[str, object]] = []
        for offering_id, offering in sorted(self._offerings.items()):
            db_row = rows.get(offering_id)
            score = scores.get(offering_id)
            admission = await self.admission.snapshot(offering)
            offerings.append({
                "offering_id": offering_id,
                "preset": offering.preset_name,
                "provider": offering.provider,
                "model": offering.model,
                "pools": list(offering.pools),
                "supports_vision": offering.supports_vision,
                "context_window_tokens": offering.context_window_tokens,
                "score": None if score is None else {
                    "quality": score["quality"],
                    "speed": score["speed"],
                    "reliability": score["reliability"],
                    "cost": score["cost"],
                    "confidence": score["confidence"],
                    "trend": score["trend"],
                    "samples": score["samples"],
                },
                "admission": admission,
                "declared_cost": None if db_row is None else {
                    "input_per_million": db_row["input_cost_per_million"],
                    "output_per_million": db_row["output_cost_per_million"],
                    "cached_input_per_million": db_row["cached_input_cost_per_million"],
                },
            })
        return {"status": "ok", "offerings": offerings}

    @staticmethod
    def _weights(task_type: str) -> dict[str, float]:
        task = task_type.strip().lower()
        if task in {"interactive", "chat"}:
            return {"quality": .25, "reliability": .20, "speed": .30, "cost": .10, "capacity": .15}
        if task in {"batch", "background", "dream"}:
            return {"quality": .20, "reliability": .15, "speed": .15, "cost": .35, "capacity": .15}
        return {"quality": .45, "reliability": .20, "speed": .15, "cost": .10, "capacity": .10}

    @staticmethod
    def _normalize(values: Mapping[str, float], *, lower_is_better: bool = False) -> dict[str, float]:
        if not values:
            return {}
        lo = min(values.values())
        hi = max(values.values())
        if math.isclose(lo, hi):
            return {key: 5.0 for key in values}
        result = {key: (value - lo) / (hi - lo) * 10.0 for key, value in values.items()}
        if lower_is_better:
            return {key: 10.0 - value for key, value in result.items()}
        return result

    async def recommend(
        self,
        *,
        pool: str | None = None,
        task_type: str = "general",
        requires_vision: bool = False,
        min_context_tokens: int | None = None,
    ) -> dict[str, object]:
        await self.refresh_scores()
        latest = self.store.latest_scores()
        candidates = [
            offering for offering in self._offerings.values()
            if (not pool or pool in offering.pools)
            and (not requires_vision or offering.supports_vision)
            and (
                min_context_tokens is None
                or (offering.context_window_tokens or 0) >= min_context_tokens
            )
        ]
        if not candidates:
            return {"status": "error", "message": "No model offering satisfies the requested constraints"}

        admission = {item.offering_id: await self.admission.snapshot(item) for item in candidates}
        # Candidate-relative cost uses declared rates, not model-name assumptions.
        cost_raw = {
            item.offering_id: float(item.input_cost_per_million or 0.0) + float(item.output_cost_per_million or 0.0)
            for item in candidates
            if item.input_cost_per_million is not None or item.output_cost_per_million is not None
        }
        cost_norm = self._normalize(cost_raw, lower_is_better=True)
        weights = self._weights(task_type)
        ranked: list[dict[str, object]] = []
        for item in candidates:
            row = latest.get(item.offering_id)
            dims: dict[str, float] = {}
            if row is not None:
                for dim in ("quality", "reliability", "speed"):
                    if row[dim] is not None:
                        dims[dim] = float(row[dim])
            if item.offering_id in cost_norm:
                dims["cost"] = cost_norm[item.offering_id]
            cap = admission[item.offering_id]
            cooldown = float(cap["cooldown_seconds"])
            limit = cap["offering_limit"] or cap["provider_limit"]
            active = max(int(cap["offering_active"]), int(cap["provider_active"]))
            if cooldown > 0:
                dims["capacity"] = 0.0
            elif isinstance(limit, int) and limit > 0:
                dims["capacity"] = max(0.0, 10.0 * (1.0 - active / limit))
            else:
                dims["capacity"] = 10.0
            available_weight = sum(weights[key] for key in dims)
            score = (
                sum(dims[key] * weights[key] for key in dims) / available_weight
                if available_weight else 0.0
            )
            confidence = float(row["confidence"]) if row is not None else 0.0
            # Avoid over-trusting tiny samples while still allowing cold-start exploration.
            adjusted = score * (0.65 + 0.35 * confidence)
            ranked.append({
                "offering_id": item.offering_id,
                "preset": item.preset_name,
                "provider": item.provider,
                "model": item.model,
                "score": round(score, 3),
                "adjusted_score": round(adjusted, 3),
                "confidence": round(confidence, 4),
                "dimensions": {key: round(value, 3) for key, value in dims.items()},
                "admission": cap,
            })
        ranked.sort(key=lambda row: (float(row["adjusted_score"]), float(row["confidence"])), reverse=True)
        return {
            "status": "ok",
            "task_type": task_type,
            "pool": pool,
            "recommended": ranked[0],
            "candidates": ranked,
            "note": "Performance dimensions come from this offering's real observed traffic; unknown dimensions are not inferred from model names.",
        }


_FLEETS: dict[str, ModelFleetManager] = {}
_FLEETS_LOCK = threading.RLock()


def _fleet_key(config: Any) -> str:
    data_dir = getattr(config, "runtime_data_dir", None)
    if data_dir is not None:
        return str(Path(data_dir).resolve())
    return f"memory:{id(config)}"


def get_model_fleet(config: Any) -> ModelFleetManager:
    """Return the process-shared fleet for one nanobot instance."""
    key = _fleet_key(config)
    with _FLEETS_LOCK:
        fleet = _FLEETS.get(key)
        if fleet is None:
            data_dir = getattr(config, "runtime_data_dir", None)
            path: Path | None = None
            if data_dir is not None:
                path = Path(data_dir) / "model_fleet.db"
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                except OSError:
                    logger.warning("Could not create model fleet data dir {}; using memory", path.parent)
                    path = None
            fleet = ModelFleetManager(ModelFleetStore(path), config=config)
            _FLEETS[key] = fleet
        else:
            fleet._config = config
        return fleet


def offering_from_config(
    config: Any,
    *,
    preset: Any,
    preset_name: str | None,
    provider_name: str,
) -> ModelOffering:
    """Build a safe offering identity/facts object from resolved configuration."""
    model = str(preset.model)
    explicit_id = str(getattr(preset, "offering_id", "") or "").strip()
    offering_id = explicit_id or f"{provider_name}:{model}"
    provider_cfg = config.get_provider(model, preset=preset)
    return ModelOffering(
        offering_id=offering_id,
        provider=provider_name,
        model=model,
        preset_name=preset_name,
        pools=tuple(str(item) for item in (getattr(preset, "fleet_pools", None) or [])),
        input_cost_per_million=getattr(preset, "input_cost_per_million", None),
        output_cost_per_million=getattr(preset, "output_cost_per_million", None),
        cached_input_cost_per_million=getattr(preset, "cached_input_cost_per_million", None),
        max_concurrent_requests=getattr(preset, "max_concurrent_requests", None),
        provider_max_concurrent_requests=getattr(provider_cfg, "max_concurrent_requests", None),
        rate_limit_scope=getattr(provider_cfg, "rate_limit_scope", "provider"),
        supports_vision=bool(getattr(preset, "supports_vision", False)),
        context_window_tokens=getattr(preset, "context_window_tokens", None),
    )


def current_fleet_priority() -> FleetPriority:
    """Infer Main/worker/background priority without leaking task identity into providers."""
    try:
        from nanobot.agent.tools.context import current_request_context
        request = current_request_context()
        if request is not None and request.exec_owner_session_key:
            return "worker"
        channel = (request.channel if request is not None else "").lower()
        if channel in {"system", "cron", "dream"}:
            return "background"
    except Exception:
        pass
    try:
        from nanobot.llm_usage.context import current_llm_usage_source
        if current_llm_usage_source() in {"cron", "dream", "system"}:
            return "background"
    except Exception:
        pass
    return "main"
