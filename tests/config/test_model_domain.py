import pytest

from nanobot.model_domain import (
    MODEL_CAPABILITIES,
    ModelConfig,
    get_model,
    require_model_capability,
    validate_model_id,
)


def _model(**overrides):
    values = {
        "display_name": "Main",
        "provider": "openai",
        "model": "gpt-5.6",
    }
    values.update(overrides)
    return ModelConfig(**values)


def test_model_config_keeps_machine_display_and_upstream_identity_distinct() -> None:
    model = ModelConfig.model_validate({
        "displayName": "Primary GPT",
        "provider": "openai",
        "model": "gpt-5.6",
        "capabilities": {
            "text": True,
            "vision": True,
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

    assert model.display_name == "Primary GPT"
    assert model.provider == "openai"
    assert model.model == "gpt-5.6"
    assert model.capabilities.text is True
    assert model.capabilities.vision is True
    assert model.context_window_tokens == 400_000
    assert model.pricing.input == 1.25
    assert model.pricing.output == 10.0
    assert model.pricing.cache_read == 0.125
    assert model.generation_defaults.temperature == 0.2
    assert model.generation_defaults.max_tokens == 16_384
    assert model.generation_defaults.reasoning_effort == "high"
    assert model.pools == ["general", "dream"]


def test_capabilities_are_limited_to_current_consumers() -> None:
    assert MODEL_CAPABILITIES == (
        "text",
        "vision",
        "image_generation",
        "transcription",
    )
    fields = set(type(_model().capabilities).model_fields)
    assert fields == set(MODEL_CAPABILITIES)


def test_capabilities_are_explicit_and_default_false() -> None:
    model = _model()

    assert model.capabilities.text is False
    assert model.capabilities.vision is False
    assert model.capabilities.image_generation is False
    assert model.capabilities.transcription is False


def test_canonical_provider_must_be_concrete() -> None:
    with pytest.raises(ValueError, match="concrete provider ID"):
        _model(provider="auto")
    with pytest.raises(ValueError, match="provider must not be blank"):
        _model(provider="   ")


def test_display_name_and_upstream_model_cannot_be_blank() -> None:
    with pytest.raises(ValueError, match="display_name must not be blank"):
        _model(display_name="   ")
    with pytest.raises(ValueError, match="model must not be blank"):
        _model(model="   ")


@pytest.mark.parametrize(
    "model_id",
    ["main", "gpt-5-6", "coder_v2", "image", "m1"],
)
def test_model_id_accepts_stable_lowercase_slugs(model_id: str) -> None:
    assert validate_model_id(model_id) == model_id


@pytest.mark.parametrize(
    "model_id",
    ["", "Main", " main", "main ", "1main", "main.model", "main/model", "a" * 65],
)
def test_model_id_rejects_noncanonical_values(model_id: str) -> None:
    with pytest.raises(ValueError, match="model_id must match"):
        validate_model_id(model_id)


def test_get_model_is_the_shared_model_id_lookup_contract() -> None:
    models = {"main": _model(display_name="Primary")}

    resolved = get_model(models, "main")

    assert resolved is models["main"]
    assert resolved.display_name == "Primary"
    with pytest.raises(KeyError, match="model_id 'missing' not found"):
        get_model(models, "missing")


def test_require_model_capability_returns_model_when_supported() -> None:
    models = {
        "vision": _model(
            display_name="Vision",
            capabilities={"text": True, "vision": True},
        )
    }

    resolved = require_model_capability(models, "vision", "vision")

    assert resolved is models["vision"]


def test_require_model_capability_rejects_missing_capability() -> None:
    models = {"main": _model(capabilities={"text": True})}

    with pytest.raises(ValueError, match="does not support vision"):
        require_model_capability(models, "main", "vision")


def test_pool_membership_is_normalized_but_remains_model_offering_fact() -> None:
    model = _model(pools=[" Dream ", "GENERAL", "dream", ""])

    assert model.pools == ["dream", "general"]
    assert not hasattr(model, "allowed_models")


def test_pricing_contains_only_current_fleet_inputs() -> None:
    model = _model()

    assert set(type(model.pricing).model_fields) == {"input", "output", "cache_read"}
