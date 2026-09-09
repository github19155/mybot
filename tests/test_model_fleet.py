from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from nanobot.config.schema import Config, ModelPresetConfig, ProviderConfig
from nanobot.model_fleet import (
    ModelAdmissionController,
    ModelFleetManager,
    ModelFleetStore,
    ModelOffering,
    offering_from_config,
)
from nanobot.providers.base import LLMProvider, LLMResponse, LLMUsage
from nanobot.providers.fleet_controlled_provider import FleetControlledProvider


def _offering(
    provider: str = "openai",
    model: str = "gpt-x",
    *,
    provider_limit: int | None = None,
    model_limit: int | None = None,
    scope: str = "provider",
) -> ModelOffering:
    return ModelOffering(
        offering_id=f"{provider}:{model}",
        provider=provider,
        model=model,
        provider_max_concurrent_requests=provider_limit,
        max_concurrent_requests=model_limit,
        rate_limit_scope=scope,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_same_model_different_providers_have_independent_capacity() -> None:
    admission = ModelAdmissionController()
    openai = _offering("openai", provider_limit=1)
    router = _offering("openrouter", provider_limit=1)
    await admission.acquire(openai, priority="worker")
    router_acquired = asyncio.Event()

    async def acquire_router() -> None:
        await admission.acquire(router, priority="worker")
        router_acquired.set()

    task = asyncio.create_task(acquire_router())
    await asyncio.wait_for(router_acquired.wait(), timeout=0.5)
    await admission.release(router)
    await admission.release(openai)
    await task


@pytest.mark.asyncio
async def test_provider_limit_blocks_sibling_model_until_release() -> None:
    admission = ModelAdmissionController()
    one = _offering("openai", "model-a", provider_limit=1)
    two = _offering("openai", "model-b", provider_limit=1)
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
    one = _offering("openai", "model-a", scope="provider")
    two = _offering("openai", "model-b", scope="provider")
    await admission.note_rate_limit(one, retry_after_s=0.05)
    snapshot = await admission.snapshot(two)
    assert snapshot["cooldown_seconds"] > 0
    assert snapshot["recent_429"] == 1


@pytest.mark.asyncio
async def test_model_scoped_429_does_not_pause_sibling_model() -> None:
    admission = ModelAdmissionController()
    one = _offering("openai", "model-a", scope="model")
    two = _offering("openai", "model-b", scope="model")
    await admission.note_rate_limit(one, retry_after_s=1)
    assert (await admission.snapshot(one))["cooldown_seconds"] > 0
    assert (await admission.snapshot(two))["cooldown_seconds"] == 0


class _ProbeProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse], *, provider_name: str = "probe") -> None:
        super().__init__(provider_name=provider_name)
        self.responses = list(responses)
        self.calls = 0

    def get_default_model(self) -> str:
        return "probe-model"

    async def chat(self, **kwargs) -> LLMResponse:
        self.calls += 1
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
async def test_non_retryable_quota_429_does_not_create_cooldown(tmp_path: Path) -> None:
    fleet = ModelFleetManager(ModelFleetStore(tmp_path / "fleet.db"), config=Config())
    offering = _offering("probe", "probe-model")
    response = LLMResponse(
        content="insufficient quota",
        finish_reason="error",
        error_status_code=429,
        error_code="insufficient_quota",
        error_should_retry=False,
    )
    await fleet.note_response(offering, response)
    assert (await fleet.admission.snapshot(offering))["cooldown_seconds"] == 0


@pytest.mark.asyncio
async def test_passive_score_uses_real_calls_and_objective_quality(tmp_path: Path) -> None:
    store = ModelFleetStore(tmp_path / "fleet.db")
    fleet = ModelFleetManager(store, config=Config())
    offering = ModelOffering(
        offering_id="provider-x:model-a",
        provider="provider-x",
        model="model-a",
        preset_name="coding-x",
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
    scores = await fleet.refresh_scores()
    score = scores[offering.offering_id]
    assert score["quality"] == pytest.approx(10.0)
    assert score["reliability"] == pytest.approx(10.0)
    assert score["confidence"] > 0


@pytest.mark.asyncio
async def test_recommendation_filters_pool_and_capability(tmp_path: Path) -> None:
    fleet = ModelFleetManager(ModelFleetStore(tmp_path / "fleet.db"), config=Config())
    coding = ModelOffering(
        offering_id="p:coding", provider="p", model="coding", preset_name="coding-fast",
        pools=("coding",), supports_vision=False,
    )
    vision = ModelOffering(
        offering_id="q:vision", provider="q", model="vision", preset_name="vision-good",
        pools=("coding", "vision"), supports_vision=True,
    )
    fleet.bind_offering(coding)
    fleet.bind_offering(vision)
    result = await fleet.recommend(pool="coding", requires_vision=True)
    assert result["status"] == "ok"
    recommended = result["recommended"]
    assert isinstance(recommended, dict)
    assert recommended["preset"] == "vision-good"


def test_offering_identity_is_provider_plus_model_not_model_name() -> None:
    config = Config(
        providers={
            "openai": ProviderConfig(api_key="x"),
            "openrouter": ProviderConfig(api_key="y"),
        },
    )
    first = ModelPresetConfig(model="gpt-same", provider="openai")
    second = ModelPresetConfig(model="gpt-same", provider="openrouter")
    a = offering_from_config(config, preset=first, preset_name="a", provider_name="openai")
    b = offering_from_config(config, preset=second, preset_name="b", provider_name="openrouter")
    assert a.offering_id == "openai:gpt-same"
    assert b.offering_id == "openrouter:gpt-same"
    assert a.offering_id != b.offering_id


def test_fleet_profile_schema_accepts_cost_pool_and_limits() -> None:
    preset = ModelPresetConfig(
        model="gpt-x",
        provider="openai",
        fleet_pools=["Coding", "coding", "vision"],
        input_cost_per_million=1.25,
        output_cost_per_million=5,
        max_concurrent_requests=3,
    )
    provider = ProviderConfig(max_concurrent_requests=7, rate_limit_scope="model")
    assert preset.fleet_pools == ["coding", "vision"]
    assert preset.input_cost_per_million == 1.25
    assert provider.max_concurrent_requests == 7
    assert provider.rate_limit_scope == "model"
