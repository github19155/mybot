"""Public resolution boundary for canonical LLM runtimes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import cast

from nanobot.model_domain import ModelConfig, get_model
from nanobot.providers.factory import ProviderSnapshot
from nanobot.utils.llm_runtime import LLMRuntime, runtime_from_provider_snapshot

ProviderSnapshotLoader = Callable[..., ProviderSnapshot]
ModelCatalogLoader = Callable[[], Mapping[str, ModelConfig]]


class ModelRuntimeResolver:
    """Own canonical model_id -> runtime resolution for ordinary LLM runtimes."""

    def __init__(
        self,
        initial_runtime: LLMRuntime,
        *,
        models: Mapping[str, ModelConfig] | None = None,
        model_catalog_loader: ModelCatalogLoader | None = None,
        provider_snapshot_loader: ProviderSnapshotLoader | None = None,
    ) -> None:
        self._runtime = initial_runtime
        self._models = dict(models or {})
        self._model_catalog_loader = model_catalog_loader
        self._catalog_refresh_required = False
        self._provider_snapshot_loader = provider_snapshot_loader
        self._refresh_required = False
        self._resolved_models: dict[str, LLMRuntime] = {}

    @property
    def runtime(self) -> LLMRuntime:
        return self._runtime

    @property
    def models(self) -> Mapping[str, ModelConfig]:
        self._refresh_model_catalog()
        return MappingProxyType({
            model_id: model.model_copy(deep=True)
            for model_id, model in self._models.items()
        })

    @property
    def model_id(self) -> str | None:
        return self._runtime.model_id

    @property
    def provider_signature(self) -> tuple[object, ...] | None:
        return self._runtime.snapshot_signature

    def current(self, *, refresh: bool = False) -> LLMRuntime:
        if refresh:
            self.refresh()
        return self._runtime

    def admit(self) -> LLMRuntime:
        if self._refresh_required:
            self.refresh()
        return self._runtime

    def invalidate(self) -> None:
        self._refresh_required = True
        self._catalog_refresh_required = True
        self._resolved_models.clear()

    def _refresh_model_catalog(self) -> None:
        if not self._catalog_refresh_required:
            return
        if self._model_catalog_loader is not None:
            self._models = dict(self._model_catalog_loader())
        self._catalog_refresh_required = False

    def resolve_snapshot(self, snapshot: ProviderSnapshot) -> LLMRuntime:
        return runtime_from_provider_snapshot(snapshot)

    def adopt_snapshot(self, snapshot: ProviderSnapshot) -> LLMRuntime:
        runtime = self.resolve_snapshot(snapshot)
        self._runtime = runtime
        return runtime

    def resolve_model(
        self,
        model_id: str,
        *,
        parent_runtime: LLMRuntime | None = None,
    ) -> LLMRuntime:
        """Resolve a configured canonical model ID without mutating selection."""
        del parent_runtime
        if not isinstance(cast(object, model_id), str) or not model_id.strip():
            raise ValueError("model_id must be a non-empty string")
        normalized = model_id.strip()
        self._refresh_model_catalog()
        get_model(self._models, normalized)
        if self._provider_snapshot_loader is None:
            raise RuntimeError("runtime selection requires a provider snapshot loader")
        cached = self._resolved_models.get(normalized)
        if cached is not None:
            return cached
        runtime = self.resolve_snapshot(
            self._provider_snapshot_loader(model_id=normalized)
        )
        self._resolved_models[normalized] = runtime
        return runtime

    def resolve_selection(
        self,
        parent_runtime: LLMRuntime,
        *,
        model_id: str | None = None,
    ) -> LLMRuntime:
        if model_id is None:
            return parent_runtime
        return self.resolve_model(model_id, parent_runtime=parent_runtime)

    def select_model(self, model_id: str) -> LLMRuntime:
        """Select a canonical model ID as the default for future turns."""
        self._runtime = self.resolve_model(model_id, parent_runtime=self._runtime)
        return self._runtime

    def select_context_window(self, context_window_tokens: int) -> LLMRuntime:
        raw_context_window = cast(object, context_window_tokens)
        if not isinstance(raw_context_window, int) or isinstance(raw_context_window, bool):
            raise TypeError("context_window_tokens must be an integer")
        self._runtime = replace(
            self._runtime,
            context_window_tokens=context_window_tokens,
        )
        return self._runtime

    def refresh(self) -> LLMRuntime | None:
        """Refresh the selected canonical model from current config."""
        if self._provider_snapshot_loader is None:
            self._refresh_required = False
            return None
        self._resolved_models.clear()
        selected_id = self._runtime.model_id
        snapshot = self._provider_snapshot_loader(model_id=selected_id)
        runtime = self.resolve_snapshot(snapshot)
        unchanged = (
            runtime.snapshot_signature == self._runtime.snapshot_signature
            and runtime.model_id == self._runtime.model_id
            and runtime.supports_vision == self._runtime.supports_vision
        )
        self._refresh_required = False
        if unchanged:
            return None
        self._runtime = runtime
        return runtime

    def resolve_override(self, *, model_id: str | None) -> LLMRuntime | None:
        """Resolve an SDK-style per-run canonical model override."""
        if model_id is None:
            return None
        return self.resolve_model(model_id, parent_runtime=self._runtime)
