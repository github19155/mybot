from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from nanobot.config.schema import Config
from nanobot.model_fleet import (
    ModelAdmissionController,
    ModelFleetManager,
    ModelFleetStore,
    ModelOffering,
    offering_from_config,
)
from nanobot.providers.base import LLMProvider, LLMResponse, LLMUsage
from nanobot.providers.factory import make_provider, provider_signature
from nanobot.providers.fleet_controlled_provider import FleetControlledProvider


def _offering(
    provider: str = "openai",
    model: str = "gpt-x",
    *,
    model_id: str = "main",
    provider_limit: int | None = None,
    model_limit: int | None = None,
    scope: str = "provider",
) -> ModelOffering:
    return ModelOffering(
        offering_id=f"{provider}:{model}",
        provider=provider,
        model=model,
        model_id=model_id,
        provider_max_concurrent_requests=provider_limit,
        max_concurrent_requests=model_limit,
        rate_limit_scope=scope,  # type: ignore[arg-type]
    )


def _config() -> Config:
    return Config.model_validate({
        "agents": {"defaults": {"model_id": "main"}},
        "providers": {
            "openai": {"api_key": "x", "max_concurrent_requests": 7},
            "cpa": {
                "api_key": "y",
                "api_base": "https://cpa.invalid/v1",
                "rate_limit_scope": "model"
            }
        },
        "models": {
            "main": {
                "display_name": "Main",
                "provider": "openai",
                "model": "gpt-same",
                "capabilities": {"text": True},
                "pools": ["coding"],
                "pricing": {"input": 1.25, "output": 5.0, "cache_read": 0.5},
                "max_concurrent_requests": 3,
                "context_window_tokens": 64000
            },
            "cpa_same": {
                "display_name": "CPA same upstream",
                "provider": "cpa",
                "model": "gpt-same",
                "capabilities": {"text": True, "vision": True},
                "pools": ["coding", "vision"],
                "context_window_tokens": 128000
            }
        }
    })


@pytest.mark.asyncio
async def test_same_model_different_providers_have_independent_capacity() -> None:
    admission = ModelAdmissionController()
    openai = _offering("openai", provider_limit=1, model_id="openai_route")
    cpa = _offering("cpa", provider_limit=1, model_id="cpa_route")
    await admission.acquire(openai, priority="worker")
    acquired = asyncio.Event()

    async def acquire_cpa() -> None:
        await admission.acquire(cpa, priority="worker")
        acquired.set()

    task = asyncio.create_task(acquire_cpa())
    await asyncio.wait_for(acquired.wait(), timeout=0.5)
    await admission.release(cpa)
    await admission.release(openai)
    await task


@pytest.mark.asyncio
async def test_provider_limit_blocks_sibling_model_until_release() -> None:
    admission = ModelAdmissionController()
    one = _offering("openai", "model-a", provider_limit=1, model_id="a")
    two = _offering("openai", "model-b", provider_limit=1, model_id="b")
    await admission.acquire(one, priority="worker")
    task = asyncio.create_task(admission.acquire(two, priority="worker"))
    await asyncio.sleep(0)
    assert not task.done()
    await admission.release(one)
    await asyncio.wait_for(task, timeout=0.5)
    await admission.release(two)


@pytest.mark.asyncio
async def test_retryable_429_creates_shared_provider_cooldown() -> None:
    admission = ModelAdmissionController()
    one = _offering("openai", "model-a", scope="provider", model_id="a")
    two = _offering("openai", "model-b", scope="provider", model_id="b")
    await admission.note_rate_limit(one, retry_after_s=0.05)
    snapshot = await admission.snapshot(two)
    assert snapshot["cooldown_seconds"] > 0
    assert snapshot["recent_429"] == 1


@pytest.mark.asyncio
async def test_model_scoped_429_does_not_pause_sibling_model() -> None:
    admission = ModelAdmissionController()
    one = _offering("openai", "model-a", scope="model", model_id="a")
    two = _offering("openai", "model-b", scope="model", model_id="b")
    await admission.note_rate_limit(one, retry_after_s=1)
    assert (await admission.snapshot(one))["cooldown_seconds"] > 0
    assert (await admission.snapshot(two))["cooldown_seconds"] == 0


class _ProbeProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(provider_name="probe")
        self.responses = list(responses)

    def get_default_model(self) -> str:
        return "probe-model"

    async def chat(self, **kwargs) -> LLMResponse:
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_controlled_provider_records_real_traffic_and_shared_429(tmp_path: Path) -> None:
    fleet = ModelFleetManager(ModelFleetStore(tmp_path / "fleet.db"), config=Config())
    offering = _offering("probe", "probe-model", provider_limit=2)
    inner = _ProbeProvider([
        LLMResponse(
            content="rate limited",
            finish_reason="error",
            error_status_code=429,
            error_code="rate_limit_exceeded",
            error_should_retry=True,
            error_retry_after_s=1,
        )
    ])
    provider = FleetControlledProvider(inner, fleet=fleet, offering=offering)
    response = await provider._safe_chat(
        messages=[{"role": "user", "content": "x"}],
        model="probe-model",
        max_tokens=32,
        temperature=0.1,
        reasoning_effort=None,
        tool_choice=None,
        tools=None,
    )
    assert response.error_status_code == 429
    snapshot = await fleet.admission.snapshot(offering)
    assert snapshot["cooldown_seconds"] > 0
    assert snapshot["provider_active"] == 0


@pytest.mark.asyncio
async def test_passive_score_uses_real_calls_and_objective_quality(tmp_path: Path) -> None:
    store = ModelFleetStore(tmp_path / "fleet.db")
    fleet = ModelFleetManager(store, config=Config())
    offering = ModelOffering(
        offering_id="provider-x:model-a",
        provider="provider-x",
        model="model-a",
        model_id="coding",
        pools=("coding",),
        input_cost_per_million=1.0,
        output_cost_per_million=2.0,
    )
    fleet.bind_offering(offering)
    now = time.time_ns() // 1_000_000
    usage = LLMUsage.reported(input_tokens=1000, output_tokens=500)
    for idx in range(10):
        store.record_call(
            offering,
            started_at_ms=now + idx,
            duration_ms=1000,
            response=LLMResponse(content="ok", finish_reason="stop", usage=usage),
            usage=usage,
        )
    fleet.record_quality(
        offering.offering_id,
        dimension="coding",
        outcome=1.0,
        weight=2.0,
        evidence="tests passed",
    )
    score = (await fleet.refresh_scores())[offering.offering_id]
    assert score["quality"] == pytest.approx(10.0)
    assert score["reliability"] == pytest.approx(10.0)
    assert score["confidence"] > 0


@pytest.mark.asyncio
async def test_recommendation_returns_model_id_and_filters_pool_capability(tmp_path: Path) -> None:
    config = _config()
    fleet = ModelFleetManager(ModelFleetStore(tmp_path / "fleet.db"), config=config)
    fleet.bind_offering(offering_from_config(config, model_id="main"))
    fleet.bind_offering(offering_from_config(config, model_id="cpa_same"))
    result = await fleet.recommend(pool="coding", requires_vision=True)
    assert result["status"] == "ok"
    recommended = result["recommended"]
    assert isinstance(recommended, dict)
    assert recommended["model_id"] == "cpa_same"
    assert "preset" not in recommended


def test_offering_identity_is_provider_plus_upstream_and_model_ids_are_distinct() -> None:
    config = _config()
    a = offering_from_config(config, model_id="main")
    b = offering_from_config(config, model_id="cpa_same")
    assert a.model == b.model == "gpt-same"
    assert a.provider == "openai"
    assert b.provider == "cpa"
    assert a.model_id == "main"
    assert b.model_id == "cpa_same"
    assert a.offering_id != b.offering_id


def test_offering_maps_static_model_facts_and_provider_account_limits() -> None:
    offering = offering_from_config(_config(), model_id="main")
    assert offering.pools == ("coding",)
    assert offering.input_cost_per_million == 1.25
    assert offering.output_cost_per_million == 5.0
    assert offering.cached_input_cost_per_million == 0.5
    assert offering.max_concurrent_requests == 3
    assert offering.provider_max_concurrent_requests == 7
    assert offering.context_window_tokens == 64000


def test_provider_signature_changes_with_fleet_route_facts() -> None:
    config = _config()
    first = provider_signature(config, model_id="main")
    changed = config.model_copy(deep=True)
    changed.models["main"].pools.append("batch")
    assert provider_signature(changed, model_id="main") != first
    changed = config.model_copy(deep=True)
    changed.providers.openai.max_concurrent_requests = 3
    assert provider_signature(changed, model_id="main") != first


def test_disabled_fleet_keeps_plain_provider() -> None:
    config = _config()
    config.model_fleet.enabled = False
    provider = make_provider(config, model_id="main")
    assert not isinstance(provider, FleetControlledProvider)
