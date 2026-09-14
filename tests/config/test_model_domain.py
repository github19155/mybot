import pytest

from nanobot.model_domain import ModelConfig


def test_model_config_groups_capabilities_generation_and_pricing() -> None:
    model = ModelConfig.model_validate({
        "model": "openai/gpt-5.6",
        "provider": "openai",
        "capabilities": {
            "vision": True,
            "tools": True,
            "reasoning": True,
        },
        "contextWindowTokens": 400_000,
        "pricing": {
            "input": 1.25,
            "output": 10.0,
            "cacheRead": 0.125,
        },
        "generationDefaults": {
            "temperature": 0.2,
            "maxTokens": 16_384,
            "reasoningEffort": "high",
        },
        "pools": ["General", "dream", "general"],
    })

    assert model.capabilities.vision is True
    assert model.capabilities.tools is True
    assert model.capabilities.reasoning is True
    assert model.context_window_tokens == 400_000
    assert model.pricing.input == 1.25
    assert model.pricing.output == 10.0
    assert model.pricing.cache_read == 0.125
    assert model.generation_defaults.temperature == 0.2
    assert model.generation_defaults.max_tokens == 16_384
    assert model.generation_defaults.reasoning_effort == "high"
    assert model.pools == ["general", "dream"]


def test_model_config_accepts_staged_flat_shape_without_duplicate_state() -> None:
    model = ModelConfig.model_validate({
        "model": "anthropic/claude-sonnet",
        "provider": "anthropic",
        "supportsVision": True,
        "supportsImageGeneration": False,
        "maxTokens": 4096,
        "temperature": 0.3,
        "reasoningEffort": "medium",
        "fleetPools": ["General", "general"],
        "inputCostPerMillion": 3.0,
        "outputCostPerMillion": 15.0,
    })

    assert model.capabilities.vision is True
    assert model.generation_defaults.max_tokens == 4096
    assert model.generation_defaults.temperature == 0.3
    assert model.generation_defaults.reasoning_effort == "medium"
    assert model.pools == ["general"]
    assert model.pricing.input == 3.0
    assert model.pricing.output == 15.0

    dumped = model.model_dump(mode="json", by_alias=True)
    assert "supportsVision" not in dumped
    assert "maxTokens" not in dumped
    assert "fleetPools" not in dumped
    assert dumped["capabilities"]["vision"] is True
    assert dumped["generationDefaults"]["maxTokens"] == 4096
    assert dumped["pools"] == ["general"]


def test_generation_defaults_are_not_capabilities() -> None:
    model = ModelConfig(model="local/test", provider="custom")

    model.temperature = 0.8
    model.max_tokens = 2048

    assert model.generation_defaults.temperature == 0.8
    assert model.generation_defaults.max_tokens == 2048
    assert "temperature" not in model.capabilities.model_dump()
    assert "max_tokens" not in model.capabilities.model_dump()


def test_pool_membership_is_normalized_but_remains_model_offering_fact() -> None:
    model = ModelConfig(
        model="openrouter/model",
        provider="openrouter",
        pools=[" Dream ", "GENERAL", "dream", ""],
    )

    assert model.pools == ["dream", "general"]
    assert not hasattr(model, "allowed_models")


def test_model_identity_fields_cannot_be_blank() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        ModelConfig(model="   ", provider="openai")
    with pytest.raises(ValueError, match="must not be blank"):
        ModelConfig(model="openai/gpt", provider="   ")
