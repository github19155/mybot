"""Instance-scoped model administration and immutable task runtime resolution."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from typing import Any, cast

from nanobot.agent.subagent_roles import resolve_role
from nanobot.config.loader import resolve_config_env_vars
from nanobot.config.schema import Config, ModelPresetConfig, ProviderConfig
from nanobot.model_fleet import get_model_fleet, offering_from_config
from nanobot.providers.factory import build_provider_snapshot
from nanobot.utils.llm_runtime import LLMRuntime, runtime_from_provider_snapshot
from nanobot.webui import settings_models as models
from nanobot.webui.settings_contracts import WebUISettingsError
from nanobot.webui.settings_services import WebUISettingsConfig


class ModelManagement:
    """Model/provider administration plus Main-facing fleet discovery."""

    def __init__(self, config: Config, *, invalidate: Callable[[], None] | None = None) -> None:
        self.config = config
        self._store = WebUISettingsConfig(config.source_path) if config.source_path is not None else None
        self._invalidate = invalidate
        self._lock = threading.RLock()

    def _load(self) -> Config:
        return self._store.load() if self._store is not None else self.config

    def _runtime_from_selection(
        self,
        parent: LLMRuntime,
        config: Config,
        *,
        model: str | None,
        model_preset: str | None,
    ) -> LLMRuntime:
        if model is not None and model_preset is not None:
            raise ValueError("Choose either model or model_preset, not both")
        if model is None and model_preset is None:
            return parent
        if model is not None:
            if not model.strip():
                raise ValueError("model must be a non-empty string")
            preset = ModelPresetConfig(
                model=model.strip(), provider="auto",
                max_tokens=parent.generation.max_tokens,
                temperature=parent.generation.temperature,
                reasoning_effort=parent.generation.reasoning_effort,
                context_window_tokens=parent.context_window_tokens,
            )
            preset_name = None
        else:
            if model_preset != "default" and model_preset not in config.model_presets:
                raise ValueError("Unknown model preset")
            preset = config.resolve_preset(model_preset)
            preset_name = model_preset
        try:
            resolved = resolve_config_env_vars(config.model_copy(deep=True), config_path=config.source_path)
            snapshot = build_provider_snapshot(
                resolved,
                preset=preset,
                preset_name=preset_name,
            )
        except Exception:
            raise ValueError("Cannot resolve task model; check the configured provider and credentials") from None
        return runtime_from_provider_snapshot(snapshot)

    def resolve_task_runtime(
        self,
        parent: LLMRuntime,
        *,
        role: str,
        model: str | None = None,
        model_preset: str | None = None,
    ) -> LLMRuntime:
        with self._lock:
            config = self._load()
            role_config = resolve_role(config, role)
            if role_config.disabled:
                raise ValueError("Subagent role is disabled")
            if model is not None and model_preset is not None:
                raise ValueError("Choose either model or model_preset, not both")
            if model is not None:
                selected_model = model
                selected_preset = None
            elif model_preset is not None:
                selected_model = None
                selected_preset = model_preset
            elif role_config.model is not None:
                selected_model = role_config.model
                selected_preset = None
            else:
                selected_model = None
                selected_preset = role_config.model_preset
            return self._runtime_from_selection(
                parent, config, model=selected_model, model_preset=selected_preset,
            )

    def resolve_ephemeral_runtime(
        self,
        parent: LLMRuntime,
        *,
        model: str | None = None,
        model_preset: str | None = None,
    ) -> LLMRuntime:
        with self._lock:
            return self._runtime_from_selection(
                parent, self._load(), model=model, model_preset=model_preset,
            )

    @staticmethod
    def _catalog(config: Config) -> dict[str, Any]:
        payload = models.model_settings_payload(config, oauth_status=models.oauth_provider_status)
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
            "status": "ok",
            "model_presets": payload["model_presets"],
            "fleet_profiles": fleet_presets,
            "image_analysis": payload["image_analysis"],
            "subagent_roles": payload["subagent_roles"],
            "max_concurrent_subagents": payload["max_concurrent_subagents"],
            "providers": [
                {key: row[key] for key in ("name", "display_name", "configured") if key in row}
                for row in payload["providers"]
            ],
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
            fleet.bind_offering(offering_from_config(
                config,
                preset=preset,
                preset_name=name,
                provider_name=provider_name,
            ))
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
            if key.replace("-", "_").lower() == normalized.lower() and isinstance(value, ProviderConfig):
                return value
        return None

    @staticmethod
    def _update_fleet_profile(config: Config, params: dict[str, Any]) -> None:
        name = str(params.get("name") or "").strip()
        if not name or name == "default" or name not in config.model_presets:
            raise WebUISettingsError("Unknown named model preset")
        current = config.model_presets[name]
        fields = {
            "offering_id", "fleet_pools", "input_cost_per_million",
            "output_cost_per_million", "cached_input_cost_per_million",
            "max_concurrent_requests",
        }
        updates = {key: params[key] for key in fields if key in params}
        config.model_presets[name] = ModelPresetConfig.model_validate({
            **current.model_dump(),
            **updates,
        })

    @classmethod
    def _update_fleet_provider(cls, config: Config, params: dict[str, Any]) -> None:
        name = str(params.get("provider") or "").strip()
        current = cls._provider_config(config, name)
        if current is None:
            raise WebUISettingsError("Unknown provider")
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
                (key for key in (config.providers.model_extra or {}) if key.replace("-", "_").lower() == normalized.lower()),
                None,
            )
            if matched is None:
                raise WebUISettingsError("Unknown provider")
            config.providers.model_extra[matched] = validated

    def _execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if action == "list":
                return self._catalog(self._load())
            operations = {
                "roles_update": models.update_subagent_roles,
                "model_create": models.create_model_configuration,
                "model_update": models.update_model_configuration,
                "model_delete": models.delete_model_configuration,
                "provider_create": models.create_provider_settings,
                "provider_update": models.update_provider_settings,
            }
            operation = operations.get(action)
            if operation is None and action not in {"fleet_profile_update", "fleet_provider_update"}:
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
                elif action in {"model_create", "model_update"}:
                    selected = config.agents.defaults.model_preset
                    fallbacks = list(config.agents.defaults.fallback_models)
                    cast(Any, operation)(config, query, oauth_status=models.oauth_provider_status)
                    if action == "model_create":
                        config.agents.defaults.model_preset = selected
                        config.agents.defaults.fallback_models = fallbacks
                else:
                    cast(Any, operation)(config, query)
                return config

            updated = self._store.update(mutate) if self._store is not None else mutate(self.config.model_copy(deep=True))
            self.config.model_presets = updated.model_presets
            self.config.providers = updated.providers
            self.config.subagent_roles = updated.subagent_roles
            self.config.model_fleet = updated.model_fleet
            self.config.agents.defaults.model_preset = updated.agents.defaults.model_preset
            self.config.agents.defaults.fallback_models = updated.agents.defaults.fallback_models
            self.config.agents.defaults.dream.model_override = updated.agents.defaults.dream.model_override
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
        except WebUISettingsError as exc:
            message = exc.message if action in {
                "roles_update", "model_delete", "fleet_profile_update", "fleet_provider_update",
            } else "Invalid model/provider settings; check the action fields and provider configuration"
            return {"status": "error", "message": message}
        except Exception:
            return {"status": "error", "message": "Could not update model settings; check configuration and instance file access"}
        if action != "list" and result["status"] == "ok" and self._invalidate is not None:
            self._invalidate()
        return result
