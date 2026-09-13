"""Instance-scoped model/provider administration and Dream selection orchestration."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from nanobot.config.schema import Config, ModelPresetConfig, ProviderConfig
from nanobot.config.store import ConfigStore
from nanobot.model_fleet import get_model_fleet, offering_from_config
from nanobot.model_settings import (
    ModelSettingsError,
    create_model_configuration,
    create_provider_settings,
    delete_model_configuration,
    management_catalog,
    update_model_configuration,
    update_provider_settings,
    update_subagent_roles,
)

if TYPE_CHECKING:
    from nanobot.agent.model_runtime import ModelRuntimeResolver
    from nanobot.utils.llm_runtime import LLMRuntime

_DREAM_MIN_CONTEXT_TOKENS = 16_000


class ModelManagement:
    """Model/provider administration plus Main-facing fleet discovery."""

    def __init__(
        self,
        config: Config,
        *,
        runtime_resolver: "ModelRuntimeResolver | None" = None,
        invalidate: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        self._store = ConfigStore(config.source_path) if config.source_path is not None else None
        self._runtime_resolver = runtime_resolver
        self._invalidate = invalidate
        self._lock = threading.RLock()

    def _load(self) -> Config:
        return self._store.load() if self._store is not None else self.config

    def config_snapshot(self) -> Config:
        """Return the current validated configuration without exposing mutable storage."""
        with self._lock:
            return self._load().model_copy(deep=True)

    async def resolve_dream_runtime(self, workload: str) -> "LLMRuntime":
        """Choose a Dream route, then delegate selection-to-runtime resolution."""
        with self._lock:
            config = self._load()
            dream = config.agents.defaults.dream
            selected = dream.model_override
            pool = dream.pool_for(workload)

        if selected is None:
            recommendation = await self.fleet_recommend(
                pool=pool,
                task_type="background",
                min_context_tokens=_DREAM_MIN_CONTEXT_TOKENS,
            )
            if recommendation.get("status") == "ok":
                recommended = recommendation.get("recommended")
                if isinstance(recommended, dict):
                    value = cast(dict[str, object], recommended).get("preset")
                    if isinstance(value, str) and value:
                        selected = value

        if selected is None:
            with self._lock:
                selected = self._load().agents.defaults.dream.fallback_preset
        if selected is None:
            raise RuntimeError(
                "Dream has no eligible model route; configure a Dream pool, "
                "model_override, or fallback_preset"
            )
        if self._runtime_resolver is None:
            raise RuntimeError("Dream runtime selection requires ModelRuntimeResolver")
        return self._runtime_resolver.resolve_selection(
            self._runtime_resolver.runtime,
            model_preset=selected,
            include_fallbacks=False,
        )

    @staticmethod
    def _catalog(config: Config) -> dict[str, Any]:
        payload = management_catalog(config)
        fleet_presets = {
            name: {
                "offering_id": preset.offering_id,
                "pools": list(preset.fleet_pools),
                "input_cost_per_million": preset.input_cost_per_million,
                "output_cost_per_million": preset.output_cost_per_million,
                "cached_input_cost_per_million": preset.cached_input_cost_per_million,
                "max_concurrent_requests": preset.max_concurrent_requests,
            }
            for name, preset in config.model_presets.items()
        }
        return {
            **payload,
            "fleet_profiles": fleet_presets,
            "dream": config.agents.defaults.dream.model_dump(),
        }

    @staticmethod
    def _sync_fleet_catalog(config: Config):
        """Bind every configured selectable route without making network calls."""
        fleet = get_model_fleet(config)
        entries: list[tuple[str | None, ModelPresetConfig]] = [
            ("default", config.resolve_default_preset()),
            *config.model_presets.items(),
        ]
        for name, preset in entries:
            provider_name = config.get_provider_name(preset.model, preset=preset)
            if not provider_name:
                continue
            fleet.bind_offering(
                offering_from_config(
                    config,
                    preset=preset,
                    preset_name=name,
                    provider_name=provider_name,
                )
            )
        return fleet

    async def fleet_status(self) -> dict[str, object]:
        config = self._load()
        return await self._sync_fleet_catalog(config).status(refresh=True)

    async def fleet_recommend(
        self,
        *,
        pool: str | None = None,
        task_type: str = "general",
        requires_vision: bool = False,
        min_context_tokens: int | None = None,
    ) -> dict[str, object]:
        config = self._load()
        return await self._sync_fleet_catalog(config).recommend(
            pool=pool.strip().lower() if pool else None,
            task_type=task_type,
            requires_vision=requires_vision,
            min_context_tokens=min_context_tokens,
        )

    async def fleet_feedback(
        self,
        *,
        offering_id: str | None = None,
        model_preset: str | None = None,
        dimension: str = "general",
        outcome: float,
        weight: float = 1.0,
        evidence: str | None = None,
    ) -> dict[str, object]:
        """Record objective task-result evidence; never ask a model to self-grade."""
        config = self._load()
        fleet = self._sync_fleet_catalog(config)
        resolved_id = (offering_id or "").strip()
        if not resolved_id:
            if not model_preset:
                return {"status": "error", "message": "offering_id or model_preset is required"}
            if model_preset != "default" and model_preset not in config.model_presets:
                return {"status": "error", "message": "Unknown model preset"}
            preset = config.resolve_preset(model_preset)
            provider_name = config.get_provider_name(preset.model, preset=preset)
            if not provider_name:
                return {"status": "error", "message": "Preset provider cannot be resolved"}
            resolved_id = offering_from_config(
                config,
                preset=preset,
                preset_name=model_preset,
                provider_name=provider_name,
            ).offering_id
        try:
            fleet.record_quality(
                resolved_id,
                dimension=dimension.strip().lower() or "general",
                outcome=outcome,
                weight=weight,
                evidence=evidence,
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        refreshed = await fleet.refresh_scores()
        return {"status": "ok", "offering_id": resolved_id, "score": refreshed.get(resolved_id)}

    @staticmethod
    def _provider_config(config: Config, name: str) -> ProviderConfig | None:
        normalized = name.strip().replace("-", "_")
        built_in = getattr(config.providers, normalized, None)
        if isinstance(built_in, ProviderConfig):
            return built_in
        for key, value in (config.providers.model_extra or {}).items():
            if key.replace("-", "_").lower() == normalized.lower() and isinstance(
                value, ProviderConfig
            ):
                return value
        return None

    @staticmethod
    def _update_fleet_profile(config: Config, params: dict[str, Any]) -> None:
        name = str(params.get("name") or "").strip()
        if not name or name == "default" or name not in config.model_presets:
            raise ModelSettingsError("Unknown named model preset")
        current = config.model_presets[name]
        fields = {
            "offering_id",
            "fleet_pools",
            "input_cost_per_million",
            "output_cost_per_million",
            "cached_input_cost_per_million",
            "max_concurrent_requests",
        }
        updates = {key: params[key] for key in fields if key in params}
        config.model_presets[name] = ModelPresetConfig.model_validate(
            {**current.model_dump(), **updates}
        )

    @classmethod
    def _update_fleet_provider(cls, config: Config, params: dict[str, Any]) -> None:
        name = str(params.get("provider") or "").strip()
        current = cls._provider_config(config, name)
        if current is None:
            raise ModelSettingsError("Unknown provider")
        updates = {
            key: params[key]
            for key in ("max_concurrent_requests", "rate_limit_scope")
            if key in params
        }
        validated = type(current).model_validate({**current.model_dump(), **updates})
        normalized = name.replace("-", "_")
        if normalized in config.providers.__class__.model_fields:
            setattr(config.providers, normalized, validated)
        else:
            matched = next(
                (
                    key
                    for key in (config.providers.model_extra or {})
                    if key.replace("-", "_").lower() == normalized.lower()
                ),
                None,
            )
            if matched is None:
                raise ModelSettingsError("Unknown provider")
            config.providers.model_extra[matched] = validated

    @staticmethod
    def _update_dream(config: Config, params: dict[str, Any]) -> None:
        """Update only Main-adjustable Dream policy fields."""
        fields = {
            "enabled",
            "cooldown_minutes",
            "idle_minutes",
            "pressure_entries",
            "max_defer_minutes",
            "max_entries_per_run",
            "max_runs_per_day",
            "retention_days",
            "poll_interval_seconds",
            "model_override",
            "fallback_preset",
            "pools",
        }
        unknown = set(params) - fields
        if unknown:
            raise ModelSettingsError(f"Unknown Dream setting: {sorted(unknown)[0]}")
        payload = {**config.agents.defaults.dream.model_dump(), **params}
        config.agents.defaults.dream = type(config.agents.defaults.dream).model_validate(payload)

    def _execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if action == "list":
                return self._catalog(self._load())
            operations = {
                "roles_update": update_subagent_roles,
                "model_create": create_model_configuration,
                "model_update": update_model_configuration,
                "model_delete": delete_model_configuration,
                "provider_create": create_provider_settings,
                "provider_update": update_provider_settings,
            }
            operation = operations.get(action)
            internal_actions = {"fleet_profile_update", "fleet_provider_update", "dream_update"}
            if operation is None and action not in internal_actions:
                return {"status": "error", "message": "Unknown model management action"}
            query = {
                key: [value if isinstance(value, str) else json.dumps(value)]
                for key, value in params.items()
            }

            def mutate(config: Config) -> Config:
                if action == "fleet_profile_update":
                    self._update_fleet_profile(config, params)
                elif action == "fleet_provider_update":
                    self._update_fleet_provider(config, params)
                elif action == "dream_update":
                    self._update_dream(config, params)
                elif action in {"model_create", "model_update"}:
                    selected = config.agents.defaults.model_preset
                    fallbacks = list(config.agents.defaults.fallback_models)
                    cast(Any, operation)(config, query)
                    if action == "model_create":
                        config.agents.defaults.model_preset = selected
                        config.agents.defaults.fallback_models = fallbacks
                else:
                    cast(Any, operation)(config, query)
                return config

            updated = (
                self._store.update(mutate)
                if self._store is not None
                else mutate(self.config.model_copy(deep=True))
            )
            self.config.model_presets = updated.model_presets
            self.config.providers = updated.providers
            self.config.subagent_roles = updated.subagent_roles
            self.config.model_fleet = updated.model_fleet
            self.config.agents.defaults.model_preset = updated.agents.defaults.model_preset
            self.config.agents.defaults.fallback_models = updated.agents.defaults.fallback_models
            self.config.agents.defaults.dream = updated.agents.defaults.dream
            return self._catalog(updated)

    async def execute(self, action: str, **params: Any) -> dict[str, Any]:
        if action == "fleet_status":
            return await self.fleet_status()
        if action == "fleet_recommend":
            return await self.fleet_recommend(
                pool=params.get("pool"),
                task_type=str(params.get("task_type") or "general"),
                requires_vision=bool(params.get("requires_vision", False)),
                min_context_tokens=params.get("min_context_tokens"),
            )
        if action == "fleet_feedback":
            try:
                outcome = float(params.get("outcome"))
                weight = float(params.get("weight", 1.0))
            except (TypeError, ValueError):
                return {"status": "error", "message": "outcome and weight must be numeric"}
            return await self.fleet_feedback(
                offering_id=params.get("offering_id"),
                model_preset=params.get("model_preset"),
                dimension=str(params.get("dimension") or "general"),
                outcome=outcome,
                weight=weight,
                evidence=params.get("evidence"),
            )
        try:
            result = await asyncio.to_thread(self._execute, action, params)
        except ModelSettingsError as exc:
            message = exc.message if action in {
                "roles_update",
                "model_delete",
                "fleet_profile_update",
                "fleet_provider_update",
                "dream_update",
            } else "Invalid model/provider settings; check the action fields and provider configuration"
            return {"status": "error", "message": message}
        except Exception:
            return {
                "status": "error",
                "message": "Could not update model settings; check configuration and instance file access",
            }
        if action != "list" and result["status"] == "ok" and self._invalidate is not None:
            self._invalidate()
        return result
