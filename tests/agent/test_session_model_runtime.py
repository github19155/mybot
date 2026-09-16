from unittest.mock import MagicMock

import pytest

from nanobot.agent.model_runtime import ModelRuntimeResolver
from nanobot.model_domain import ModelConfig
from nanobot.providers.base import GenerationSettings
from nanobot.providers.factory import ProviderSnapshot
from nanobot.session.model_selection import SESSION_MODEL_ID_METADATA_KEY, model_id_from_metadata
from nanobot.utils.llm_runtime import LLMRuntime


def _model(model: str) -> ModelConfig:
    return ModelConfig(display_name=model, provider="cpa", model=model)


def _runtime() -> LLMRuntime:
    provider = MagicMock()
    provider.generation = GenerationSettings()
    return LLMRuntime.capture(
        provider,
        "upstream/base",
        model_id="main",
        context_window_tokens=8000,
    )


def _snapshot(model_id: str, model: str) -> ProviderSnapshot:
    provider = MagicMock()
    provider.generation = GenerationSettings()
    return ProviderSnapshot(
        model_id=model_id,
        provider=provider,
        model=model,
        context_window_tokens=16000,
        signature=(model_id, model),
    )


def test_session_metadata_round_trips_model_id() -> None:
    metadata = {SESSION_MODEL_ID_METADATA_KEY: "fast"}
    assert model_id_from_metadata(metadata) == "fast"


@pytest.mark.parametrize("value", [{"invalid": True}, 7, "  "])
def test_invalid_internal_model_id_metadata_fails_explicitly(value: object) -> None:
    with pytest.raises(ValueError, match="session model_id must be a non-empty string"):
        model_id_from_metadata({SESSION_MODEL_ID_METADATA_KEY: value})


def test_public_custom_metadata_does_not_select_a_model() -> None:
    assert model_id_from_metadata({"model_id": "fast"}) is None
    assert model_id_from_metadata({"model": "openai/gpt-5.6"}) is None
    assert model_id_from_metadata({"model_preset": "legacy"}) is None


def test_session_selection_accepts_registry_model_id_not_raw_upstream() -> None:
    resolver = ModelRuntimeResolver(
        _runtime(),
        models={
            "main": _model("upstream/base"),
            "fast": _model("openai/gpt-5.6"),
        },
        provider_snapshot_loader=lambda *, model_id=None: _snapshot(
            model_id or "main",
            "openai/gpt-5.6" if model_id == "fast" else "upstream/base",
        ),
    )
    selected = resolver.resolve_model(model_id_from_metadata({SESSION_MODEL_ID_METADATA_KEY: "fast"}) or "")
    assert selected.model_id == "fast"
    assert selected.model == "openai/gpt-5.6"

    with pytest.raises(ValueError, match="model_id must match"):
        resolver.resolve_model("openai/gpt-5.6")


def test_removed_session_model_id_can_be_detected_without_upstream_fallback() -> None:
    resolver = ModelRuntimeResolver(
        _runtime(),
        models={"main": _model("upstream/base")},
        provider_snapshot_loader=MagicMock(),
    )
    stored = model_id_from_metadata({SESSION_MODEL_ID_METADATA_KEY: "removed"})
    assert stored == "removed"
    with pytest.raises(KeyError, match="model_id 'removed' not found"):
        resolver.resolve_model(stored)
