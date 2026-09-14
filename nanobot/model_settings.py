"""Canonical model/provider configuration rules shared by Agent and WebUI."""

from __future__ import annotations

import json
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Callable, cast

from nanobot.agent.subagent_roles import SUBAGENT_ROLES
from nanobot.config.schema import Config, ProviderConfig, SubagentRoleConfig, SubagentRoleName
from nanobot.model_domain import ModelConfig, get_model, validate_model_id
from nanobot.providers.image_generation import get_image_gen_provider
from nanobot.providers.registry import PROVIDERS, create_dynamic_spec, find_by_name

QueryParams = dict[str, list[str]]
OAuthStatusReader = Callable[[Any], dict[str, Any]]

_REDACTED_PROVIDER_SECRET = "••••••••"
_PROVIDER_STRUCTURED_FIELDS = ("extra_headers", "extra_body", "extra_query")
_PROVIDER_SECRET_KEYS = frozenset({
    "auth", "authentication", "authorization", "bearer", "cookie", "credential",
    "credentials", "hmac", "key", "passphrase", "passwd", "proxyauthorization",
    "setcookie", "sig", "signature",
})
_PROVIDER_SECRET_KEY_SUFFIXES = (
    "accesskey", "apikey", "encryptionkey", "password", "privatekey", "secret",
    "secretkey", "signingkey", "subscriptionkey", "token",
)
_OAUTH_PROXY_PROVIDERS = {"openai_codex", "xai_grok"}
_DEFAULT_REASONING_EFFORT_VALUES: tuple[str, ...] = ("", "low", "medium", "high")
_MODEL_NESTED_FIELDS = frozenset({"capabilities", "pricing", "generation_defaults", "generationDefaults", "pools"})


@dataclass(frozen=True)
class ModelSettingsError(ValueError):
    """Transport-neutral model/provider validation failure."""

    message: str
    status: int = 400

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ModelInUseError(ModelSettingsError):
    """Deletion was blocked because consumers still reference the model."""

    usages: tuple[str, ...] = ()


def _query_first(query: QueryParams, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _query_first_alias(query: QueryParams, snake: str, camel: str) -> str | None:
    value = _query_first(query, snake)
    return _query_first(query, camel) if value is None else value


def _query_has_alias(query: QueryParams, snake: str, camel: str) -> bool:
    return snake in query or camel in query


def _provider_json_setting(query: QueryParams, snake: str, camel: str) -> dict[str, Any] | None:
    raw = (_query_first_alias(query, snake, camel) or "").strip()
    if not raw:
        return None
    try:
        value: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelSettingsError(f"{snake} must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ModelSettingsError(f"{snake} must be a JSON object")
    return cast(dict[str, Any], value) or None


def _provider_setting_key_is_secret(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return compact in _PROVIDER_SECRET_KEYS or compact.endswith(_PROVIDER_SECRET_KEY_SUFFIXES)


def _restore_redacted_provider_secret_values(submitted: Any, current: Any, *, secret: bool = False) -> Any:
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


def _parse_optional_positive_int(value: str | None, field: str) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError:
        raise ModelSettingsError(f"{field} must be an integer") from None
    if parsed <= 0:
        raise ModelSettingsError(f"{field} must be greater than zero")
    return parsed


def _provider_config_updates(query: QueryParams) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for snake, camel in (
        ("api_key", "apiKey"), ("api_base", "apiBase"), ("api_type", "apiType"),
        ("proxy", "proxy"), ("thinking_style", "thinkingStyle"), ("region", "region"),
        ("profile", "profile"), ("display_name", "displayName"),
        ("rate_limit_scope", "rateLimitScope"),
    ):
        if _query_has_alias(query, snake, camel):
            value = (_query_first_alias(query, snake, camel) or "").strip()
            updates[snake] = value or ("auto" if snake == "api_type" else None)
    if _query_has_alias(query, "max_concurrent_requests", "maxConcurrentRequests"):
        updates["max_concurrent_requests"] = _parse_optional_positive_int(
            _query_first_alias(query, "max_concurrent_requests", "maxConcurrentRequests"),
            "max_concurrent_requests",
        )
    for snake, camel in (
        ("extra_headers", "extraHeaders"),
        ("extra_body", "extraBody"),
        ("extra_query", "extraQuery"),
    ):
        if _query_has_alias(query, snake, camel):
            updates[snake] = _provider_json_setting(query, snake, camel)
    return updates


def _validation_message(exc: ValueError) -> str:
    errors_callback = getattr(exc, "errors", None)
    errors: list[dict[str, Any]] = cast(Any, errors_callback)() if callable(errors_callback) else []
    if not errors:
        return str(exc)
    parts: list[str] = []
    for error in errors:
        field = ".".join(str(part) for part in error.get("loc", ()))
        message = str(error.get("msg", "invalid value"))
        parts.append(f"{field}: {message}" if field else message)
    return "; ".join(parts)


def _validated_provider_config(provider_config: ProviderConfig | None, updates: dict[str, Any]) -> ProviderConfig:
    config_type = type(provider_config) if provider_config is not None else ProviderConfig
    values = provider_config.model_dump(mode="python") if provider_config is not None else {}
    updates = dict(updates)
    if provider_config is not None:
        for field in _PROVIDER_STRUCTURED_FIELDS:
            if field in updates:
                updates[field] = _restore_redacted_provider_secret_values(
                    updates[field], getattr(provider_config, field)
                )
    values.update(updates)
    try:
        return config_type.model_validate(values)
    except ValueError as exc:
        raise ModelSettingsError(_validation_message(exc)) from exc


def oauth_provider_status(spec: Any) -> dict[str, Any]:
    if not getattr(spec, "is_oauth", False):
        return {"configured": False, "account": None, "expires_at": None, "login_supported": False}
    if spec.name == "openai_codex":
        try:
            from oauth_cli_kit.providers import OPENAI_CODEX_PROVIDER
            from oauth_cli_kit.storage import FileTokenStorage
        except Exception:
            return {"configured": False, "account": None, "expires_at": None, "login_supported": False}
        token = None
        with suppress(Exception):
            token = FileTokenStorage(token_filename=OPENAI_CODEX_PROVIDER.token_filename).load()
        expires_at = getattr(token, "expires", None) if token else None
        now_ms = int(time.time() * 1000)
        return {
            "configured": bool(token and token.access and (getattr(token, "refresh", None) or (expires_at and expires_at > now_ms))),
            "account": getattr(token, "account_id", None) if token else None,
            "expires_at": expires_at,
            "login_supported": True,
        }
    if spec.name == "github_copilot":
        try:
            from nanobot.providers.github_copilot_provider import get_github_copilot_login_status
        except Exception:
            return {"configured": False, "account": None, "expires_at": None, "login_supported": False}
        token = None
        with suppress(Exception):
            token = get_github_copilot_login_status()
        return {
            "configured": bool(token and token.access and token.expires > int(time.time() * 1000)),
            "account": getattr(token, "account_id", None) if token else None,
            "expires_at": getattr(token, "expires", None) if token else None,
            "login_supported": True,
        }
    if spec.name == "xai_grok":
        try:
            from nanobot.providers.xai_oauth import get_xai_oauth_login_status
        except Exception:
            return {"configured": False, "account": None, "expires_at": None, "login_supported": False}
        token = None
        with suppress(Exception):
            token = get_xai_oauth_login_status()
        expires_at = getattr(token, "expires", None) if token else None
        now_ms = int(time.time() * 1000)
        return {
            "configured": bool(token and token.access and (getattr(token, "refresh", None) or (expires_at and expires_at > now_ms))),
            "account": getattr(token, "account_id", None) if token else None,
            "expires_at": expires_at,
            "login_supported": True,
        }
    return {"configured": False, "account": None, "expires_at": None, "login_supported": False}


def provider_requires_api_key(spec: Any) -> bool:
    return not (spec.name == "azure_openai" or spec.is_oauth or spec.is_local or spec.is_direct)


def provider_requires_api_base(spec: Any) -> bool:
    if spec.name == "azure_openai":
        return True
    return bool(spec.backend == "openai_compat" and spec.is_direct and not spec.default_api_base)


def provider_configured(spec: Any, provider_config: Any, oauth_status: OAuthStatusReader = oauth_provider_status) -> bool:
    if spec.is_oauth:
        return bool(oauth_status(spec)["configured"])
    if provider_requires_api_base(spec):
        return bool(provider_config.api_base)
    if provider_requires_api_key(spec):
        return bool(provider_config.api_key)
    return bool(provider_config.api_key or provider_config.api_base or getattr(provider_config, "region", None) or getattr(provider_config, "profile", None))


def _dynamic_provider_items(config: Config) -> list[tuple[str, ProviderConfig]]:
    return [
        (name, provider_config)
        for name, provider_config in (config.providers.model_extra or {}).items()
        if isinstance(provider_config, ProviderConfig)
    ]


def resolve_provider(config: Config, provider_name: str) -> tuple[Any, str, ProviderConfig] | None:
    spec = find_by_name(provider_name)
    if spec is not None:
        provider_config = getattr(config.providers, spec.name, None)
        return (spec, spec.name, provider_config) if isinstance(provider_config, ProviderConfig) else None
    normalized = provider_name.replace("-", "_")
    for extra_name, provider_config in _dynamic_provider_items(config):
        if provider_name == extra_name or normalized == extra_name.replace("-", "_"):
            return (
                create_dynamic_spec(extra_name, display_name=provider_config.display_name or "", thinking_style=provider_config.thinking_style or ""),
                extra_name,
                provider_config,
            )
    return None


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
    return fields


def _provider_id(value: str) -> str:
    try:
        return validate_model_id(value.strip())
    except ValueError as exc:
        raise ModelSettingsError(str(exc)) from exc


def create_provider_settings(config: Config, query: QueryParams) -> str:
    provider_key = _provider_id(_query_first(query, "provider") or "")
    if resolve_provider(config, provider_key) is not None:
        raise ModelSettingsError("provider already exists", status=409)
    updates = _provider_config_updates(query)
    allowed = {
        "api_key", "api_base", "proxy", "extra_headers", "extra_body", "extra_query",
        "thinking_style", "display_name", "max_concurrent_requests", "rate_limit_scope",
    }
    unsupported = set(updates) - allowed
    if unsupported:
        raise ModelSettingsError(f"{sorted(unsupported)[0]} is not supported for a custom provider")
    if not str(updates.get("api_base") or ""):
        raise ModelSettingsError("API base is required")
    updates.setdefault("display_name", provider_key)
    updates["api_type"] = "auto"
    setattr(config.providers, provider_key, _validated_provider_config(None, updates))
    return provider_key


def update_provider_settings(config: Config, query: QueryParams) -> tuple[bool, bool]:
    provider_name = (_query_first(query, "provider") or "").strip()
    if not provider_name:
        raise ModelSettingsError("provider is required")
    resolved = resolve_provider(config, provider_name)
    if resolved is None:
        raise ModelSettingsError("unknown provider")
    spec, provider_key, provider_config = resolved
    updates = _provider_config_updates(query)
    if not spec.is_oauth and spec.name != "openai":
        updates.pop("api_type", None)
    common = {"max_concurrent_requests", "rate_limit_scope"}
    if spec.is_oauth:
        if spec.name not in _OAUTH_PROXY_PROVIDERS:
            raise ModelSettingsError("unknown provider")
        unsupported = set(updates) - {"proxy", "extra_body", *common}
        if unsupported:
            raise ModelSettingsError("OAuth provider only supports proxy, extra_body, concurrency, and rate-limit settings")
    else:
        allowed = {"api_key", "api_base", *_provider_advanced_field_names(provider_key, spec), *common}
        if find_by_name(provider_key) is None:
            allowed.add("display_name")
        unsupported = set(updates) - allowed
        if unsupported:
            raise ModelSettingsError(f"{sorted(unsupported)[0]} is not supported for this provider")
    updated_provider_config = _validated_provider_config(provider_config, updates)
    changed = updated_provider_config != provider_config
    if changed:
        setattr(config.providers, provider_key, updated_provider_config)
    image_config = config.tools.image_generation
    image_provider: str | None = None
    if image_config.model_id is not None:
        image_provider = get_model(config.models, image_config.model_id).provider
    restart_required = (
        changed
        and image_config.enabled
        and image_provider == provider_key
        and get_image_gen_provider(provider_key) is not None
    )
    return changed, restart_required


def reasoning_effort_values_for(provider_name: str, model: str) -> list[str]:
    spec = find_by_name(provider_name) if provider_name else None
    if spec is None:
        return list(_DEFAULT_REASONING_EFFORT_VALUES)
    model_lower = (model or "").lower()
    if model_lower.rsplit("/", 1)[-1] == "kimi-k3":
        return ["", "max"]
    implicit = getattr(spec, "implicit_reasoning_models", ())
    if implicit and any(pattern in model_lower for pattern in implicit):
        return [""]
    remap = getattr(spec, "reasoning_effort_remap", ())
    if remap:
        wire_values: list[str] = []
        for _user_value, wire_value in remap:
            if wire_value and wire_value != "none" and wire_value not in wire_values:
                wire_values.append(wire_value)
        return ["", *wire_values]
    return list(_DEFAULT_REASONING_EFFORT_VALUES)


def _require_known_provider(config: Config, provider: str) -> None:
    if resolve_provider(config, provider) is None:
        raise ModelSettingsError("unknown provider")


def _validate_model_payload(config: Config, payload: dict[str, Any]) -> ModelConfig:
    try:
        model = ModelConfig.model_validate(payload)
    except ValueError as exc:
        raise ModelSettingsError(_validation_message(exc)) from exc
    _require_known_provider(config, model.provider)
    return model


def create_model(config: Config, *, model_id: str, values: dict[str, Any]) -> ModelConfig:
    try:
        canonical_id = validate_model_id(model_id)
    except ValueError as exc:
        raise ModelSettingsError(str(exc)) from exc
    if canonical_id in config.models:
        raise ModelSettingsError("model already exists", status=409)
    model = _validate_model_payload(config, values)
    config.models[canonical_id] = model
    return model


def update_model(config: Config, *, model_id: str, changes: dict[str, Any]) -> ModelConfig:
    try:
        canonical_id = validate_model_id(model_id)
    except ValueError as exc:
        raise ModelSettingsError(str(exc)) from exc
    current = config.models.get(canonical_id)
    if current is None:
        raise ModelSettingsError("unknown model")
    payload = current.model_dump(mode="python")
    payload.update(changes)
    updated = _validate_model_payload(config, payload)
    config.models[canonical_id] = updated
    return updated


def find_model_usages(config: Config, model_id: str) -> list[str]:
    usages: list[str] = []
    defaults = config.agents.defaults
    if defaults.model_id == model_id:
        usages.append("agents.defaults.model_id")
    dream = defaults.dream
    if dream.model_id == model_id:
        usages.append("dream.model_id")
    if dream.fallback_model_id == model_id:
        usages.append("dream.fallback_model_id")
    for role, binding in sorted(config.subagent_roles.items()):
        if binding.model_id == model_id:
            usages.append(f"subagent_roles.{role}.model_id")
    if config.transcription.model_id == model_id:
        usages.append("transcription.model_id")
    for index, override in enumerate(config.system_prompt_overrides):
        if model_id in override.model_ids:
            usages.append(f"system_prompt_overrides[{index}].model_ids")
    tools = getattr(config, "tools", None)
    for tool_name in ("image_analysis", "image_generation"):
        tool_config = getattr(tools, tool_name, None)
        if getattr(tool_config, "model_id", None) == model_id:
            usages.append(f"tools.{tool_name}.model_id")
    return usages


def delete_model(config: Config, *, model_id: str) -> None:
    try:
        canonical_id = validate_model_id(model_id)
    except ValueError as exc:
        raise ModelSettingsError(str(exc)) from exc
    if canonical_id not in config.models:
        raise ModelSettingsError("unknown model")
    usages = find_model_usages(config, canonical_id)
    if usages:
        raise ModelInUseError("model is in use", status=409, usages=tuple(usages))
    del config.models[canonical_id]


def model_catalog_entry(config: Config, model_id: str) -> dict[str, Any]:
    model = config.models.get(model_id)
    if model is None:
        raise ModelSettingsError("unknown model")
    return {
        "model_id": model_id,
        **model.model_dump(mode="python"),
        "is_default": config.agents.defaults.model_id == model_id,
        "usages": find_model_usages(config, model_id),
    }


def _decode_model_query(query: QueryParams) -> tuple[str, dict[str, Any]]:
    model_id = (_query_first_alias(query, "model_id", "modelId") or "").strip()
    payload: dict[str, Any] = {}
    for key, values in query.items():
        if key in {"model_id", "modelId"} or not values:
            continue
        raw: Any = values[0]
        if key in _MODEL_NESTED_FIELDS and isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ModelSettingsError(f"{key} must be valid JSON") from exc
        payload[key] = raw
    return model_id, payload


def create_model_configuration(config: Config, query: QueryParams, *, oauth_status: OAuthStatusReader = oauth_provider_status) -> str:
    del oauth_status
    model_id, payload = _decode_model_query(query)
    create_model(config, model_id=model_id, values=payload)
    return model_id


def update_model_configuration(config: Config, query: QueryParams, *, oauth_status: OAuthStatusReader = oauth_provider_status) -> bool:
    del oauth_status
    model_id, changes = _decode_model_query(query)
    before = config.models.get(model_id)
    updated = update_model(config, model_id=model_id, changes=changes)
    return before != updated


def delete_model_configuration(config: Config, query: QueryParams) -> None:
    model_id = (_query_first_alias(query, "model_id", "modelId") or "").strip()
    delete_model(config, model_id=model_id)


def update_subagent_roles(config: Config, query: QueryParams) -> None:
    raw = _query_first(query, "bindings")
    try:
        bindings = json.loads(raw) if raw is not None else None
    except (TypeError, json.JSONDecodeError):
        raise ModelSettingsError("bindings must be an object of role names to model IDs or null") from None
    if not isinstance(bindings, dict):
        raise ModelSettingsError("bindings must be an object of role names to model IDs or null")
    updates: dict[SubagentRoleName, SubagentRoleConfig] = {}
    for name, model_id in cast(dict[str, str | None], bindings).items():
        if name not in config.subagent_roles and name not in SUBAGENT_ROLES:
            raise ModelSettingsError("unknown subagent role")
        if model_id is not None:
            try:
                validate_model_id(model_id)
            except ValueError as exc:
                raise ModelSettingsError(str(exc)) from exc
            if model_id not in config.models:
                raise ModelSettingsError("unknown model_id in role bindings")
        current = config.subagent_roles.get(name, SubagentRoleConfig())
        values = current.model_dump(mode="python")
        values["model_id"] = model_id
        updates[cast(SubagentRoleName, name)] = SubagentRoleConfig.model_validate(values)
    config.subagent_roles.update(updates)


def _safe_provider_catalog(config: Config) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec in PROVIDERS:
        if spec.settings_alias_for or spec.name in seen:
            continue
        provider_config = getattr(config.providers, spec.name, None)
        if isinstance(provider_config, ProviderConfig):
            providers.append({
                "provider": spec.name,
                "display_name": provider_config.display_name or spec.label,
                "configured": provider_configured(spec, provider_config),
                "api_base": provider_config.api_base,
                "api_type": provider_config.api_type,
                "max_concurrent_requests": provider_config.max_concurrent_requests,
                "rate_limit_scope": provider_config.rate_limit_scope,
                "oauth": oauth_provider_status(spec),
            })
            seen.add(spec.name)
    for name, provider_config in _dynamic_provider_items(config):
        spec = create_dynamic_spec(
            name,
            display_name=provider_config.display_name or "",
            thinking_style=provider_config.thinking_style or "",
        )
        providers.append({
            "provider": name,
            "display_name": provider_config.display_name or name,
            "configured": provider_configured(spec, provider_config),
            "api_base": provider_config.api_base,
            "api_type": provider_config.api_type,
            "max_concurrent_requests": provider_config.max_concurrent_requests,
            "rate_limit_scope": provider_config.rate_limit_scope,
            "oauth": oauth_provider_status(spec),
        })
    return providers


def management_catalog(config: Config) -> dict[str, Any]:
    defaults = config.agents.defaults
    return {
        "status": "ok",
        "models": [model_catalog_entry(config, model_id) for model_id in sorted(config.models)],
        "subagent_roles": [
            {
                "name": name,
                **(SUBAGENT_ROLES.get(name) or {}),
                "model_id": binding.model_id,
            }
            for name, binding in sorted(config.subagent_roles.items())
        ],
        "max_concurrent_subagents": defaults.max_concurrent_subagents,
        "providers": _safe_provider_catalog(config),
    }
