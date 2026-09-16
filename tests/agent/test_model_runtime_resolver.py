from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest

from nanobot.agent.model_runtime import ModelRuntimeResolver
from nanobot.model_domain import ModelConfig
from nanobot.providers.base import GenerationSettings
from nanobot.providers.factory import ProviderSnapshot
from nanobot.utils.llm_runtime import LLMRuntime, runtime_from_provider_snapshot


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.generation = GenerationSettings(temperature=0.2, max_tokens=2048)
    return provider


def _model(name: str) -> ModelConfig:
    return ModelConfig(display_name=name, provider="cpa", model=name)


def _runtime(model_id: str = "main", model: str = "upstream/base") -> LLMRuntime:
    return LLMRuntime.capture(
        _provider(),
        model,
        model_id=model_id,
        context_window_tokens=10_000,
        snapshot_signature=(model_id, model),
    )


def _snapshot(model_id: str, model: str, *, version: str = "v1") -> ProviderSnapshot:
    return ProviderSnapshot(
        model_id=model_id,
        provider=_provider(),
        model=model,
        context_window_tokens=20_000,
        signature=(model_id, model, version),
        generation=GenerationSettings(temperature=0.4, max_tokens=4096),
        supports_vision=True,
        system_prompt_prefix=f"prompt:{model_id}",
    )


def test_runtime_captures_model_id_and_is_immutable() -> None:
    runtime = _runtime()
    assert runtime.model_id == "main"
    with pytest.raises(FrozenInstanceError):
        runtime.model_id = "other"  # type: ignore[misc]


def test_provider_snapshot_conversion_preserves_canonical_identity() -> None:
    snapshot = _snapshot("fast", "openai/gpt-5.6")
    runtime = runtime_from_provider_snapshot(snapshot)
    assert runtime.model_id == "fast"
    assert runtime.model == "openai/gpt-5.6"
    assert runtime.provider is snapshot.provider
    assert runtime.generation == snapshot.generation
    assert runtime.system_prompt_prefix == "prompt:fast"
    assert runtime.supports_vision is True


def test_model_id_resolution_is_cached_and_non_mutating() -> None:
    parent = _runtime()
    loader = MagicMock(return_value=_snapshot("fast", "openai/gpt-5.6"))
    resolver = ModelRuntimeResolver(
        parent,
        models={"main": _model("upstream/base"), "fast": _model("openai/gpt-5.6")},
        provider_snapshot_loader=loader,
    )

    first = resolver.resolve_model("fast")
    second = resolver.resolve_model("fast")

    assert first is second
    assert first.model_id == "fast"
    assert resolver.runtime is parent
    loader.assert_called_once_with(model_id="fast")


def test_unknown_model_id_fails_before_provider_loading() -> None:
    loader = MagicMock()
    resolver = ModelRuntimeResolver(
        _runtime(),
        models={"main": _model("upstream/base")},
        provider_snapshot_loader=loader,
    )
    with pytest.raises(KeyError, match="model_id 'missing' not found"):
        resolver.resolve_model("missing")
    loader.assert_not_called()


def test_select_model_accepts_only_canonical_model_id() -> None:
    resolver = ModelRuntimeResolver(
        _runtime(),
        models={
            "main": _model("upstream/base"),
            "worker": _model("openai/gpt-5.6"),
        },
        provider_snapshot_loader=lambda *, model_id=None: _snapshot(
            model_id or "main",
            "openai/gpt-5.6" if model_id == "worker" else "upstream/base",
        ),
    )
    selected = resolver.select_model("worker")
    assert selected.model_id == "worker"
    assert selected.model == "openai/gpt-5.6"
    with pytest.raises(ValueError, match="model_id must match"):
        resolver.select_model("openai/gpt-5.6")


def test_invalidate_refreshes_current_model_id_on_next_admission() -> None:
    current = _runtime("main", "v1")
    version = "v1"

    def load(*, model_id=None) -> ProviderSnapshot:
        return _snapshot(model_id or "main", version, version=version)

    resolver = ModelRuntimeResolver(
        current,
        models={"main": _model("v1")},
        provider_snapshot_loader=load,
    )
    version = "v2"
    resolver.invalidate()
    refreshed = resolver.admit()
    assert refreshed.model_id == "main"
    assert refreshed.model == "v2"
    assert refreshed.snapshot_signature[-1] == "v2"
