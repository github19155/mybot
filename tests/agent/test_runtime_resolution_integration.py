from unittest.mock import MagicMock

import pytest

from nanobot.config.schema import Config
from nanobot.model_domain import ModelCapabilities, ModelConfig, ModelGenerationDefaults
from nanobot.providers.factory import build_provider_snapshot, provider_signature


def _config() -> Config:
    return Config.model_validate({
        "agents": {"defaults": {"model_id": "main"}},
        "providers": {
            "cpa": {"api_key": "sk-cpa", "api_base": "https://cpa.invalid/v1"},
            "openai": {"api_key": "sk-openai"},
        },
        "models": {
            "main": {
                "display_name": "CPA Main",
                "provider": "cpa",
                "model": "openai/gpt-5.6",
                "capabilities": {"text": True, "vision": True},
                "context_window_tokens": 123456,
                "generation_defaults": {
                    "temperature": 0.3,
                    "max_tokens": 4096,
                    "reasoning_effort": "high"
                },
                "pools": ["main"]
            },
            "openai_route": {
                "display_name": "OpenAI Route",
                "provider": "openai",
                "model": "openai/gpt-5.6",
                "capabilities": {"text": True}
            }
        },
        "system_prompt_overrides": [
            {"prompt": "CPA only prompt", "model_ids": ["main"]}
        ],
        "model_fleet": {"enabled": False}
    })


def test_default_runtime_comes_from_agents_defaults_model_id() -> None:
    snapshot = build_provider_snapshot(_config())
    assert snapshot.model_id == "main"
    assert snapshot.model == "openai/gpt-5.6"
    assert snapshot.context_window_tokens == 123456
    assert snapshot.generation is not None
    assert snapshot.generation.temperature == 0.3
    assert snapshot.generation.max_tokens == 4096
    assert snapshot.generation.reasoning_effort == "high"
    assert snapshot.supports_vision is True


def test_cpa_openai_prefixed_upstream_does_not_select_openai_provider() -> None:
    snapshot = build_provider_snapshot(_config(), model_id="main")
    assert snapshot.provider.provider_name == "cpa"
    assert snapshot.model == "openai/gpt-5.6"


def test_same_upstream_model_different_provider_routes_have_distinct_signatures() -> None:
    config = _config()
    cpa = build_provider_snapshot(config, model_id="main")
    openai = build_provider_snapshot(config, model_id="openai_route")
    assert cpa.model == openai.model == "openai/gpt-5.6"
    assert cpa.provider.provider_name == "cpa"
    assert openai.provider.provider_name == "openai"
    assert provider_signature(config, model_id="main") != provider_signature(
        config, model_id="openai_route"
    )


def test_system_prompt_matches_canonical_model_id_not_upstream_model() -> None:
    config = _config()
    cpa = build_provider_snapshot(config, model_id="main")
    openai = build_provider_snapshot(config, model_id="openai_route")
    assert cpa.system_prompt_prefix == "CPA only prompt"
    assert openai.system_prompt_prefix is None


def test_unknown_model_id_fails_explicitly() -> None:
    with pytest.raises(KeyError, match="model_id 'missing' not found"):
        build_provider_snapshot(_config(), model_id="missing")


def test_generation_settings_are_read_from_nested_model_defaults() -> None:
    config = _config()
    config.models["main"] = ModelConfig(
        display_name="changed",
        provider="cpa",
        model="openai/gpt-5.6",
        capabilities=ModelCapabilities(text=True),
        generation_defaults=ModelGenerationDefaults(
            temperature=0.7,
            max_tokens=777,
            reasoning_effort="medium",
        ),
    )
    snapshot = build_provider_snapshot(config, model_id="main")
    assert snapshot.generation is not None
    assert snapshot.generation.temperature == 0.7
    assert snapshot.generation.max_tokens == 777
    assert snapshot.generation.reasoning_effort == "medium"


def test_provider_factory_never_asks_config_to_guess_from_upstream(monkeypatch) -> None:
    config = _config()
    # Canonical factory must not consult old Config provider-guessing helpers.
    for name in ("get_provider_name", "get_provider", "resolve_preset", "resolve_default_preset"):
        if hasattr(config, name):
            monkeypatch.setattr(config, name, MagicMock(side_effect=AssertionError(name)))
    snapshot = build_provider_snapshot(config, model_id="main")
    assert snapshot.provider.provider_name == "cpa"
