"""Model and provider settings domain logic.

The WebUI model surface is a view over the canonical ``Config.models`` registry.
Stable ``model_id`` keys are consumer identity; provider and upstream model values
live only inside ``ModelConfig``. Provider settings remain an independent domain.
"""

# oauth-cli-kit does not publish type stubs.
# pyright: reportMissingTypeStubs=false

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypedDict, cast

import httpx

from nanobot import model_settings as core_models
from nanobot.agent.subagent_roles import SUBAGENT_ROLES
from nanobot.config.loader import resolve_config_env_vars
from nanobot.config.schema import Config, ProviderConfig, SystemPromptOverrideConfig
from nanobot.providers.image_generation import get_image_gen_provider
from nanobot.providers.oauth_guidance import OAUTH_CLI_KIT_MISSING_MESSAGE
from nanobot.providers.oauth_model_catalog import (
    get_oauth_model_catalog,
    invalidate_oauth_model_catalog,
)
from nanobot.providers.registry import PROVIDERS, create_dynamic_spec, find_by_name
from nanobot.webui.settings_contracts import (
    QueryParams,
    SettingsRequest,
    SettingsRouteResult,
    WebUISettingsError,
    query_first,
    query_first_alias,
    query_has_alias,
)

if TYPE_CHECKING:
    from nanobot.webui.settings_services import (
        WebUIOAuthFlowRegistry,
        WebUISettingsServices,
    )

OAuthStatusReader = Callable[[Any], dict[str, Any]]
SettingsPayloadBuilder = Callable[..., dict[str, Any]]
HttpGet = Callable[..., httpx.Response]
SettingsOperation = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ModelSettingsOperations:
    update_agent: SettingsOperation
    create_model: SettingsOperation
    update_model: SettingsOperation
    delete_model: SettingsOperation
    update_prompt_overrides: SettingsOperation
    update_provider: SettingsOperation
    create_provider: SettingsOperation
    provider_models: SettingsOperation
    oauth_login: SettingsOperation
    oauth_complete: SettingsOperation
    oauth_logout: SettingsOperation
    update_subagent_roles: SettingsOperation
    apply_image_runtime_change: Callable[
        [dict[str, Any]],
        Awaitable[tuple[dict[str, Any], bool]],
    ]


class ModelSettingsPayload(TypedDict):
    agent: dict[str, Any]
    models: list[dict[str, Any]]
    image_analysis: dict[str, Any]
    system_prompt_overrides: list[dict[str, Any]]
    providers: list[dict[str, Any]]
    subagent_roles: list[dict[str, Any]]
    max_concurrent_subagents: int


_OAUTH_PROXY_PROVIDERS = {"openai_codex", "xai_grok"}
_WEBUI_OAUTH_TIMEOUT_S = 600
_MODEL_CONFIGURATION_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_REDACTED_PROVIDER_SECRET = "••••••••"
_PROVIDER_STRUCTURED_FIELDS = ("extra_headers", "extra_body", "extra_query")
_PROVIDER_SECRET_KEYS = frozenset(
    {
        "auth",
        "authentication",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "hmac",
        "key",
        "passphrase",
        "passwd",
        "proxyauthorization",
        "setcookie",
        "sig",
        "signature",
    }
)
_PROVIDER_SECRET_KEY_SUFFIXES = (
    "accesskey",
    "apikey",
    "encryptionkey",
    "password",
    "privatekey",
    "secret",
    "secretkey",
    "signingkey",
    "subscriptionkey",
    "token",
)


def _core_model_call(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return operation(*args, **kwargs)
    except core_models.ModelSettingsError as exc:
        raise WebUISettingsError(exc.message, status=exc.status) from exc


def _provider_json_setting(
    query: QueryParams,
    snake: str,
    camel: str,
) -> dict[str, Any] | None:
    raw = (query_first_alias(query, snake, camel) or "").strip()
    if not raw:
        return None
    try:
        value: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WebUISettingsError(f"{snake} must be a JSON object") from exc
    if not isinstance(value, dict):
        raise WebUISettingsError(f"{snake} must be a JSON object")
    return cast(dict[str, Any], value) or None


def _provider_setting_key_is_secret(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return compact in _PROVIDER_SECRET_KEYS or compact.endswith(
        _PROVIDER_SECRET_KEY_SUFFIXES
    )


def _redact_provider_secret_values(value: Any, *, secret: bool = False) -> Any:
    if secret and value not in (None, ""):
        return _REDACTED_PROVIDER_SECRET
    if isinstance(value, dict):
        value_mapping = cast(dict[str, Any], value)
        return {
            key: _redact_provider_secret_values(
                item,
                secret=_provider_setting_key_is_secret(key),
            )
            for key, item in value_mapping.items()
        }
    if isinstance(value, list):
        return [_redact_provider_secret_values(item) for item in cast(list[Any], value)]
    return value


def _restore_redacted_provider_secret_values(
    submitted: Any,
    current: Any,
    *,
    secret: bool = False,
) -> Any:
    if secret and submitted == _REDACTED_PROVIDER_SECRET:
        return current
    if isinstance(submitted, dict):
        submitted_mapping = cast(dict[str, Any], submitted)
        current_mapping = cast(dict[str, Any], current) if isinstance(current, dict) else {}
        return {
            key: _restore_redacted_provider_secret_values(
                item,
                current_mapping.get(key),
                secret=_provider_setting_key_is_secret(key),
            )
            for key, item in submitted_mapping.items()
        }
    if isinstance(submitted, list):
        submitted_items = cast(list[Any], submitted)
        current_items = cast(list[Any], current) if isinstance(current, list) else []
        return [
            _restore_redacted_provider_secret_values(
                item,
                current_items[index] if index < len(current_items) else None,
            )
            for index, item in enumerate(submitted_items)
        ]
    return submitted


def _provider_config_updates(query: QueryParams) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    string_fields = (
        ("api_key", "apiKey"),
        ("api_base", "apiBase"),
        ("api_type", "apiType"),
        ("proxy", "proxy"),
        ("thinking_style", "thinkingStyle"),
        ("region", "region"),
        ("profile", "profile"),
        ("display_name", "displayName"),
        ("rate_limit_scope", "rateLimitScope"),
    )
    for snake, camel in string_fields:
        if query_has_alias(query, snake, camel):
            value = (query_first_alias(query, snake, camel) or "").strip()
            updates[snake] = value or ("auto" if snake == "api_type" else None)
    if query_has_alias(query, "max_concurrent_requests", "maxConcurrentRequests"):
        raw_max = query_first_alias(
            query,
            "max_concurrent_requests",
            "maxConcurrentRequests",
        )
        if raw_max is None or not raw_max.strip():
            updates["max_concurrent_requests"] = None
        else:
            try:
                parsed_max = int(raw_max)
            except ValueError:
                raise WebUISettingsError(
                    "max_concurrent_requests must be an integer"
                ) from None
            if parsed_max <= 0:
                raise WebUISettingsError(
                    "max_concurrent_requests must be greater than zero"
                )
            updates["max_concurrent_requests"] = parsed_max
    for snake, camel in (
        ("extra_headers", "extraHeaders"),
        ("extra_body", "extraBody"),
        ("extra_query", "extraQuery"),
    ):
        if query_has_alias(query, snake, camel):
            updates[snake] = _provider_json_setting(query, snake, camel)
    return updates


def _validated_provider_config(
    provider_config: ProviderConfig | None,
    updates: dict[str, Any],
) -> ProviderConfig:
    config_type = type(provider_config) if provider_config is not None else ProviderConfig
    values = provider_config.model_dump(mode="python") if provider_config is not None else {}
    updates = dict(updates)
    if provider_config is not None:
        for field in _PROVIDER_STRUCTURED_FIELDS:
            if field in updates:
                updates[field] = _restore_redacted_provider_secret_values(
                    updates[field],
                    getattr(provider_config, field),
                )
    values.update(updates)
    try:
        return config_type.model_validate(values)
    except ValueError as exc:
        errors_callback = getattr(exc, "errors", None)
        errors: list[dict[str, Any]] = (
            cast(Any, errors_callback)() if callable(errors_callback) else []
        )
        if errors:
            error = errors[0]
            field = ".".join(str(part) for part in error.get("loc", ()))
            message = str(error.get("msg", "invalid value"))
            raise WebUISettingsError(f"{field}: {message}" if field else message) from exc
        raise WebUISettingsError(str(exc)) from exc


def mask_secret_hint(secret: str | None) -> str | None:
    if not secret:
        return None
    if len(secret) <= 8:
        return "••••"
    return f"{secret[:4]}••••{secret[-4:]}"


def _resolve_env_placeholders(value: str | None) -> str | None:
    if not value:
        return None
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        env_value = os.environ.get(match.group(1))
        if env_value is None:
            missing = True
            return ""
        return env_value

    resolved = _ENV_REF_RE.sub(replace, value).strip()
    if missing and not resolved:
        return None
    return resolved or None


def provider_requires_api_key(spec: Any) -> bool:
    return core_models.provider_requires_api_key(spec)


def provider_requires_api_base(spec: Any) -> bool:
    return core_models.provider_requires_api_base(spec)


def oauth_provider_status(spec: Any) -> dict[str, Any]:
    return core_models.oauth_provider_status(spec)


def provider_configured_for_settings(
    spec: Any,
    provider_config: Any,
    oauth_status: OAuthStatusReader,
) -> bool:
    return core_models.provider_configured(spec, provider_config, oauth_status)


def _dynamic_provider_items(config: Config) -> list[tuple[str, ProviderConfig]]:
    model_extra = config.providers.model_extra or {}
    return [
        (name, provider_config)
        for name, provider_config in model_extra.items()
        if isinstance(provider_config, ProviderConfig)
    ]


def resolve_settings_provider(
    config: Config,
    provider_name: str,
) -> tuple[Any, str, ProviderConfig] | None:
    return core_models.resolve_provider(config, provider_name)


def _provider_advanced_field_names(name: str, spec: Any) -> list[str]:
    fields: list[str] = []
    if spec.backend in {"openai_compat", "anthropic"}:
        fields.append("extra_headers")
    if spec.backend in {"openai_compat", "bedrock", "openai_codex", "xai_grok"}:
        fields.append("extra_body")
    if spec.backend == "openai_compat":
        fields.extend(("extra_query", "proxy"))
    if spec.name in _OAUTH_PROXY_PROVIDERS and "proxy" not in fields:
        fields.append("proxy")
    if spec.name == "openai":
        fields.append("api_type")
    if spec.backend == "bedrock":
        fields.extend(("region", "profile"))
    if find_by_name(name) is None:
        fields.append("thinking_style")
    fields.extend(("max_concurrent_requests", "rate_limit_scope"))
    return fields


def _provider_settings_row(
    name: str,
    spec: Any,
    provider_config: ProviderConfig,
    oauth_status_reader: OAuthStatusReader,
) -> dict[str, Any]:
    oauth_status = oauth_status_reader(spec) if spec.is_oauth else None
    is_custom = find_by_name(name) is None
    row = {
        "name": name,
        "label": provider_config.display_name or spec.label,
        "is_custom": is_custom,
        "configured": (
            bool(oauth_status["configured"])
            if oauth_status is not None
            else provider_configured_for_settings(
                spec,
                provider_config,
                oauth_status_reader,
            )
        ),
        "auth_type": "oauth" if spec.is_oauth else "api_key",
        "api_key_required": provider_requires_api_key(spec),
        "api_key_hint": mask_secret_hint(provider_config.api_key),
        "api_base": provider_config.api_base,
        "default_api_base": spec.default_api_base or None,
        "model_selectable": not spec.is_transcription_only,
        "model_catalog": model_catalog_kind(spec),
        "advanced_fields": _provider_advanced_field_names(name, spec),
        "extra_headers": _redact_provider_secret_values(provider_config.extra_headers),
        "extra_body": _redact_provider_secret_values(provider_config.extra_body),
        "extra_query": _redact_provider_secret_values(provider_config.extra_query),
        "thinking_style": provider_config.thinking_style,
        "region": getattr(provider_config, "region", None),
        "profile": getattr(provider_config, "profile", None),
        "proxy": provider_config.proxy,
        "max_concurrent_requests": provider_config.max_concurrent_requests,
        "rate_limit_scope": provider_config.rate_limit_scope,
    }
    if oauth_status is not None:
        row["oauth_account"] = oauth_status["account"]
        row["oauth_expires_at"] = oauth_status["expires_at"]
        row["oauth_login_supported"] = oauth_status["login_supported"]
    if spec.name == "openai":
        row["api_type"] = provider_config.api_type
    return row


def _provider_settings_rows(
    config: Config,
    selected_provider: str | None,
    oauth_status: OAuthStatusReader,
) -> list[dict[str, Any]]:
    aliases: dict[str, list[Any]] = {}
    for spec in PROVIDERS:
        if spec.settings_alias_for:
            aliases.setdefault(spec.settings_alias_for, []).append(spec)

    rows: list[dict[str, Any]] = []
    for canonical in PROVIDERS:
        if canonical.settings_alias_for:
            continue
        candidates = [canonical, *aliases.get(canonical.name, [])]
        chosen = next((spec for spec in candidates if spec.name == selected_provider), None)
        if chosen is None:
            chosen = next(
                (
                    spec
                    for spec in candidates
                    if (
                        provider_config := getattr(config.providers, spec.name, None)
                    )
                    is not None
                    and provider_configured_for_settings(
                        spec,
                        provider_config,
                        oauth_status,
                    )
                ),
                canonical,
            )
        provider_config = getattr(config.providers, chosen.name, None)
        if provider_config is None:
            continue
        row = _provider_settings_row(
            chosen.name,
            chosen,
            provider_config,
            oauth_status,
        )
        row["label"] = canonical.label
        rows.append(row)
    return rows


def model_catalog_kind(spec: Any) -> str:
    catalog = getattr(spec, "model_catalog", "auto")
    if catalog != "auto":
        return catalog
    if spec.is_transcription_only or spec.is_oauth:
        return "unsupported"
    if spec.backend != "openai_compat" and spec.name != "minimax_anthropic":
        return "unsupported"
    if spec.is_local:
        return "local"
    if spec.is_direct:
        return "custom"
    if spec.is_gateway:
        return "catalog"
    return "official"


def _model_id_from_row(row: Any) -> str | None:
    if isinstance(row, str):
        return row.strip() or None
    if not isinstance(row, dict):
        return None
    row_mapping = cast(dict[str, Any], row)
    for key in ("id", "name", "model"):
        value = row_mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _model_context_window(row: Any) -> int | None:
    if not isinstance(row, dict):
        return None
    row_mapping = cast(dict[str, Any], row)
    for key in (
        "context_window",
        "context_length",
        "max_context_length",
        "max_model_len",
        "max_input_tokens",
    ):
        value = row_mapping.get(key)
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, float) and value > 0:
            return int(value)
    return None


def _model_row_payload(row: Any) -> dict[str, Any] | None:
    model_id = _model_id_from_row(row)
    if not model_id:
        return None
    label: str | None = None
    description: str | None = None
    owned_by: str | None = None
    if isinstance(row, dict):
        row_mapping = cast(dict[str, Any], row)
        raw_label = (
            row_mapping.get("display_name")
            or row_mapping.get("label")
            or row_mapping.get("name")
        )
        if (
            isinstance(raw_label, str)
            and raw_label.strip()
            and raw_label.strip() != model_id
        ):
            label = raw_label.strip()
        raw_description = row_mapping.get("description")
        if isinstance(raw_description, str) and raw_description.strip():
            description = raw_description.strip()
        raw_owner = (
            row_mapping.get("owned_by")
            or row_mapping.get("owner")
            or row_mapping.get("organization")
        )
        if isinstance(raw_owner, str) and raw_owner.strip():
            owned_by = raw_owner.strip()
    payload = {
        "id": model_id,
        "label": label,
        "owned_by": owned_by,
        "context_window": _model_context_window(row),
    }
    if description:
        payload["description"] = description
    return payload


def _extract_model_rows(body: Any) -> list[dict[str, Any]]:
    raw_rows = cast(dict[str, Any], body).get("data") if isinstance(body, dict) else body
    if not isinstance(raw_rows, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_row in cast(list[object], raw_rows):
        row = _model_row_payload(raw_row)
        if row is None or row["id"] in seen:
            continue
        seen.add(row["id"])
        rows.append(row)
    return rows


def provider_models_payload(
    config: Config,
    query: QueryParams,
    *,
    http_get: HttpGet,
) -> dict[str, Any]:
    """Fetch an advisory upstream model list without mutating configuration."""
    provider_name = (query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")

    resolved_provider = resolve_settings_provider(config, provider_name)
    if resolved_provider is None:
        raise WebUISettingsError("unknown provider")
    spec, provider_key, provider_config = resolved_provider

    catalog_kind = model_catalog_kind(spec)
    base_payload: dict[str, Any] = {
        "provider": provider_key,
        "label": spec.label,
        "catalog_kind": catalog_kind,
        "models": [],
        "model_count": 0,
        "message": None,
        "fetched_at": time.time(),
    }
    if catalog_kind == "unsupported":
        return {
            **base_payload,
            "status": "unsupported",
            "message": "Model list is not available for this provider. Type a model ID manually.",
        }
    if catalog_kind == "builtin":
        rows = [
            {
                "id": model.id,
                "label": model.label or None,
                "description": model.description or None,
                "owned_by": spec.label,
                "context_window": model.context_window,
            }
            for model in spec.builtin_models
        ]
        return {
            **base_payload,
            "status": "available",
            "models": rows,
            "model_count": len(rows),
        }
    if catalog_kind == "hybrid":
        proxy = _resolve_env_placeholders(provider_config.proxy)
        catalog = get_oauth_model_catalog(spec.name, proxy=proxy)
        rows = [
            {
                "id": model.id,
                "label": model.label or None,
                "description": model.description or None,
                "owned_by": model.owned_by or spec.label,
                "context_window": model.context_window,
                "reasoning_efforts": list(model.reasoning_efforts),
                "supports_backend_search": model.supports_backend_search,
            }
            for model in catalog.models
        ]
        return {
            **base_payload,
            "status": "available",
            "source": catalog.source,
            "models": rows,
            "model_count": len(rows),
            "message": catalog.message,
            "fetched_at": catalog.fetched_at,
        }

    api_base = _resolve_env_placeholders(provider_config.api_base) or spec.default_api_base
    if spec.name == "openai" and not api_base:
        api_base = "https://api.openai.com/v1"
    if not api_base:
        return {
            **base_payload,
            "status": "missing_api_base",
            "message": "Configure an API base URL to load models.",
        }

    api_key = _resolve_env_placeholders(provider_config.api_key)
    if provider_requires_api_key(spec) and not api_key:
        return {
            **base_payload,
            "status": "not_configured",
            "message": "Configure this provider before loading models.",
        }

    headers = {"Accept": "application/json"}
    if api_key:
        if spec.name == "minimax_anthropic":
            headers["X-Api-Key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    models_url = f"{api_base.rstrip('/')}/models"
    if spec.name == "minimax_anthropic" and not api_base.rstrip("/").endswith("/v1"):
        models_url = f"{api_base.rstrip('/')}/v1/models"

    try:
        response = http_get(
            models_url,
            headers=headers,
            timeout=10.0,
            follow_redirects=False,
        )
        response.raise_for_status()
        rows = _extract_model_rows(response.json())
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            return {
                **base_payload,
                "status": "not_configured",
                "message": "The provider rejected the configured credential.",
            }
        return {
            **base_payload,
            "status": "error",
            "message": f"Model list request failed with HTTP {status}.",
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {
            **base_payload,
            "status": "error",
            "message": f"Could not load models: {exc}",
        }

    return {
        **base_payload,
        "status": "available",
        "models": rows,
        "model_count": len(rows),
    }


def reasoning_effort_values_for(provider_name: str, model: str) -> list[str]:
    return core_models.reasoning_effort_values_for(provider_name, model)


def _model_settings_row(config: Config, model_id: str) -> dict[str, Any]:
    model = config.models[model_id]
    return {
        "model_id": model_id,
        **model.model_dump(mode="python"),
        "is_default": config.agents.defaults.model_id == model_id,
        "usages": core_models.find_model_usages(config, model_id),
        "reasoning_effort_values": reasoning_effort_values_for(
            model.provider,
            model.model,
        ),
    }


def model_settings_payload(
    config: Config,
    *,
    oauth_status: OAuthStatusReader,
) -> ModelSettingsPayload:
    defaults = config.agents.defaults
    selected_model = config.models[defaults.model_id]
    selected_provider = selected_model.provider
    resolved_provider = resolve_settings_provider(config, selected_provider)
    provider_config = resolved_provider[2] if resolved_provider is not None else None

    providers = _provider_settings_rows(config, selected_provider, oauth_status)
    known_provider_names = {row["name"] for row in providers}
    for provider_key, provider_config_row in _dynamic_provider_items(config):
        if provider_key in known_provider_names:
            continue
        providers.append(
            _provider_settings_row(
                provider_key,
                create_dynamic_spec(
                    provider_key,
                    display_name=provider_config_row.display_name or "",
                    thinking_style=provider_config_row.thinking_style or "",
                ),
                provider_config_row,
                oauth_status,
            )
        )

    return {
        "agent": {
            "model_id": defaults.model_id,
            "display_name": selected_model.display_name,
            "provider": selected_model.provider,
            "model": selected_model.model,
            "capabilities": selected_model.capabilities.model_dump(mode="python"),
            "context_window_tokens": selected_model.context_window_tokens,
            "generation_defaults": selected_model.generation_defaults.model_dump(
                mode="python"
            ),
            "has_api_key": bool(
                provider_config is not None and provider_config.api_key
            ),
            "image_analysis_model_id": config.tools.image_analysis.model_id,
            "timezone": defaults.timezone,
            "tool_hint_max_length": defaults.tool_hint_max_length,
        },
        "models": [
            _model_settings_row(config, model_id) for model_id in sorted(config.models)
        ],
        "image_analysis": {
            "enabled": config.tools.image_analysis.enabled,
            "model_id": config.tools.image_analysis.model_id,
            "max_image_mb": config.tools.image_analysis.max_image_mb,
            "max_images": config.tools.image_analysis.max_images,
        },
        "system_prompt_overrides": [
            {"prompt": row.prompt, "model_ids": list(row.model_ids)}
            for row in config.system_prompt_overrides
        ],
        "providers": providers,
        "subagent_roles": [
            {
                "name": name,
                **(SUBAGENT_ROLES.get(name) or {}),
                "model_id": config.subagent_roles[name].model_id
                if name in config.subagent_roles
                else None,
            }
            for name in sorted(set(SUBAGENT_ROLES) | set(config.subagent_roles))
        ],
        "max_concurrent_subagents": defaults.max_concurrent_subagents,
    }


def update_agent_model_settings(
    config: Config,
    query: QueryParams,
    *,
    oauth_status: OAuthStatusReader,
) -> bool:
    del oauth_status
    defaults = config.agents.defaults
    changed = False

    model_id = query_first_alias(query, "model_id", "modelId")
    if model_id is not None:
        selected = model_id.strip()
        if not selected:
            raise WebUISettingsError("model_id is required")
        if selected not in config.models:
            raise WebUISettingsError("unknown model_id")
        if defaults.model_id != selected:
            defaults.model_id = selected
            changed = True

    image_model_id = query_first_alias(
        query,
        "image_analysis_model_id",
        "imageAnalysisModelId",
    )
    if image_model_id is not None:
        selected_image = image_model_id.strip() or None
        if selected_image is not None:
            model = config.models.get(selected_image)
            if model is None:
                raise WebUISettingsError("unknown image analysis model_id")
            if not model.capabilities.vision:
                raise WebUISettingsError(
                    "image analysis model_id must support vision"
                )
        if config.tools.image_analysis.model_id != selected_image:
            config.tools.image_analysis.model_id = selected_image
            changed = True

    return changed


def create_model_configuration(
    config: Config,
    query: QueryParams,
    *,
    oauth_status: OAuthStatusReader,
) -> str:
    del oauth_status
    return cast(
        str,
        _core_model_call(core_models.create_model_configuration, config, query),
    )


def update_model_configuration(
    config: Config,
    query: QueryParams,
    *,
    oauth_status: OAuthStatusReader,
) -> bool:
    del oauth_status
    if query_has_alias(query, "new_name", "newName"):
        raise WebUISettingsError("model_id cannot be renamed")
    return bool(_core_model_call(core_models.update_model_configuration, config, query))


def update_model_prompt_overrides(config: Config, query: QueryParams) -> bool:
    raw_overrides = query_first_alias(query, "overrides", "systemPromptOverrides")
    if raw_overrides is None:
        raise WebUISettingsError("system prompt overrides are required")
    try:
        parsed: object = json.loads(raw_overrides)
    except json.JSONDecodeError:
        raise WebUISettingsError("system prompt overrides must be a JSON array") from None
    if not isinstance(parsed, list):
        raise WebUISettingsError("system prompt overrides must be a JSON array")

    rows: list[SystemPromptOverrideConfig] = []
    bound: set[str] = set()
    for item in cast(list[object], parsed):
        if not isinstance(item, dict):
            raise WebUISettingsError(
                "each override must be an object with prompt and model_ids"
            )
        prompt = str(item.get("prompt") or "").strip()
        raw_model_ids = item.get("model_ids", item.get("modelIds"))
        model_ids: list[str] = []
        if isinstance(raw_model_ids, list):
            model_ids = [
                model_id.strip()
                for model_id in raw_model_ids
                if isinstance(model_id, str) and model_id.strip()
            ]
            model_ids = list(dict.fromkeys(model_ids))
        if not prompt:
            raise WebUISettingsError("override prompt must not be blank")
        if not model_ids:
            raise WebUISettingsError("each override must bind at least one model_id")
        unknown = next(
            (model_id for model_id in model_ids if model_id not in config.models),
            None,
        )
        if unknown is not None:
            raise WebUISettingsError(f"unknown model_id {unknown!r}")
        duplicate = next((model_id for model_id in model_ids if model_id in bound), None)
        if duplicate:
            raise WebUISettingsError(
                f"model_id {duplicate!r} is already bound to another prompt"
            )
        bound.update(model_ids)
        rows.append(SystemPromptOverrideConfig(prompt=prompt, model_ids=model_ids))

    changed = [
        (row.prompt, tuple(row.model_ids)) for row in rows
    ] != [
        (row.prompt, tuple(row.model_ids)) for row in config.system_prompt_overrides
    ]
    if changed:
        config.system_prompt_overrides = rows
    return changed


def delete_model_configuration(config: Config, query: QueryParams) -> None:
    _core_model_call(core_models.delete_model_configuration, config, query)


def update_subagent_roles(config: Config, query: QueryParams) -> None:
    _core_model_call(core_models.update_subagent_roles, config, query)


def _custom_provider_key(config: Config, display_name: str) -> str:
    slug = _MODEL_CONFIGURATION_SLUG_RE.sub("-", display_name.strip().lower()).strip(
        "-_"
    )
    base = f"custom-{slug or 'provider'}"
    if len(base) > 56:
        base = base[:56].rstrip("-_")
    existing = {
        name.replace("_", "-").lower()
        for name, _provider_config in _dynamic_provider_items(config)
    }
    candidate = base
    suffix = 2
    while candidate.replace("_", "-").lower() in existing or find_by_name(candidate):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _provider_display_name_exists(
    config: Config,
    display_name: str,
    *,
    exclude_key: str | None = None,
) -> bool:
    normalized = display_name.strip().casefold()
    if any(spec.label.strip().casefold() == normalized for spec in PROVIDERS):
        return True
    for provider_key, provider_config in _dynamic_provider_items(config):
        if provider_key == exclude_key:
            continue
        label = (
            provider_config.display_name
            or provider_key.replace("-", " ").replace("_", " ").title()
        )
        if label.strip().casefold() == normalized:
            return True
    return False


def create_provider_settings(config: Config, query: QueryParams) -> str:
    display_name = (query_first_alias(query, "name", "displayName") or "").strip()
    if not display_name:
        raise WebUISettingsError("provider name is required")
    if len(display_name) > 80:
        raise WebUISettingsError("provider name must be 80 characters or fewer")
    updates = _provider_config_updates(query)
    allowed = {
        "api_key",
        "api_base",
        "proxy",
        "extra_headers",
        "extra_body",
        "extra_query",
        "thinking_style",
        "display_name",
        "max_concurrent_requests",
        "rate_limit_scope",
    }
    unsupported = set(updates) - allowed
    if unsupported:
        field = sorted(unsupported)[0]
        raise WebUISettingsError(f"{field} is not supported for a custom provider")
    api_base = str(updates.get("api_base") or "")
    if not api_base:
        raise WebUISettingsError("API base is required")
    if _provider_display_name_exists(config, display_name):
        raise WebUISettingsError("provider already exists", status=409)

    provider_key = _custom_provider_key(config, display_name)
    updates["display_name"] = display_name
    updates["api_type"] = "auto"
    provider_config = _validated_provider_config(None, updates)
    setattr(config.providers, provider_key, provider_config)
    return provider_key


def update_provider_settings(
    config: Config,
    query: QueryParams,
) -> tuple[bool, bool]:
    provider_name = (query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")

    resolved_provider = resolve_settings_provider(config, provider_name)
    if resolved_provider is None:
        raise WebUISettingsError("unknown provider")
    spec, provider_key, provider_config = resolved_provider
    updates = _provider_config_updates(query)
    if not spec.is_oauth and spec.name != "openai":
        updates.pop("api_type", None)
    common = {"max_concurrent_requests", "rate_limit_scope"}
    if spec.is_oauth:
        if spec.name not in _OAUTH_PROXY_PROVIDERS:
            raise WebUISettingsError("unknown provider")
        unsupported = set(updates) - {"proxy", "extra_body", *common}
        if unsupported:
            raise WebUISettingsError(
                "OAuth provider only supports proxy, extra_body, concurrency, and rate-limit settings"
            )
    else:
        allowed = {
            "api_key",
            "api_base",
            *_provider_advanced_field_names(provider_key, spec),
        }
        if find_by_name(provider_key) is None:
            allowed.add("display_name")
        unsupported = set(updates) - allowed
        if unsupported:
            field = sorted(unsupported)[0]
            raise WebUISettingsError(f"{field} is not supported for this provider")

    if "display_name" in updates:
        display_name = str(updates["display_name"] or "")
        if not display_name:
            raise WebUISettingsError("provider name is required")
        if len(display_name) > 80:
            raise WebUISettingsError("provider name must be 80 characters or fewer")
        if _provider_display_name_exists(config, display_name, exclude_key=provider_key):
            raise WebUISettingsError("provider already exists", status=409)

    updated_provider_config = _validated_provider_config(provider_config, updates)
    changed = updated_provider_config != provider_config
    if changed:
        setattr(config.providers, provider_key, updated_provider_config)

    image_config = config.tools.image_generation
    image_provider: str | None = None
    if image_config.model_id is not None:
        image_model = config.models.get(image_config.model_id)
        image_provider = image_model.provider if image_model is not None else None
    restart_required = (
        changed
        and image_config.enabled
        and image_provider == provider_key
        and get_image_gen_provider(provider_key) is not None
    )
    return changed, restart_required


def login_oauth_provider(
    config: Config,
    query: QueryParams,
    *,
    oauth_flows: WebUIOAuthFlowRegistry,
    config_path: Path | None,
    settings_payload: SettingsPayloadBuilder,
) -> dict[str, Any]:
    provider_name = (query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")
    spec = find_by_name(provider_name)
    if spec is None or not spec.is_oauth:
        raise WebUISettingsError("unknown OAuth provider")

    if spec.name == "openai_codex":
        try:
            from nanobot.providers.openai_codex_oauth import start_openai_codex_oauth_login
        except ImportError:
            raise WebUISettingsError(OAUTH_CLI_KIT_MISSING_MESSAGE, status=500) from None

        try:
            proxy = resolve_config_env_vars(
                config,
                config_path=config_path,
            ).providers.openai_codex.proxy or None
        except ValueError as exc:
            raise WebUISettingsError(str(exc), status=400) from exc
        remote_browser_value = query_first(query, "remote_browser")
        remote_browser = (
            remote_browser_value is not None
            and remote_browser_value.strip().lower() in {"1", "true", "yes", "on"}
        )
        try:
            flow = start_openai_codex_oauth_login(
                proxy=proxy,
                timeout_s=_WEBUI_OAUTH_TIMEOUT_S,
                open_browser=not remote_browser,
            )
        except Exception as exc:
            raise WebUISettingsError(
                f"OpenAI Codex OAuth login failed: {exc}",
                status=502,
            ) from exc
        flow_id = secrets.token_urlsafe(24)
        oauth_flows.register(spec.name, flow_id, flow)
        return {
            "status": "authorization_required",
            "provider": spec.name,
            "flow_id": flow_id,
            "authorization_url": flow.authorization_url,
            "expires_in": flow.remaining_seconds,
            "completion_input": "callback_url",
        }

    if spec.name == "github_copilot":
        try:
            from nanobot.providers.github_copilot_provider import (
                get_github_copilot_login_status,
                login_github_copilot,
            )
        except ImportError:
            raise WebUISettingsError(OAUTH_CLI_KIT_MISSING_MESSAGE, status=500) from None

        token = get_github_copilot_login_status()
        if not token:
            token = login_github_copilot(print_fn=lambda _message: None)
        if not (token and token.access):
            raise WebUISettingsError("OAuth login failed", status=401)
        invalidate_oauth_model_catalog(spec.name)
        return settings_payload(config_path=config_path)

    if spec.name == "xai_grok":
        from nanobot.providers.xai_oauth import start_xai_oauth_login

        try:
            proxy = resolve_config_env_vars(
                config,
                config_path=config_path,
            ).providers.xai_grok.proxy or None
        except ValueError as exc:
            raise WebUISettingsError(str(exc), status=400) from exc
        try:
            flow = start_xai_oauth_login(
                proxy=proxy,
                timeout_s=_WEBUI_OAUTH_TIMEOUT_S,
            )
        except Exception as exc:
            raise WebUISettingsError(f"xAI OAuth login failed: {exc}", status=502) from exc
        flow_id = secrets.token_urlsafe(24)
        oauth_flows.register(spec.name, flow_id, flow)
        return {
            "status": "authorization_required",
            "provider": spec.name,
            "flow_id": flow_id,
            "authorization_url": flow.authorization_url,
            "expires_in": flow.remaining_seconds,
            "completion_input": "authorization_code",
        }

    raise WebUISettingsError("OAuth login is not supported for this provider")


def complete_oauth_provider(
    query: QueryParams,
    authorization_response: str | None = None,
    *,
    oauth_flows: WebUIOAuthFlowRegistry,
    config_path: Path | None,
    settings_payload: SettingsPayloadBuilder,
) -> dict[str, Any]:
    provider_name = (query_first(query, "provider") or "").strip()
    flow_id = (query_first(query, "flow_id") or "").strip()
    spec = find_by_name(provider_name)
    if spec is None or spec.name not in {"openai_codex", "xai_grok"}:
        raise WebUISettingsError(
            "OAuth completion is not supported for this provider"
        )
    if not flow_id:
        raise WebUISettingsError("flow_id is required")

    flow = oauth_flows.get(spec.name, flow_id)
    if flow is None:
        raise WebUISettingsError(
            f"{spec.label} sign-in expired. Start again.",
            status=410,
        )

    try:
        if spec.name == "openai_codex":
            from nanobot.providers.openai_codex_oauth import (
                OpenAICodexOAuthInputError,
                complete_openai_codex_oauth_login,
            )

            try:
                token = complete_openai_codex_oauth_login(
                    flow,
                    authorization_response,
                )
            except OpenAICodexOAuthInputError as exc:
                raise WebUISettingsError(str(exc), status=400) from exc
        else:
            from nanobot.providers.xai_oauth import complete_xai_oauth_login

            token = complete_xai_oauth_login(flow, authorization_response)
    except WebUISettingsError:
        raise
    except Exception as exc:
        oauth_flows.remove(spec.name, flow_id, flow)
        raise WebUISettingsError(
            f"{spec.label} OAuth login failed: {exc}",
            status=502,
        ) from exc
    if token is None:
        return {
            "status": "pending",
            "provider": spec.name,
            "flow_id": flow_id,
        }
    oauth_flows.remove(spec.name, flow_id, flow, cancel=False)
    if not token.access:
        raise WebUISettingsError("OAuth login failed", status=401)
    invalidate_oauth_model_catalog(spec.name)
    return settings_payload(config_path=config_path)


def logout_oauth_provider(
    query: QueryParams,
    *,
    oauth_flows: WebUIOAuthFlowRegistry,
    config_path: Path | None,
    settings_payload: SettingsPayloadBuilder,
) -> dict[str, Any]:
    provider_name = (query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")
    spec = find_by_name(provider_name)
    if spec is None or not spec.is_oauth:
        raise WebUISettingsError("unknown OAuth provider")

    if spec.name == "openai_codex":
        try:
            from oauth_cli_kit.providers import OPENAI_CODEX_PROVIDER
            from oauth_cli_kit.storage import FileTokenStorage
        except ImportError:
            raise WebUISettingsError(OAUTH_CLI_KIT_MISSING_MESSAGE, status=500) from None
        oauth_flows.clear(spec.name)
        token_path = FileTokenStorage(
            token_filename=OPENAI_CODEX_PROVIDER.token_filename
        ).get_token_path()
    elif spec.name == "github_copilot":
        try:
            from nanobot.providers.github_copilot_provider import get_storage
        except ImportError:
            raise WebUISettingsError(OAUTH_CLI_KIT_MISSING_MESSAGE, status=500) from None
        token_path = get_storage().get_token_path()
    elif spec.name == "xai_grok":
        from nanobot.providers.xai_oauth import logout_xai_oauth

        oauth_flows.clear(spec.name)
        logout_xai_oauth()
        invalidate_oauth_model_catalog(spec.name)
        return settings_payload(config_path=config_path)
    else:
        raise WebUISettingsError("OAuth logout is not supported for this provider")

    for path in (token_path, token_path.with_suffix(".lock")):
        with suppress(FileNotFoundError):
            path.unlink()
    invalidate_oauth_model_catalog(spec.name)
    return settings_payload(config_path=config_path)


class ModelSettingsHandler:
    """Handle model/provider commands after transport authentication and decoding."""

    def __init__(self, settings: WebUISettingsServices, logger: Any) -> None:
        self.settings = settings
        self.logger = logger

    def _refresh_runtime_config(self) -> None:
        if self.settings.refresh_runtime_config is not None:
            self.settings.refresh_runtime_config()

    async def handle(
        self,
        action: str,
        request: SettingsRequest,
        operations: ModelSettingsOperations,
    ) -> SettingsRouteResult:
        try:
            if action == "agent-update":
                payload = self.settings.mutate(operations.update_agent, request.query)
                self._refresh_runtime_config()
                return SettingsRouteResult.success(
                    payload,
                    decorate_restart=True,
                    restart_section="runtime",
                )

            if action == "model-update":
                payload = self.settings.mutate(operations.update_model, request.query)
                self._refresh_runtime_config()
                return SettingsRouteResult.success(payload, decorate_restart=True)

            mutation = {
                "model-create": operations.create_model,
                "model-delete": operations.delete_model,
                "prompt-overrides-update": operations.update_prompt_overrides,
                "provider-create": operations.create_provider,
                "subagent-roles-update": operations.update_subagent_roles,
            }.get(action)
            if mutation is not None:
                payload = self.settings.mutate(mutation, request.query)
                self._refresh_runtime_config()
                return SettingsRouteResult.success(payload, decorate_restart=True)

            if action == "provider-update":
                payload = self.settings.mutate(
                    operations.update_provider,
                    request.query,
                )
                payload, image_restart_cleared = (
                    await operations.apply_image_runtime_change(payload)
                )
                self._refresh_runtime_config()
                return SettingsRouteResult.success(
                    payload,
                    decorate_restart=True,
                    restart_section="image",
                    clear_restart_section=(
                        "image" if image_restart_cleared else None
                    ),
                )

            if action == "provider-models":
                try:
                    payload = await asyncio.to_thread(
                        self.settings.read,
                        operations.provider_models,
                        request.query,
                    )
                except WebUISettingsError:
                    raise
                except Exception:
                    self.logger.exception("failed to load provider model list")
                    return SettingsRouteResult.failure(
                        500,
                        "failed to load provider model list",
                    )
                return SettingsRouteResult.success(payload)

            if action == "oauth-login":
                payload = await asyncio.to_thread(
                    self.settings.read,
                    operations.oauth_login,
                    request.query,
                    oauth_flows=self.settings.oauth_flows,
                )
            elif action == "oauth-complete":
                raw_response = (request.payload or {}).get("authorization_response")
                if raw_response is not None and not isinstance(raw_response, str):
                    raise WebUISettingsError(
                        "OAuth authorization response must be a string"
                    )
                payload = await asyncio.to_thread(
                    self.settings.read,
                    operations.oauth_complete,
                    request.query,
                    raw_response or None,
                    oauth_flows=self.settings.oauth_flows,
                )
            elif action == "oauth-logout":
                payload = await asyncio.to_thread(
                    self.settings.read,
                    operations.oauth_logout,
                    request.query,
                    oauth_flows=self.settings.oauth_flows,
                )
            else:
                return SettingsRouteResult.failure(404, "unknown settings action")
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)

        if payload.get("status") in {"authorization_required", "pending"}:
            return SettingsRouteResult.success(payload)
        return SettingsRouteResult.success(payload, decorate_restart=True)
