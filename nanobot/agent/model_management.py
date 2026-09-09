"""Instance-scoped model administration and immutable task runtime resolution."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from typing import Any, cast

from nanobot.agent.subagent_roles import resolve_role
from nanobot.config.loader import resolve_config_env_vars
from nanobot.config.schema import Config, ModelPresetConfig
from nanobot.providers.factory import build_provider_snapshot
from nanobot.utils.llm_runtime import LLMRuntime, runtime_from_provider_snapshot
from nanobot.webui import settings_models as models
from nanobot.webui.settings_contracts import WebUISettingsError
from nanobot.webui.settings_services import WebUISettingsConfig


class ModelManagement:
    """Reuse the settings domain, retaining its validation and file locking."""

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
                parent,
                config,
                model=selected_model,
                model_preset=selected_preset,
            )

    def resolve_ephemeral_runtime(
        self,
        parent: LLMRuntime,
        *,
        model: str | None = None,
        model_preset: str | None = None,
    ) -> LLMRuntime:
        """Resolve explicit task runtime overrides without consulting a persistent role."""
        with self._lock:
            return self._runtime_from_selection(
                parent,
                self._load(),
                model=model,
                model_preset=model_preset,
            )

    @staticmethod
    def _catalog(config: Config) -> dict[str, Any]:
        payload = models.model_settings_payload(config, oauth_status=models.oauth_provider_status)
        # Provider connection details and custom headers are unnecessary for administration discovery.
        return {
            "status": "ok",
            "model_presets": payload["model_presets"],
            "image_analysis": payload["image_analysis"],
            "subagent_roles": payload["subagent_roles"],
            "max_concurrent_subagents": payload["max_concurrent_subagents"],
            "providers": [
                {key: row[key] for key in ("name", "display_name", "configured") if key in row}
                for row in payload["providers"]
            ],
        }

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
            if operation is None:
                return {"status": "error", "message": "Unknown model management action"}
            operation = cast(Any, operation)
            query = {
                key: [value if isinstance(value, str) else json.dumps(value)]
                for key, value in params.items()
            }

            def mutate(config: Config) -> Config:
                if action in {"model_create", "model_update"}:
                    selected = config.agents.defaults.model_preset
                    fallbacks = list(config.agents.defaults.fallback_models)
                    operation(config, query, oauth_status=models.oauth_provider_status)
                    if action == "model_create":
                        config.agents.defaults.model_preset = selected
                        config.agents.defaults.fallback_models = fallbacks
                else:
                    operation(config, query)
                return config

            # A failed domain mutation must not partly mutate an in-memory config.
            updated = self._store.update(mutate) if self._store is not None else mutate(self.config.model_copy(deep=True))
            self.config.model_presets = updated.model_presets
            self.config.providers = updated.providers
            self.config.subagent_roles = updated.subagent_roles
            self.config.agents.defaults.model_preset = updated.agents.defaults.model_preset
            self.config.agents.defaults.fallback_models = updated.agents.defaults.fallback_models
            self.config.agents.defaults.dream.model_override = updated.agents.defaults.dream.model_override
            return self._catalog(updated)

    async def execute(self, action: str, **params: Any) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(self._execute, action, params)
        except WebUISettingsError as exc:
            # Domain errors for bindings/deletion contain only controlled text and role names.
            message = exc.message if action in {"roles_update", "model_delete"} else "Invalid model/provider settings; check the action fields and provider configuration"
            return {"status": "error", "message": message}
        except Exception:
            return {"status": "error", "message": "Could not update model settings; check configuration and instance file access"}
        if action != "list" and result["status"] == "ok" and self._invalidate is not None:
            self._invalidate()
        return result
