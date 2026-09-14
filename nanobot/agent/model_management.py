"""Instance-scoped canonical model/provider administration."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from typing import Any, cast

from nanobot.config.schema import Config, ProviderConfig
from nanobot.config.store import ConfigStore
from nanobot.model_fleet import get_model_fleet, offering_from_config
from nanobot.model_settings import (
    ModelInUseError,
    ModelSettingsError,
    create_model,
    create_provider_settings,
    delete_model,
    management_catalog,
    model_catalog_entry,
    update_model,
    update_provider_settings,
    update_subagent_roles,
)


class ModelManagement:
    """Canonical model/provider control plane shared by Main and WebUI."""

    def __init__(
        self,
        config: Config,
        *,
        runtime_resolver: Any | None = None,
        invalidate: Callable[[], None] | None = None,
    ) -> None:
        # Compatibility glue while B/E callers migrate. Model Management does
        # not execute runtimes and deliberately does not use this resolver.
        del runtime_resolver
        self.config = config
        self._store = ConfigStore(config.source_path) if config.source_path is not None else None
        self._invalidate = invalidate
        self._lock = threading.RLock()

    def _load(self) -> Config:
        return self._store.load() if self._store is not None else self.config

    def config_snapshot(self) -> Config:
        """Return current validated configuration without exposing mutable storage."""
        with self._lock:
            return self._load().model_copy(deep=True)

    @staticmethod
    def _catalog(config: Config) -> dict[str, Any]:
        return management_catalog(config)

    @staticmethod
    def _canonical_offering(config: Config, model_id: str):
        """Delegate canonical ModelConfig -> Fleet offering construction to B."""
        try:
            return offering_from_config(config, model_id=model_id)  # type: ignore[call-arg]
        except TypeError as exc:
            raise ModelSettingsError(
                "canonical Model Fleet adapter is unavailable; integrate refactor/model-runtime-fleet-v2",
                status=503,
            ) from exc

    @classmethod
    def _sync_fleet_catalog(cls, config: Config):
        fleet = get_model_fleet(config)
        for model_id in config.models:
            fleet.bind_offering(cls._canonical_offering(config, model_id))
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
        model_id: str | None = None,
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
            selected = (model_id or "").strip()
            if not selected:
                return {"status": "error", "message": "offering_id or model_id is required"}
            model = config.models.get(selected)
            if model is None:
                return {"status": "error", "message": "unknown model"}
            resolved_id = model.offering_id or self._canonical_offering(config, selected).offering_id
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

    @classmethod
    def _update_fleet_provider(cls, config: Config, params: dict[str, Any]) -> None:
        name = str(params.get("provider") or "").strip()
        current = cls._provider_config(config, name)
        if current is None:
            raise ModelSettingsError("unknown provider")
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
                raise ModelSettingsError("unknown provider")
            config.providers.model_extra[matched] = validated

    @staticmethod
    def _model_values(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        model_id = str(params.get("model_id") or "").strip()
        values = {key: value for key, value in params.items() if key != "model_id"}
        return model_id, values

    @staticmethod
    def _fleet_model_values(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        model_id = str(params.get("model_id") or "").strip()
        allowed = {"offering_id", "pools", "pricing", "max_concurrent_requests"}
        unknown = set(params) - allowed - {"model_id"}
        if unknown:
            raise ModelSettingsError(f"unknown Fleet model field: {sorted(unknown)[0]}")
        values = {key: params[key] for key in allowed if key in params}
        return model_id, values

    @staticmethod
    def _query(params: dict[str, Any]) -> dict[str, list[str]]:
        return {
            key: [value if isinstance(value, str) else json.dumps(value)]
            for key, value in params.items()
        }

    def _commit_mutation(self, mutation: Callable[[Config], dict[str, Any]]) -> dict[str, Any]:
        result_holder: dict[str, Any] = {}

        def apply(config: Config) -> Config:
            result_holder.update(mutation(config))
            return config

        updated = (
            self._store.update(apply)
            if self._store is not None
            else apply(self.config.model_copy(deep=True))
        )
        self.config.models = updated.models
        self.config.providers = updated.providers
        self.config.subagent_roles = updated.subagent_roles
        self.config.model_fleet = updated.model_fleet
        return result_holder

    def _execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            config = self._load()
            if action == "list":
                return self._catalog(config)
            if action == "model_get":
                model_id = str(params.get("model_id") or "").strip()
                return {"status": "ok", "model": model_catalog_entry(config, model_id)}

            def mutate(target: Config) -> dict[str, Any]:
                if action == "model_create":
                    model_id, values = self._model_values(params)
                    create_model(target, model_id=model_id, values=values)
                    return {"status": "ok", "model": model_catalog_entry(target, model_id)}
                if action == "model_update":
                    model_id, changes = self._model_values(params)
                    update_model(target, model_id=model_id, changes=changes)
                    return {"status": "ok", "model": model_catalog_entry(target, model_id)}
                if action == "model_delete":
                    model_id = str(params.get("model_id") or "").strip()
                    delete_model(target, model_id=model_id)
                    return {"status": "ok", "model_id": model_id}
                if action == "provider_create":
                    provider = create_provider_settings(target, self._query(params))
                    return {"status": "ok", "provider": provider}
                if action == "provider_update":
                    changed, restart_required = update_provider_settings(target, self._query(params))
                    return {
                        "status": "ok",
                        "provider": str(params.get("provider") or ""),
                        "changed": changed,
                        "restart_required": restart_required,
                    }
                if action == "roles_update":
                    update_subagent_roles(target, self._query(params))
                    return {"status": "ok", "subagent_roles": self._catalog(target)["subagent_roles"]}
                if action == "fleet_model_update":
                    model_id, changes = self._fleet_model_values(params)
                    update_model(target, model_id=model_id, changes=changes)
                    return {"status": "ok", "model": model_catalog_entry(target, model_id)}
                if action == "fleet_provider_update":
                    self._update_fleet_provider(target, params)
                    return {"status": "ok", "provider": str(params.get("provider") or "")}
                raise ModelSettingsError("unknown model management action")

            return self._commit_mutation(mutate)

    async def execute(self, action: str, **params: Any) -> dict[str, Any]:
        if action == "fleet_status":
            try:
                return cast(dict[str, Any], await self.fleet_status())
            except ModelSettingsError as exc:
                return {"status": "error", "message": exc.message}
        if action == "fleet_recommend":
            try:
                return cast(dict[str, Any], await self.fleet_recommend(
                    pool=params.get("pool"),
                    task_type=str(params.get("task_type") or "general"),
                    requires_vision=bool(params.get("requires_vision", False)),
                    min_context_tokens=params.get("min_context_tokens"),
                ))
            except ModelSettingsError as exc:
                return {"status": "error", "message": exc.message}
        if action == "fleet_feedback":
            try:
                outcome = float(params.get("outcome"))
                weight = float(params.get("weight", 1.0))
            except (TypeError, ValueError):
                return {"status": "error", "message": "outcome and weight must be numeric"}
            try:
                return cast(dict[str, Any], await self.fleet_feedback(
                    offering_id=params.get("offering_id"),
                    model_id=params.get("model_id"),
                    dimension=str(params.get("dimension") or "general"),
                    outcome=outcome,
                    weight=weight,
                    evidence=params.get("evidence"),
                ))
            except ModelSettingsError as exc:
                return {"status": "error", "message": exc.message}
        try:
            result = await asyncio.to_thread(self._execute, action, params)
        except ModelInUseError as exc:
            return {"status": "error", "message": exc.message, "usages": list(exc.usages)}
        except ModelSettingsError as exc:
            return {"status": "error", "message": exc.message}
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception:
            return {
                "status": "error",
                "message": "could not update model settings; check configuration and instance file access",
            }
        if action not in {"list", "model_get"} and result.get("status") == "ok" and self._invalidate is not None:
            self._invalidate()
        return result
