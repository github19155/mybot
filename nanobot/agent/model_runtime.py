"""Public resolution boundary for default and overridden LLM runtimes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import cast

from nanobot.agent import model_presets as preset_helpers
from nanobot.config.schema import ModelPresetConfig
from nanobot.providers.factory import ProviderSnapshot
from nanobot.utils.llm_runtime import LLMRuntime, runtime_from_provider_snapshot

ProviderSnapshotLoader = Callable[..., ProviderSnapshot]


class ModelRuntimeResolver:
    """Own selection-to-runtime resolution for Main, Subagent, and Dream."""

    def __init__(
        self,
        initial_runtime: LLMRuntime,
        *,
        model_presets: Mapping[str, ModelPresetConfig] | None = None,
        preset_catalog_loader: preset_helpers.PresetCatalogLoader | None = None,
        provider_snapshot_loader: ProviderSnapshotLoader | None = None,
    ) -> None:
        self._runtime = initial_runtime
        self._model_presets = dict(model_presets or {})
        self._preset_catalog_loader = preset_catalog_loader
        self._preset_catalog_refresh_required = False
        self._provider_snapshot_loader = provider_snapshot_loader
        self._refresh_required = False
        self._resolved_presets: dict[tuple[str, bool], LLMRuntime] = {}
        self._default_selection_signature = preset_helpers.default_selection_signature(
            initial_runtime.snapshot_signature,
            initial_runtime.model_preset,
        )

    @property
    def runtime(self) -> LLMRuntime:
        """Return the current immutable default without refreshing configuration."""
        return self._runtime

    @property
    def model_presets(self) -> Mapping[str, ModelPresetConfig]:
        self._refresh_preset_catalog()
        return MappingProxyType({
            name: preset.model_copy(deep=True)
            for name, preset in self._model_presets.items()
        })

    @property
    def model_preset(self) -> str | None:
        return self._runtime.model_preset

    @property
    def provider_signature(self) -> tuple[object, ...] | None:
        return self._runtime.snapshot_signature

    def current(self, *, refresh: bool = False) -> LLMRuntime:
        """Return the selected runtime, optionally refreshing configured state."""
        if refresh:
            self.refresh()
        return self._runtime

    def admit(self) -> LLMRuntime:
        """Resolve the immutable runtime for the next turn admission."""
        if self._refresh_required:
            self.refresh()
        return self._runtime

    def invalidate(self) -> None:
        """Refresh configured runtime state lazily on the next resolution/admission."""
        self._refresh_required = True
        self._preset_catalog_refresh_required = True
        self._resolved_presets.clear()

    def _refresh_preset_catalog(self) -> None:
        if not self._preset_catalog_refresh_required:
            return
        if self._preset_catalog_loader is not None:
            self._model_presets = dict(self._preset_catalog_loader())
        self._preset_catalog_refresh_required = False

    def resolve_snapshot(self, snapshot: ProviderSnapshot) -> LLMRuntime:
        """Apply the one pure ProviderSnapshot -> LLMRuntime conversion."""
        return runtime_from_provider_snapshot(snapshot)

    def adopt_snapshot(self, snapshot: ProviderSnapshot) -> LLMRuntime:
        """Select a complete provider snapshot as the default for future turns."""
        runtime = self.resolve_snapshot(snapshot)
        self._runtime = runtime
        self._default_selection_signature = preset_helpers.default_selection_signature(
            runtime.snapshot_signature,
            runtime.model_preset,
        )
        return runtime

    def resolve_selection(
        self,
        parent_runtime: LLMRuntime,
        *,
        model: str | None = None,
        model_preset: str | None = None,
        include_fallbacks: bool = True,
    ) -> LLMRuntime:
        """Resolve one inherited, direct-model, or preset selection without mutation."""
        if model is not None and model_preset is not None:
            raise ValueError("model and model_preset are mutually exclusive")
        if model is None and model_preset is None:
            return parent_runtime

        if model is not None:
            if not isinstance(cast(object, model), str) or not model.strip():
                raise ValueError("model must be a non-empty string")
            if self._provider_snapshot_loader is None:
                raise RuntimeError("runtime selection requires a provider snapshot loader")
            preset = ModelPresetConfig(
                model=model.strip(),
                provider="auto",
                max_tokens=parent_runtime.generation.max_tokens,
                temperature=parent_runtime.generation.temperature,
                reasoning_effort=parent_runtime.generation.reasoning_effort,
                context_window_tokens=parent_runtime.context_window_tokens,
                supports_vision=False,
            )
            snapshot = self._provider_snapshot_loader(
                preset=preset,
                include_fallbacks=include_fallbacks,
            )
            return self.resolve_snapshot(snapshot)

        self._refresh_preset_catalog()
        normalized = preset_helpers.normalize_preset_name(model_preset, self._model_presets)
        if self._provider_snapshot_loader is None:
            raise RuntimeError("runtime selection requires a provider snapshot loader")
        cache_key = (normalized, include_fallbacks)
        cached = self._resolved_presets.get(cache_key)
        if cached is not None:
            return cached
        snapshot = self._provider_snapshot_loader(
            preset_name=normalized,
            include_fallbacks=include_fallbacks,
        )
        runtime = self.resolve_snapshot(snapshot)
        self._resolved_presets[cache_key] = runtime
        return runtime

    def resolve_preset(
        self,
        name: str | None,
        *,
        parent_runtime: LLMRuntime | None = None,
        include_fallbacks: bool = True,
    ) -> LLMRuntime:
        """Delegate named preset resolution to the canonical selection primitive."""
        return self.resolve_selection(
            parent_runtime or self._runtime,
            model_preset=name,
            include_fallbacks=include_fallbacks,
        )

    def select_preset(self, name: str | None) -> LLMRuntime:
        """Select a named preset as the default for future turns."""
        self._runtime = self.resolve_selection(self._runtime, model_preset=name)
        return self._runtime

    def select_model(self, model: str) -> LLMRuntime:
        """Select a direct model through canonical provider snapshot construction."""
        self._runtime = self.resolve_selection(self._runtime, model=model)
        return self._runtime

    def select_context_window(self, context_window_tokens: int) -> LLMRuntime:
        """Change the default context limit for future admissions."""
        raw_context_window = cast(object, context_window_tokens)
        if not isinstance(raw_context_window, int) or isinstance(raw_context_window, bool):
            raise TypeError("context_window_tokens must be an integer")
        self._runtime = replace(
            self._runtime,
            context_window_tokens=context_window_tokens,
        )
        return self._runtime

    def refresh(self) -> LLMRuntime | None:
        """Refresh configured defaults and return the replacement when changed."""
        if self._provider_snapshot_loader is None:
            self._refresh_required = False
            return None

        self._resolved_presets.clear()
        snapshot = self._provider_snapshot_loader(include_fallbacks=True)
        default_selection = preset_helpers.default_selection_signature(
            snapshot.signature,
            snapshot.model_preset,
        )
        active_preset = self._runtime.model_preset
        if active_preset and self._default_selection_signature in (None, default_selection):
            runtime = self.resolve_selection(
                self._runtime,
                model_preset=active_preset,
                include_fallbacks=True,
            )
        else:
            runtime = self.resolve_snapshot(snapshot)

        unchanged = (
            runtime.snapshot_signature == self._runtime.snapshot_signature
            and runtime.model_preset == self._runtime.model_preset
            and runtime.supports_vision == self._runtime.supports_vision
        )
        self._refresh_required = False
        self._default_selection_signature = default_selection
        if unchanged:
            return None
        self._runtime = runtime
        return runtime

    def resolve_override(
        self,
        *,
        model: str | None,
        model_preset: str | None,
        include_fallbacks: bool = True,
    ) -> LLMRuntime | None:
        """Resolve an SDK-style per-run override without mutating the default."""
        if model is None and model_preset is None:
            return None
        return self.resolve_selection(
            self._runtime,
            model=model,
            model_preset=model_preset,
            include_fallbacks=include_fallbacks,
        )
