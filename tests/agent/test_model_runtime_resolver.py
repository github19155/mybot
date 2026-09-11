from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest

from nanobot.agent.model_runtime import ModelRuntimeResolver
from nanobot.config.schema import ModelPresetConfig
from nanobot.providers.base import GenerationSettings
from nanobot.providers.factory import ProviderSnapshot
from nanobot.utils.llm_runtime import LLMRuntime, runtime_from_provider_snapshot


def _provider(
    *,
    temperature: float = 0.1,
    max_tokens: int = 1024,
    reasoning_effort: str | None = None,
) -> MagicMock:
    provider = MagicMock()
    provider.generation = GenerationSettings(
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
    )
    return provider


def _runtime(provider: MagicMock | None = None) -> LLMRuntime:
    return LLMRuntime.capture(
        provider or _provider(),
        "base-model",
        context_window_tokens=10_000,
        snapshot_signature=("base-model", "auto", "v1"),
    )


def _snapshot(
    *,
    model: str,
    preset: str | None = None,
    signature: tuple[object, ...] | None = None,
    generation: GenerationSettings | None = None,
    supports_vision: bool = False,
    context_window_tokens: int = 20_000,
) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider=_provider(),
        model=model,
        context_window_tokens=context_window_tokens,
        signature=signature or (model, "auto", "v1"),
        generation=generation or GenerationSettings(0.4, 2048, "medium"),
        model_preset=preset,
        supports_vision=supports_vision,
        system_prompt_prefix=f"prompt:{model}",
    )


def test_runtime_captures_generation_and_is_immutable() -> None:
    provider = _provider(temperature=0.2, max_tokens=2048, reasoning_effort="low")
    runtime = _runtime(provider)
    provider.generation = GenerationSettings(temperature=0.9, max_tokens=99)

    assert runtime.generation == GenerationSettings(0.2, 2048, "low")
    with pytest.raises(FrozenInstanceError):
        runtime.model = "changed"  # type: ignore[misc]


def test_provider_snapshot_has_one_complete_runtime_conversion() -> None:
    snapshot = _snapshot(
        model="snapshot-model",
        preset="fast",
        signature=("snapshot-model", "openai", "credential"),
        generation=GenerationSettings(0.3, 4096, "high"),
        supports_vision=True,
        context_window_tokens=32_768,
    )

    runtime = runtime_from_provider_snapshot(snapshot)

    assert runtime.provider is snapshot.provider
    assert runtime.model == snapshot.model
    assert runtime.generation == snapshot.generation
    assert runtime.context_window_tokens == snapshot.context_window_tokens
    assert runtime.model_preset == snapshot.model_preset
    assert runtime.snapshot_signature == snapshot.signature
    assert runtime.supports_vision is True
    assert runtime.system_prompt_prefix == "prompt:snapshot-model"


def test_inherited_selection_returns_exact_parent_without_loading() -> None:
    parent = _runtime()
    loader = MagicMock()
    resolver = ModelRuntimeResolver(parent, provider_snapshot_loader=loader)

    resolved = resolver.resolve_selection(parent)

    assert resolved is parent
    assert resolver.runtime is parent
    loader.assert_not_called()


def test_named_preset_resolution_is_non_mutating_normalized_and_cached() -> None:
    parent = _runtime()
    snapshot = _snapshot(model="fast-model", preset="Fast")
    loader = MagicMock(return_value=snapshot)
    resolver = ModelRuntimeResolver(
        parent,
        model_presets={"Fast": ModelPresetConfig(model="fast-model")},
        provider_snapshot_loader=loader,
    )

    first = resolver.resolve_selection(parent, model_preset="fast")
    second = resolver.resolve_preset("Fast")

    assert first is second
    assert first.model_preset == "Fast"
    assert resolver.runtime is parent
    loader.assert_called_once_with(preset_name="Fast", include_fallbacks=True)


def test_direct_model_rebuilds_a_canonical_snapshot_from_parent_settings() -> None:
    parent = _runtime(_provider(temperature=0.25, max_tokens=3072, reasoning_effort="high"))
    canonical = _snapshot(
        model="provider/new-model",
        signature=("provider/new-model", "resolved-provider", "credential-v2"),
        generation=GenerationSettings(0.25, 3072, "high"),
        supports_vision=False,
        context_window_tokens=10_000,
    )
    loader = MagicMock(return_value=canonical)
    resolver = ModelRuntimeResolver(parent, provider_snapshot_loader=loader)

    resolved = resolver.resolve_selection(parent, model="provider/new-model")

    call = loader.call_args
    assert call is not None
    preset = call.kwargs["preset"]
    assert preset.model == "provider/new-model"
    assert preset.provider == "auto"
    assert preset.to_generation_settings() == parent.generation
    assert preset.context_window_tokens == parent.context_window_tokens
    assert preset.supports_vision is False
    assert call.kwargs["include_fallbacks"] is True
    assert resolved.provider is canonical.provider
    assert resolved.snapshot_signature == canonical.signature
    assert resolved.system_prompt_prefix == canonical.system_prompt_prefix
    assert resolver.runtime is parent


def test_selection_forwards_explicit_fallback_policy() -> None:
    parent = _runtime()
    loader = MagicMock(return_value=_snapshot(model="dream-model", preset="dream"))
    resolver = ModelRuntimeResolver(
        parent,
        model_presets={"dream": ModelPresetConfig(model="dream-model")},
        provider_snapshot_loader=loader,
    )

    resolver.resolve_selection(parent, model_preset="dream", include_fallbacks=False)

    loader.assert_called_once_with(preset_name="dream", include_fallbacks=False)


def test_selection_requires_one_canonical_snapshot_loader() -> None:
    resolver = ModelRuntimeResolver(
        _runtime(),
        model_presets={"fast": ModelPresetConfig(model="fast-model")},
    )

    with pytest.raises(RuntimeError, match="provider snapshot loader"):
        resolver.resolve_preset("fast")


def test_selection_validates_model_and_preset_inputs() -> None:
    parent = _runtime()
    resolver = ModelRuntimeResolver(parent, provider_snapshot_loader=MagicMock())

    with pytest.raises(ValueError, match="mutually exclusive"):
        resolver.resolve_selection(parent, model="m", model_preset="p")
    with pytest.raises(ValueError, match="non-empty"):
        resolver.resolve_selection(parent, model="  ")


def test_select_wrappers_mutate_only_the_resolver_default() -> None:
    parent = _runtime()

    def load(**kwargs) -> ProviderSnapshot:
        if "preset_name" in kwargs:
            return _snapshot(model="fast-model", preset=kwargs["preset_name"])
        preset = kwargs["preset"]
        return _snapshot(model=preset.model)

    resolver = ModelRuntimeResolver(
        parent,
        model_presets={"fast": ModelPresetConfig(model="fast-model")},
        provider_snapshot_loader=load,
    )

    preset_runtime = resolver.select_preset("fast")
    model_runtime = resolver.select_model("direct-model")
    window_runtime = resolver.select_context_window(65_536)

    assert preset_runtime.model_preset == "fast"
    assert model_runtime.model == "direct-model"
    assert model_runtime.model_preset is None
    assert window_runtime.context_window_tokens == 65_536
    assert resolver.runtime is window_runtime
    assert parent.model == "base-model"


def test_model_presets_are_deep_read_only_views() -> None:
    resolver = ModelRuntimeResolver(
        _runtime(),
        model_presets={"fast": ModelPresetConfig(model="fast-model")},
    )

    exposed = resolver.model_presets
    with pytest.raises(TypeError):
        exposed["other"] = ModelPresetConfig(model="other")  # type: ignore[index]
    exposed["fast"].model = "mutated"

    assert resolver.model_presets["fast"].model == "fast-model"


def test_invalidate_refreshes_only_on_next_admission() -> None:
    initial = _runtime()
    refreshed = _snapshot(model="refreshed-model")
    loader = MagicMock(return_value=refreshed)
    resolver = ModelRuntimeResolver(initial, provider_snapshot_loader=loader)

    assert resolver.admit() is initial
    loader.assert_not_called()

    resolver.invalidate()
    admitted = resolver.admit()

    assert admitted.model == "refreshed-model"
    loader.assert_called_once_with(include_fallbacks=True)
    assert resolver.admit() is admitted


def test_refresh_preserves_active_preset_when_configured_default_is_unchanged() -> None:
    default = _snapshot(
        model="base-model",
        preset="default",
        signature=("base-model", "auto", "v1"),
    )
    fast_v1 = _snapshot(
        model="fast-model",
        preset="fast",
        signature=("fast-model", "auto", "v1"),
    )
    fast_v2 = _snapshot(
        model="fast-model",
        preset="fast",
        signature=("fast-model", "auto", "v2"),
    )
    active_fast = fast_v1

    def load(**kwargs) -> ProviderSnapshot:
        nonlocal active_fast
        if kwargs.get("preset_name") == "fast":
            return active_fast
        return default

    resolver = ModelRuntimeResolver(
        runtime_from_provider_snapshot(default),
        model_presets={
            "default": ModelPresetConfig(model="base-model"),
            "fast": ModelPresetConfig(model="fast-model"),
        },
        provider_snapshot_loader=load,
    )
    resolver.select_preset("fast")
    active_fast = fast_v2
    resolver.invalidate()

    refreshed = resolver.admit()

    assert refreshed.model_preset == "fast"
    assert refreshed.snapshot_signature == fast_v2.signature


def test_resolve_override_delegates_without_mutating_default() -> None:
    parent = _runtime()
    loader = MagicMock(return_value=_snapshot(model="override-model"))
    resolver = ModelRuntimeResolver(parent, provider_snapshot_loader=loader)

    override = resolver.resolve_override(model="override-model", model_preset=None)

    assert override is not None
    assert override.model == "override-model"
    assert resolver.runtime is parent
    assert resolver.resolve_override(model=None, model_preset=None) is None
