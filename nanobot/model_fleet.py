"""Passive model-fleet telemetry, scoring, retention and request admission.

A fleet offering is a concrete configured provider identity plus model route.
Performance is learned from that route's real traffic; model names never imply
quality, speed or reliability.
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
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, AsyncIterator, Literal, Mapping, Sequence

from loguru import logger

from nanobot.config.schema import ProviderConfig
from nanobot.model_domain import get_model
from nanobot.providers.registry import find_by_name

FleetPriority = Literal["main", "worker", "background", "dream"]
RateLimitScope = Literal["provider", "model"]


@dataclass(frozen=True, slots=True)
class ModelOffering:
    offering_id: str
    provider: str
    model: str
    model_id: str | None = None
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
class _Waiter:
    sequence: int
    priority: FleetPriority
    provider: str
    offering_id: str


@dataclass(slots=True)
class _Pool:
    configured_limit: int | None = None
    adaptive_limit: int | None = None
    active: int = 0
    cooldown_until: float = 0.0
    success_streak: int = 0
    recent_429: int = 0

    @property
    def limit(self) -> int | None:
        if self.adaptive_limit is None:
            return self.configured_limit
        if self.configured_limit is None:
            return self.adaptive_limit
        return min(self.configured_limit, self.adaptive_limit)


class ModelAdmissionController:
    """Provider + offering admission with shared cooldown and adaptive limits."""

    _RECOVERY_STEP = 8
    _UNLIMITED_RECOVERY = 32

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._providers: dict[str, _Pool] = {}
        self._offerings: dict[str, _Pool] = {}
        self._waiters: deque[_Waiter] = deque()
        self._sequence = 0

    def configure(self, offering: ModelOffering) -> None:
        provider = self._providers.setdefault(offering.provider, _Pool())
        provider.configured_limit = offering.provider_max_concurrent_requests
        model = self._offerings.setdefault(offering.offering_id, _Pool())
        model.configured_limit = offering.max_concurrent_requests

    @staticmethod
    def _rank(priority: FleetPriority) -> int:
        return {"main": 0, "worker": 1, "background": 2, "dream": 3}[priority]

    def _candidate(self) -> _Waiter | None:
        if not self._waiters:
            return None
        oldest = self._waiters[0]
        best = min(self._waiters, key=lambda item: (self._rank(item.priority), item.sequence))
        # Bounded fairness: after eight overtakes, let the oldest request pass.
        if best.sequence - oldest.sequence >= 8:
            return oldest
        return best

    @staticmethod
    def _capacity(pool: _Pool) -> bool:
        return pool.limit is None or pool.active < pool.limit

    def _can_enter(self, waiter: _Waiter, now: float) -> bool:
        if self._candidate() is not waiter:
            return False
        provider = self._providers[waiter.provider]
        offering = self._offerings[waiter.offering_id]
        return (
            provider.cooldown_until <= now
            and offering.cooldown_until <= now
            and self._capacity(provider)
            and self._capacity(offering)
        )

    async def acquire(self, offering: ModelOffering, *, priority: FleetPriority) -> None:
        self.configure(offering)
        async with self._condition:
            self._sequence += 1
            waiter = _Waiter(self._sequence, priority, offering.provider, offering.offering_id)
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
                    waits = [
                        value - now
                        for value in (
                            self._providers[offering.provider].cooldown_until,
                            self._offerings[offering.offering_id].cooldown_until,
                        )
                        if value > now
                    ]
                    if not waits:
                        await self._condition.wait()
                    else:
                        try:
                            await asyncio.wait_for(self._condition.wait(), timeout=min(waits))
                        except TimeoutError:
                            pass
            except asyncio.CancelledError:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                self._condition.notify_all()
                raise

    async def release(self, offering: ModelOffering) -> None:
        async with self._condition:
            provider = self._providers.setdefault(offering.provider, _Pool())
            model = self._offerings.setdefault(offering.offering_id, _Pool())
            provider.active = max(0, provider.active - 1)
            model.active = max(0, model.active - 1)
            self._condition.notify_all()

    @staticmethod
    def _reduce(pool: _Pool) -> None:
        current = pool.limit
        pool.adaptive_limit = (
            max(1, pool.active - 1)
            if current is None and pool.active > 1
            else max(1, (current or 1) // 2)
        )
        pool.success_streak = 0
        pool.recent_429 += 1

    @classmethod
    def _recover(cls, pool: _Pool) -> None:
        if pool.adaptive_limit is None:
            return
        pool.success_streak += 1
        if pool.configured_limit is None:
            if pool.success_streak >= cls._UNLIMITED_RECOVERY:
                pool.adaptive_limit = None
                pool.success_streak = 0
            elif pool.success_streak % cls._RECOVERY_STEP == 0:
                pool.adaptive_limit += 1
        elif pool.success_streak >= cls._RECOVERY_STEP:
            pool.adaptive_limit = min(pool.configured_limit, pool.adaptive_limit + 1)
            pool.success_streak = 0

    async def note_rate_limit(self, offering: ModelOffering, *, retry_after_s: float | None) -> None:
        self.configure(offering)
        async with self._condition:
            target = (
                self._providers[offering.provider]
                if offering.rate_limit_scope == "provider"
                else self._offerings[offering.offering_id]
            )
            target.cooldown_until = max(
                target.cooldown_until,
                time.monotonic() + max(1.0, float(retry_after_s or 1.0)),
            )
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
                "provider_limit": provider.limit,
                "offering_active": model.active,
                "offering_limit": model.limit,
                "queued": sum(item.offering_id == offering.offering_id for item in self._waiters),
                "cooldown_seconds": round(max(
                    0.0,
                    provider.cooldown_until - now,
                    model.cooldown_until - now,
                ), 3),
                "recent_429": provider.recent_429 + model.recent_429,
            }


class ModelFleetStore:
    """SQLite persistence for content-free fleet telemetry."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path) if path is not None else ":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._db:
            self._db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS offerings (
                    offering_id TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
                    model_id TEXT, pools_json TEXT NOT NULL DEFAULT '[]',
                    input_cost_per_million REAL, output_cost_per_million REAL,
                    cached_input_cost_per_million REAL, supports_vision INTEGER NOT NULL DEFAULT 0,
                    context_window_tokens INTEGER, updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, offering_id TEXT NOT NULL,
                    started_at_ms INTEGER NOT NULL, duration_ms INTEGER NOT NULL,
                    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
                    generation_ms INTEGER, ttft_ms INTEGER, finish_reason TEXT NOT NULL,
                    error_status_code INTEGER, error_kind TEXT, estimated_cost REAL
                );
                CREATE INDEX IF NOT EXISTS calls_offering_time ON calls(offering_id, started_at_ms);
                CREATE TABLE IF NOT EXISTS quality_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, offering_id TEXT NOT NULL,
                    dimension TEXT NOT NULL, outcome REAL NOT NULL, weight REAL NOT NULL,
                    evidence TEXT, created_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS quality_offering_time ON quality_events(offering_id, created_at_ms);
                CREATE TABLE IF NOT EXISTS score_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, offering_id TEXT NOT NULL,
                    calculated_at_ms INTEGER NOT NULL, quality REAL, speed REAL,
                    reliability REAL, cost REAL, confidence REAL NOT NULL, trend REAL NOT NULL,
                    samples INTEGER NOT NULL, metrics_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scores_offering_time ON score_snapshots(offering_id, calculated_at_ms);
                CREATE TABLE IF NOT EXISTS aggregates (
                    offering_id TEXT NOT NULL, bucket_kind TEXT NOT NULL,
                    bucket_start_ms INTEGER NOT NULL, calls INTEGER NOT NULL,
                    successes INTEGER NOT NULL, rate_limits INTEGER NOT NULL,
                    avg_duration_ms REAL, avg_ttft_ms REAL, avg_generation_ms REAL,
                    total_cost REAL,
                    PRIMARY KEY(offering_id, bucket_kind, bucket_start_ms)
                );
                """
            )
            offering_columns = {
                str(row["name"])
                for row in self._db.execute("PRAGMA table_info(offerings)")
            }
            if "preset_name" in offering_columns and "model_id" not in offering_columns:
                self._db.execute(
                    "ALTER TABLE offerings RENAME COLUMN preset_name TO model_id"
                )

    def upsert_offering(self, offering: ModelOffering) -> None:
        with self._lock, self._db:
            self._db.execute(
                """
                INSERT INTO offerings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(offering_id) DO UPDATE SET
                    provider=excluded.provider, model=excluded.model,
                    model_id=COALESCE(excluded.model_id, offerings.model_id),
                    pools_json=excluded.pools_json,
                    input_cost_per_million=excluded.input_cost_per_million,
                    output_cost_per_million=excluded.output_cost_per_million,
                    cached_input_cost_per_million=excluded.cached_input_cost_per_million,
                    supports_vision=excluded.supports_vision,
                    context_window_tokens=excluded.context_window_tokens,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (
                    offering.offering_id, offering.provider, offering.model, offering.model_id,
                    json.dumps(list(offering.pools)), offering.input_cost_per_million,
                    offering.output_cost_per_million, offering.cached_input_cost_per_million,
                    int(offering.supports_vision), offering.context_window_tokens,
                    time.time_ns() // 1_000_000,
                ),
            )

    @staticmethod
    def _cost(offering: ModelOffering, usage: Any | None) -> float | None:
        if usage is None:
            return None
        rates = (
            offering.input_cost_per_million,
            offering.output_cost_per_million,
            offering.cached_input_cost_per_million,
        )
        if all(rate is None for rate in rates):
            return None
        cache = int(getattr(usage, "cache_read_tokens", 0) or 0)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        input_rate = float(offering.input_cost_per_million or 0.0)
        cache_rate = float(
            offering.cached_input_cost_per_million
            if offering.cached_input_cost_per_million is not None
            else input_rate
        )
        return (
            max(0, input_tokens - cache) * input_rate
            + cache * cache_rate
            + output_tokens * float(offering.output_cost_per_million or 0.0)
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
        with self._lock, self._db:
            self._db.execute(
                """
                INSERT INTO calls(
                    offering_id, started_at_ms, duration_ms, input_tokens, output_tokens,
                    cache_read_tokens, generation_ms, ttft_ms, finish_reason,
                    error_status_code, error_kind, estimated_cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    offering.offering_id, started_at_ms, duration_ms,
                    getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None),
                    getattr(usage, "cache_read_tokens", None), getattr(usage, "generation_ms", None),
                    getattr(usage, "ttft_ms", None),
                    str(getattr(response, "finish_reason", "unknown") or "unknown"),
                    getattr(response, "error_status_code", None), getattr(response, "error_kind", None),
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
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO quality_events VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                (
                    offering_id, dimension, max(-1.0, min(1.0, outcome)),
                    max(0.01, min(10.0, weight)), (evidence or "")[:500] or None,
                    time.time_ns() // 1_000_000,
                ),
            )

    def offerings(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute("SELECT * FROM offerings ORDER BY offering_id"))

    def calls_for(self, offering_id: str, since_ms: int) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute(
                "SELECT * FROM calls WHERE offering_id=? AND started_at_ms>=? ORDER BY started_at_ms",
                (offering_id, since_ms),
            ))

    def quality_for(self, offering_id: str, since_ms: int) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute(
                "SELECT * FROM quality_events WHERE offering_id=? AND created_at_ms>=? ORDER BY created_at_ms",
                (offering_id, since_ms),
            ))

    def save_score(self, offering_id: str, score: Mapping[str, Any]) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO score_snapshots VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    offering_id, int(score["calculated_at_ms"]), score.get("quality"),
                    score.get("speed"), score.get("reliability"), score.get("cost"),
                    float(score["confidence"]), float(score["trend"]), int(score["samples"]),
                    json.dumps(score.get("metrics", {}), sort_keys=True),
                ),
            )

    def latest_scores(self) -> dict[str, sqlite3.Row]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT s.* FROM score_snapshots s JOIN (
                    SELECT offering_id, MAX(id) AS id FROM score_snapshots GROUP BY offering_id
                ) x ON x.id=s.id
                """
            )
            return {row["offering_id"]: row for row in rows}

    def _aggregate_before(self, cutoff_ms: int, kind: str, bucket_ms: int) -> None:
        rows = list(self._db.execute(
            """
            SELECT offering_id, (started_at_ms / ?) * ? AS bucket,
                   COUNT(*), SUM(CASE WHEN finish_reason NOT IN ('error','cancelled') THEN 1 ELSE 0 END),
                   SUM(CASE WHEN error_status_code=429 THEN 1 ELSE 0 END),
                   AVG(duration_ms), AVG(ttft_ms), AVG(generation_ms), SUM(estimated_cost)
            FROM calls WHERE started_at_ms < ? GROUP BY offering_id, bucket
            """,
            (bucket_ms, bucket_ms, cutoff_ms),
        ))
        for row in rows:
            self._db.execute(
                "INSERT OR REPLACE INTO aggregates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row[0], kind, *row[1:]),
            )

    def maintenance(self, *, raw_calls_days: int, hourly_days: int, score_full_days: int) -> None:
        now_ms = time.time_ns() // 1_000_000
        day_ms = 86_400_000
        raw_cutoff = now_ms - raw_calls_days * day_ms
        with self._lock, self._db:
            # Aggregate first; deletion never destroys long-term trend data.
            self._aggregate_before(raw_cutoff, "daily", day_ms)
            self._aggregate_before(raw_cutoff, "hourly", 3_600_000)
            self._db.execute("DELETE FROM calls WHERE started_at_ms < ?", (raw_cutoff,))
            self._db.execute(
                "DELETE FROM aggregates WHERE bucket_kind='hourly' AND bucket_start_ms < ?",
                (now_ms - hourly_days * day_ms,),
            )
            score_cutoff = now_ms - score_full_days * day_ms
            self._db.execute(
                """
                DELETE FROM score_snapshots WHERE calculated_at_ms < ? AND id NOT IN (
                    SELECT MAX(id) FROM score_snapshots WHERE calculated_at_ms < ?
                    GROUP BY offering_id, calculated_at_ms / ?
                )
                """,
                (score_cutoff, score_cutoff, day_ms),
            )


class ModelFleetManager:
    """Main-visible fleet platform using only passive real-work evidence."""

    _WINDOW_DAYS = 7

    def __init__(self, store: ModelFleetStore, *, config: Any) -> None:
        self.store = store
        self.admission = ModelAdmissionController()
        self._offerings: dict[str, ModelOffering] = {}
        self._config = config
        self._last_refresh_ms = 0
        self._last_maintenance_ms = 0
        self._refresh_lock = asyncio.Lock()
        self._maintenance_lock = threading.Lock()

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
        # Periodic score refresh consumes no model tokens; active traffic merely
        # triggers a recomputation when the configured interval has elapsed.
        interval = int(getattr(getattr(self._config, "model_fleet", None), "score_refresh_minutes", 60))
        now_ms = time.time_ns() // 1_000_000
        if now_ms - self._last_refresh_ms >= interval * 60_000:
            await self.refresh_scores()

    async def note_response(self, offering: ModelOffering, response: Any) -> None:
        if getattr(response, "error_status_code", None) == 429:
            try:
                from nanobot.providers.base import LLMProvider
                retryable = LLMProvider._is_retryable_429_response(response)
            except Exception:
                retryable = bool(getattr(response, "error_should_retry", True))
            if retryable:
                await self.admission.note_rate_limit(
                    offering,
                    retry_after_s=(
                        getattr(response, "error_retry_after_s", None)
                        or getattr(response, "retry_after", None)
                    ),
                )
                return
        if getattr(response, "finish_reason", None) not in {"error", "cancelled"}:
            await self.admission.note_success(offering)

    @asynccontextmanager
    async def slot(self, offering: ModelOffering, *, priority: FleetPriority) -> AsyncIterator[None]:
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
    def _quality(rows: Sequence[sqlite3.Row], now_ms: int) -> tuple[float | None, int]:
        if not rows:
            return None, 0
        weighted = total = 0.0
        for row in rows:
            age_days = max(0.0, (now_ms - int(row["created_at_ms"])) / 86_400_000.0)
            weight = float(row["weight"]) * (0.5 ** (age_days / 14.0))
            weighted += ((float(row["outcome"]) + 1.0) * 5.0) * weight
            total += weight
        return (weighted / total if total else None), len(rows)

    @staticmethod
    def _speed(calls: Sequence[sqlite3.Row]) -> float | None:
        values = [float(row["duration_ms"]) for row in calls if row["duration_ms"] is not None]
        return None if not values else round(10.0 / (1.0 + median(values) / 5000.0), 3)

    @staticmethod
    def _reliability(calls: Sequence[sqlite3.Row]) -> float | None:
        if not calls:
            return None
        successes = sum(row["finish_reason"] not in {"error", "cancelled"} for row in calls)
        rate_limits = sum(row["error_status_code"] == 429 for row in calls)
        value = successes / len(calls) - min(0.25, rate_limits / len(calls) * 0.5)
        return round(max(0.0, min(10.0, value * 10.0)), 3)

    @staticmethod
    def _cost(calls: Sequence[sqlite3.Row]) -> float | None:
        values = [float(row["estimated_cost"]) for row in calls if row["estimated_cost"] is not None]
        return None if not values else round(10.0 / (1.0 + (sum(values) / len(values)) * 20.0), 3)

    @staticmethod
    def _trend(calls: Sequence[sqlite3.Row]) -> float:
        if len(calls) < 8:
            return 0.0
        half = len(calls) // 2
        def rate(rows: Sequence[sqlite3.Row]) -> float:
            return sum(row["finish_reason"] not in {"error", "cancelled"} for row in rows) / len(rows)
        return round((rate(calls[half:]) - rate(calls[:half])) * 10.0, 3)

    async def refresh_scores(self) -> dict[str, dict[str, object]]:
        async with self._refresh_lock:
            now_ms = time.time_ns() // 1_000_000
            since_ms = now_ms - self._WINDOW_DAYS * 86_400_000
            output: dict[str, dict[str, object]] = {}
            for offering_id in list(self._offerings):
                calls, quality_rows = await asyncio.gather(
                    asyncio.to_thread(self.store.calls_for, offering_id, since_ms),
                    asyncio.to_thread(self.store.quality_for, offering_id, since_ms),
                )
                quality, quality_samples = self._quality(quality_rows, now_ms)
                confidence = 1.0 - math.exp(-(len(calls) + quality_samples * 3) / 30.0)
                score: dict[str, object] = {
                    "calculated_at_ms": now_ms,
                    "quality": quality,
                    "speed": self._speed(calls),
                    "reliability": self._reliability(calls),
                    "cost": self._cost(calls),
                    "confidence": round(confidence, 4),
                    "trend": self._trend(calls),
                    "samples": len(calls),
                    "quality_samples": quality_samples,
                    "metrics": {
                        "window_days": self._WINDOW_DAYS,
                        "rate_limits": sum(row["error_status_code"] == 429 for row in calls),
                        "errors": sum(row["finish_reason"] == "error" for row in calls),
                    },
                }
                await asyncio.to_thread(self.store.save_score, offering_id, score)
                output[offering_id] = score
            self._last_refresh_ms = now_ms
            await self._maybe_maintenance(now_ms)
            return output

    async def _maybe_maintenance(self, now_ms: int) -> None:
        if now_ms - self._last_maintenance_ms < 86_400_000:
            return
        with self._maintenance_lock:
            if now_ms - self._last_maintenance_ms < 86_400_000:
                return
            self._last_maintenance_ms = now_ms
        retention = getattr(getattr(self._config, "model_fleet", None), "retention", None)
        await asyncio.to_thread(
            self.store.maintenance,
            raw_calls_days=int(getattr(retention, "raw_calls_days", 30)),
            hourly_days=int(getattr(retention, "hourly_days", 180)),
            score_full_days=int(getattr(retention, "score_full_days", 30)),
        )

    async def status(self, *, refresh: bool = True) -> dict[str, object]:
        if refresh:
            await self.refresh_scores()
        latest = self.store.latest_scores()
        rows = {row["offering_id"]: row for row in self.store.offerings()}
        items: list[dict[str, object]] = []
        for offering_id, offering in sorted(self._offerings.items()):
            score = latest.get(offering_id)
            declared = rows.get(offering_id)
            items.append({
                "offering_id": offering_id,
                "model_id": offering.model_id,
                "provider": offering.provider,
                "model": offering.model,
                "pools": list(offering.pools),
                "supports_vision": offering.supports_vision,
                "context_window_tokens": offering.context_window_tokens,
                "score": None if score is None else {
                    key: score[key]
                    for key in ("quality", "speed", "reliability", "cost", "confidence", "trend", "samples")
                },
                "admission": await self.admission.snapshot(offering),
                "declared_cost": None if declared is None else {
                    "input_per_million": declared["input_cost_per_million"],
                    "output_per_million": declared["output_cost_per_million"],
                    "cached_input_per_million": declared["cached_input_cost_per_million"],
                },
            })
        return {"status": "ok", "offerings": items}

    @staticmethod
    def _weights(task_type: str) -> dict[str, float]:
        task = task_type.strip().lower()
        if task in {"interactive", "chat"}:
            return {"quality": .25, "reliability": .20, "speed": .30, "cost": .10, "capacity": .15}
        if task in {"batch", "background"}:
            return {"quality": .20, "reliability": .15, "speed": .15, "cost": .35, "capacity": .15}
        return {"quality": .45, "reliability": .20, "speed": .15, "cost": .10, "capacity": .10}

    @staticmethod
    def _normalize(values: Mapping[str, float], *, lower_is_better: bool = False) -> dict[str, float]:
        if not values:
            return {}
        lo, hi = min(values.values()), max(values.values())
        if math.isclose(lo, hi):
            return {key: 5.0 for key in values}
        out = {key: (value - lo) / (hi - lo) * 10.0 for key, value in values.items()}
        return {key: 10.0 - value for key, value in out.items()} if lower_is_better else out

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
            item for item in self._offerings.values()
            if (not pool or pool in item.pools)
            and (not requires_vision or item.supports_vision)
            and (min_context_tokens is None or (item.context_window_tokens or 0) >= min_context_tokens)
        ]
        if not candidates:
            return {"status": "error", "message": "No model offering satisfies the requested constraints"}
        admission = {item.offering_id: await self.admission.snapshot(item) for item in candidates}
        cost_raw = {
            item.offering_id: float(item.input_cost_per_million or 0.0) + float(item.output_cost_per_million or 0.0)
            for item in candidates
            if item.input_cost_per_million is not None or item.output_cost_per_million is not None
        }
        cost_score = self._normalize(cost_raw, lower_is_better=True)
        weights = self._weights(task_type)
        ranked: list[dict[str, object]] = []
        for item in candidates:
            row = latest.get(item.offering_id)
            dims: dict[str, float] = {}
            if row is not None:
                for dim in ("quality", "reliability", "speed"):
                    if row[dim] is not None:
                        dims[dim] = float(row[dim])
            if item.offering_id in cost_score:
                dims["cost"] = cost_score[item.offering_id]
            cap = admission[item.offering_id]
            limit = cap["offering_limit"] or cap["provider_limit"]
            active = max(int(cap["offering_active"]), int(cap["provider_active"]))
            if float(cap["cooldown_seconds"]) > 0:
                dims["capacity"] = 0.0
            elif isinstance(limit, int) and limit > 0:
                dims["capacity"] = max(0.0, 10.0 * (1.0 - active / limit))
            else:
                dims["capacity"] = 10.0
            available = sum(weights[key] for key in dims)
            raw = sum(dims[key] * weights[key] for key in dims) / available if available else 0.0
            confidence = float(row["confidence"]) if row is not None else 0.0
            adjusted = raw * (0.65 + 0.35 * confidence)
            ranked.append({
                "offering_id": item.offering_id,
                "model_id": item.model_id,
                "provider": item.provider,
                "model": item.model,
                "score": round(raw, 3),
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
            "note": "Performance comes from this offering's observed traffic; unknown dimensions are not inferred from model names.",
        }


_FLEETS: dict[str, ModelFleetManager] = {}
_FLEETS_LOCK = threading.RLock()


def _fleet_key(config: Any) -> str:
    data_dir = getattr(config, "runtime_data_dir", None)
    return str(Path(data_dir).resolve()) if data_dir is not None else f"memory:{id(config)}"


def get_model_fleet(config: Any) -> ModelFleetManager:
    key = _fleet_key(config)
    with _FLEETS_LOCK:
        fleet = _FLEETS.get(key)
        if fleet is None:
            path: Path | None = None
            data_dir = getattr(config, "runtime_data_dir", None)
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
    model_id: str,
) -> ModelOffering:
    model_config = get_model(config.models, model_id)
    provider_name = model_config.provider
    spec = find_by_name(provider_name)
    provider_cfg = (
        getattr(config.providers, spec.name, None)
        if spec is not None
        else (config.providers.model_extra or {}).get(provider_name)
    )
    if not isinstance(provider_cfg, ProviderConfig):
        provider_cfg = None
    pricing = model_config.pricing
    return ModelOffering(
        offering_id=model_config.offering_id or f"{provider_name}:{model_config.model}",
        provider=provider_name,
        model=model_config.model,
        model_id=model_id,
        pools=tuple(model_config.pools),
        input_cost_per_million=pricing.input,
        output_cost_per_million=pricing.output,
        cached_input_cost_per_million=pricing.cache_read,
        max_concurrent_requests=model_config.max_concurrent_requests,
        provider_max_concurrent_requests=(
            provider_cfg.max_concurrent_requests if provider_cfg else None
        ),
        rate_limit_scope=(provider_cfg.rate_limit_scope if provider_cfg else "provider"),
        supports_vision=model_config.capabilities.vision,
        context_window_tokens=model_config.context_window_tokens,
    )


def current_fleet_priority() -> FleetPriority:
    try:
        from nanobot.agent.tools.context import current_request_context
        request = current_request_context()
        if request is not None and request.exec_owner_session_key:
            return "worker"
        if request is not None and request.channel.lower() == "dream":
            return "dream"
        if request is not None and request.channel.lower() in {"system", "cron"}:
            return "background"
    except Exception:
        pass
    try:
        from nanobot.llm_usage.context import current_llm_usage_source
        source = current_llm_usage_source()
        if source == "dream":
            return "dream"
        if source in {"cron", "system"}:
            return "background"
    except Exception:
        pass
    return "main"
