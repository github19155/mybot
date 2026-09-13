"""Core model/provider configuration rules shared by Agent and WebUI."""

from __future__ import annotations

import json
import math
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Callable, cast

from nanobot.agent.subagent_roles import SUBAGENT_ROLES
from nanobot.config.schema import (
    Config,
    ModelPresetConfig,
    ProviderConfig,
    SubagentRoleConfig,
    SubagentRoleName,
)
from nanobot.providers.image_generation import get_image_gen_provider
from nanobot.providers.registry import PROVIDERS, create_dynamic_spec, find_by_name

QueryParams = dict[str, list[str]]
OAuthStatusReader = Callable[[Any], dict[str, Any]]

_MODEL_CONFIGURATION_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
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


@dataclass(frozen=True)
class ModelSettingsError(ValueError):
    """Transport-neutral model/provider validation failure."""

    message: str
    status: int = 400

    def __str__(self) -> str:
        return self.message


def _query_first(query: QueryParams, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _query_first_alias(query: QueryParams, snake: str, camel: str) -> str | None:
    value = _query_first(query, snake)
    return _query_first(query, camel) if value is None else value


def _query_has_alias(query: QueryParams, snake: str, camel: str) -> bool:
    return snake in query or camel in query


def _parse_bool(value: str, field: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"1", "0", "true", "false", "yes", "no"}:
        raise ModelSettingsError(f"{field} must be boolean")
    return normalized in {"1", "true", "yes"}


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


def _provider_config_updates(query: QueryParams) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for snake, camel in (
        ("api_key", "apiKey"), ("api_base", "apiBase"), ("api_type", "apiType"),
        ("proxy", "proxy"), ("thinking_style", "thinkingStyle"), ("region", "region"),
        ("profile", "profile"), ("display_name", "displayName"),
    ):
        if _query_has_alias(query, snake, camel):
            value = (_query_first_alias(query, snake, camel) or "").strip()
            updates[snake] = value or ("auto" if snake == "api_type" else None)
    for snake, camel in (
        ("extra_headers", "extraHeaders"),
        ("extra_body", "extraBody"),
        ("extra_query", "extraQuery"),
    ):
        if _query_has_alias(query, snake, camel):
            updates[snake] = _provider_json_setting(query, snake, camel)
    return updates


def _validated_provider_config(provider_config: ProviderConfig | None, updates: dict[str, Any]) -> ProviderConfig:
    config_type = type(provider_config) if provider_config is not None else ProviderConfig
    values = provider_config.model_dump(mode="python") if provider_config is not None else {}
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
        errors_callback = getattr(exc, "errors", None)
        errors: list[dict[str, Any]] = cast(Any, errors_callback)() if callable(errors_callback) else []
        if errors:
            error = errors[0]
            field = ".".join(str(part) for part in error.get("loc", ()))
            message = str(error.get("msg", "invalid value"))
            raise ModelSettingsError(f"{field}: {message}" if field else message) from exc
        raise ModelSettingsError(str(exc)) from exc


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


def _parse_positive_int(value: str | None, field: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        raise ModelSettingsError(f"{field} must be an integer") from None
    if parsed <= 0:
        raise ModelSettingsError(f"{field} must be greater than zero")
    return parsed


def _parse_temperature(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        raise ModelSettingsError("temperature must be a number") from None
    if not math.isfinite(parsed) or parsed < 0 or parsed > 2:
        raise ModelSettingsError("temperature must be between 0 and 2")
    return parsed


def _model_configuration_slug(label: str) -> str:
    normalized = _MODEL_CONFIGURATION_SLUG_RE.sub("-", label.strip().lower()).strip("-_")
    if not normalized:
        raise ModelSettingsError("configuration name is required")
    if normalized == "default":
        raise ModelSettingsError("configuration name is reserved")
    return normalized[:48].rstrip("-_") if len(normalized) > 48 else normalized


def _model_configuration_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ModelSettingsError("configuration name is required")
    if name.casefold() == "default":
        raise ModelSettingsError("configuration name is reserved")
    if len(name) > 48:
        raise ModelSettingsError("configuration name must be 48 characters or fewer")
    if not name.isprintable():
        raise ModelSettingsError("configuration name contains unsupported characters")
    return name


def _model_configuration_name_exists(config: Config, name: str, *, exclude: str | None = None) -> bool:
    normalized = name.casefold()
    return any(existing != exclude and existing.casefold() == normalized for existing in config.model_presets)


def _rename_model_configuration(config: Config, old_name: str, new_name: str) -> bool:
    if old_name == new_name:
        return False
    if _model_configuration_name_exists(config, new_name, exclude=old_name):
        raise ModelSettingsError("configuration already exists", status=409)
    config.model_presets = {(new_name if name == old_name else name): preset for name, preset in config.model_presets.items()}
    defaults = config.agents.defaults
    if defaults.model_preset == old_name:
        defaults.model_preset = new_name
    defaults.fallback_models = [new_name if fallback == old_name else fallback for fallback in defaults.fallback_models]
    if defaults.dream.model_override == old_name:
        defaults.dream.model_override = new_name
    for binding in config.subagent_roles.values():
        if binding.model_preset == old_name:
            binding.model_preset = new_name
    return True


def _model_call_order_state(config: Config) -> tuple[list[str], bool]:
    defaults = config.agents.defaults
    primary = defaults.model_preset
    if not primary or primary == "default" or primary not in config.model_presets:
        return [], False
    order = [primary]
    for fallback in defaults.fallback_models:
        if not isinstance(fallback, str):
            return [], False
        order.append(fallback)
    return order, True


def _legacy_model_configuration_migratable(config: Config, oauth_status: OAuthStatusReader) -> bool:
    _, editable = _model_call_order_state(config)
    if editable:
        return False
    defaults = config.agents.defaults
    if defaults.fallback_models:
        return True
    provider_name = defaults.provider
    if provider_name == "auto":
        model_prefix = defaults.model.split("/", 1)[0] if "/" in defaults.model else ""
        if model_prefix and resolve_provider(config, model_prefix) is not None:
            provider_name = model_prefix
        else:
            provider_name = config.get_provider_name(defaults.model, preset=config.resolve_default_preset()) or ""
    if not provider_name or provider_name == "auto":
        return False
    resolved = resolve_provider(config, provider_name)
    if resolved is None:
        return False
    spec, _, provider_config = resolved
    return provider_configured(spec, provider_config, oauth_status)


def _validate_configured_provider(config: Config, provider: str, oauth_status: OAuthStatusReader) -> None:
    if provider == "auto":
        return
    resolved = resolve_provider(config, provider)
    if resolved is None:
        raise ModelSettingsError("unknown provider")
    spec, _, provider_config = resolved
    if spec.is_transcription_only:
        raise ModelSettingsError("provider does not support chat models")
    if not provider_configured(spec, provider_config, oauth_status):
        raise ModelSettingsError("provider is not configured")


def create_model_configuration(config: Config, query: QueryParams, *, oauth_status: OAuthStatusReader = oauth_provider_status) -> str:
    raw_name = _query_first(query, "name")
    legacy_label = _query_first_alias(query, "label", "displayName")
    model = (_query_first(query, "model") or "").strip()
    provider = (_query_first(query, "provider") or "").strip()
    if not model:
        raise ModelSettingsError("model is required")
    if not provider:
        raise ModelSettingsError("provider is required")
    name = _model_configuration_name(raw_name) if raw_name is not None else _model_configuration_slug(legacy_label or "")
    if _model_configuration_name_exists(config, name):
        raise ModelSettingsError("configuration already exists", status=409)
    _validate_configured_provider(config, provider, oauth_status)
    activate_as_primary = not config.model_presets and not _legacy_model_configuration_migratable(config, oauth_status)
    base = config.resolve_preset()
    max_tokens = _parse_positive_int(_query_first_alias(query, "max_tokens", "maxTokens"), "max_tokens")
    context_window_tokens = _parse_positive_int(_query_first_alias(query, "context_window_tokens", "contextWindowTokens"), "context_window_tokens")
    temperature = _parse_temperature(_query_first(query, "temperature"))
    reasoning_effort = base.reasoning_effort
    supports_vision = base.supports_vision
    if _query_has_alias(query, "supports_vision", "supportsVision"):
        supports_vision = _parse_bool(_query_first_alias(query, "supports_vision", "supportsVision") or "", "supports_vision")
    if "reasoning_effort" in query or "reasoningEffort" in query:
        reasoning_effort = (_query_first_alias(query, "reasoning_effort", "reasoningEffort") or "").strip() or None
    config.model_presets[name] = ModelPresetConfig(
        model=model,
        provider=provider,
        max_tokens=max_tokens if max_tokens is not None else base.max_tokens,
        context_window_tokens=context_window_tokens if context_window_tokens is not None else base.context_window_tokens,
        temperature=temperature if temperature is not None else base.temperature,
        reasoning_effort=reasoning_effort,
        supports_vision=supports_vision,
    )
    if activate_as_primary:
        config.agents.defaults.model_preset = name
        config.agents.defaults.fallback_models = []
    return name


def update_model_configuration(config: Config, query: QueryParams, *, oauth_status: OAuthStatusReader = oauth_provider_status) -> bool:
    name = (_query_first(query, "name") or "").strip()
    if not name or name == "default":
        raise ModelSettingsError("model configuration is required")
    preset = config.model_presets.get(name)
    if preset is None:
        raise ModelSettingsError("unknown model configuration")
    changed = False
    new_name_value = _query_first_alias(query, "new_name", "newName")
    if new_name_value is not None:
        new_name = _model_configuration_name(new_name_value)
        changed = _rename_model_configuration(config, name, new_name) or changed
        name = new_name
        preset = config.model_presets[name]
    model = _query_first(query, "model")
    if model is not None:
        model = model.strip()
        if not model:
            raise ModelSettingsError("model is required")
        if preset.model != model:
            preset.model = model
            changed = True
    provider = _query_first(query, "provider")
    if provider is not None:
        provider = provider.strip()
        if not provider:
            raise ModelSettingsError("provider is required")
        _validate_configured_provider(config, provider, oauth_status)
        if preset.provider != provider:
            preset.provider = provider
            changed = True
    context_window_tokens = _parse_positive_int(_query_first_alias(query, "context_window_tokens", "contextWindowTokens"), "context_window_tokens")
    if context_window_tokens is not None and preset.context_window_tokens != context_window_tokens:
        preset.context_window_tokens = context_window_tokens
        changed = True
    max_tokens = _parse_positive_int(_query_first_alias(query, "max_tokens", "maxTokens"), "max_tokens")
    if max_tokens is not None and preset.max_tokens != max_tokens:
        preset.max_tokens = max_tokens
        changed = True
    temperature = _parse_temperature(_query_first(query, "temperature"))
    if temperature is not None and preset.temperature != temperature:
        preset.temperature = temperature
        changed = True
    if _query_has_alias(query, "supports_vision", "supportsVision"):
        supports_vision = _parse_bool(_query_first_alias(query, "supports_vision", "supportsVision") or "", "supports_vision")
        if preset.supports_vision != supports_vision:
            preset.supports_vision = supports_vision
            changed = True
    if "reasoning_effort" in query or "reasoningEffort" in query:
        reasoning_effort = (_query_first_alias(query, "reasoning_effort", "reasoningEffort") or "").strip() or None
        if preset.reasoning_effort != reasoning_effort:
            preset.reasoning_effort = reasoning_effort
            changed = True
    return changed


def delete_model_configuration(config: Config, query: QueryParams) -> None:
    name = (_query_first(query, "name") or "").strip()
    if not name or name == "default":
        raise ModelSettingsError("model configuration is required")
    if name not in config.model_presets:
        raise ModelSettingsError("unknown model configuration")
    bound_roles = [role for role, binding in config.subagent_roles.items() if binding.model_preset == name]
    if bound_roles:
        raise ModelSettingsError("Rebind or clear these subagent roles before deleting the preset: " + ", ".join(bound_roles), status=409)
    defaults = config.agents.defaults
    if defaults.model_preset == name or any(fallback == name for fallback in defaults.fallback_models):
        raise ModelSettingsError("remove the model preset from the call order first", status=409)
    if config.tools.image_analysis.model_preset == name:
        raise ModelSettingsError("clear the image analysis model preset before deleting it", status=409)
    del config.model_presets[name]


def update_subagent_roles(config: Config, query: QueryParams) -> None:
    raw = _query_first(query, "bindings")
    try:
        bindings = json.loads(raw) if raw is not None else None
    except (TypeError, json.JSONDecodeError):
        raise ModelSettingsError("bindings must be an object of role names to presets or null") from None
    if not isinstance(bindings, dict):
        raise ModelSettingsError("bindings must be an object of role names to presets or null")
    updates: dict[SubagentRoleName, SubagentRoleConfig] = {}
    for name, preset in cast(dict[str, str | None], bindings).items():
        if name not in SUBAGENT_ROLES:
            raise ModelSettingsError("unknown subagent role")
        if preset is not None and preset != "default" and preset not in config.model_presets:
            raise ModelSettingsError("unknown model preset in role bindings")
        current = config.subagent_roles.get(name, SubagentRoleConfig())
        values = current.model_dump()
        values["model_preset"] = preset
        if preset is not None:
            values["model"] = None
        updates[cast(SubagentRoleName, name)] = SubagentRoleConfig.model_validate(values)
    config.subagent_roles.update(updates)


def _custom_provider_key(config: Config, display_name: str) -> str:
    slug = _MODEL_CONFIGURATION_SLUG_RE.sub("-", display_name.strip().lower()).strip("-_")
    base = f"custom-{slug or 'provider'}"
    if len(base) > 56:
        base = base[:56].rstrip("-_")
    existing = {name.replace("_", "-").lower() for name, _ in _dynamic_provider_items(config)}
    candidate = base
    suffix = 2
    while candidate.replace("_", "-").lower() in existing or find_by_name(candidate):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _provider_display_name_exists(config: Config, display_name: str, *, exclude_key: str | None = None) -> bool:
    normalized = display_name.strip().casefold()
    if any(spec.label.strip().casefold() == normalized for spec in PROVIDERS):
        return True
    for provider_key, provider_config in _dynamic_provider_items(config):
        if provider_key == exclude_key:
            continue
        label = provider_config.display_name or provider_key.replace("-", " ").replace("_", " ").title()
        if label.strip().casefold() == normalized:
            return True
    return False


def create_provider_settings(config: Config, query: QueryParams) -> str:
    display_name = (_query_first_alias(query, "name", "displayName") or "").strip()
    if not display_name:
        raise ModelSettingsError("provider name is required")
    if len(display_name) > 80:
        raise ModelSettingsError("provider name must be 80 characters or fewer")
    updates = _provider_config_updates(query)
    allowed = {"api_key", "api_base", "proxy", "extra_headers", "extra_body", "extra_query", "thinking_style", "display_name"}
    unsupported = set(updates) - allowed
    if unsupported:
        raise ModelSettingsError(f"{sorted(unsupported)[0]} is not supported for a custom provider")
    if not str(updates.get("api_base") or ""):
        raise ModelSettingsError("API base is required")
    if _provider_display_name_exists(config, display_name):
        raise ModelSettingsError("provider already exists", status=409)
    provider_key = _custom_provider_key(config, display_name)
    updates["display_name"] = display_name
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
    if spec.is_oauth:
        if spec.name not in _OAUTH_PROXY_PROVIDERS:
            raise ModelSettingsError("unknown provider")
        unsupported = set(updates) - {"proxy", "extra_body"}
        if unsupported:
            raise ModelSettingsError("OAuth provider only supports proxy and extra_body settings")
    else:
        allowed = {"api_key", "api_base", *_provider_advanced_field_names(provider_key, spec)}
        if find_by_name(provider_key) is None:
            allowed.add("display_name")
        unsupported = set(updates) - allowed
        if unsupported:
            raise ModelSettingsError(f"{sorted(unsupported)[0]} is not supported for this provider")
    if "display_name" in updates:
        display_name = str(updates["display_name"] or "")
        if not display_name:
            raise ModelSettingsError("provider name is required")
        if len(display_name) > 80:
            raise ModelSettingsError("provider name must be 80 characters or fewer")
        if _provider_display_name_exists(config, display_name, exclude_key=provider_key):
            raise ModelSettingsError("provider already exists", status=409)
    updated_provider_config = _validated_provider_config(provider_config, updates)
    changed = updated_provider_config != provider_config
    if changed:
        setattr(config.providers, provider_key, updated_provider_config)
    image_config = config.tools.image_generation
    restart_required = changed and image_config.enabled and image_config.provider == provider_key and get_image_gen_provider(provider_key) is not None
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


def management_catalog(config: Config) -> dict[str, Any]:
    defaults = config.agents.defaults
    active_preset_name = defaults.model_preset or "default"
    presets = [{
        "name": "default", "label": "Default", "active": active_preset_name == "default",
        "is_default": True, "model": defaults.model, "provider": defaults.provider,
        "resolved_provider": config.get_provider_name(defaults.model, preset=config.resolve_default_preset()),
        "max_tokens": defaults.max_tokens, "context_window_tokens": defaults.context_window_tokens,
        "temperature": defaults.temperature, "reasoning_effort": defaults.reasoning_effort,
        "supports_vision": defaults.supports_vision,
        "reasoning_effort_values": reasoning_effort_values_for(
            config.get_provider_name(defaults.model, preset=config.resolve_default_preset()) or defaults.provider,
            defaults.model,
        ),
    }]
    for name, preset in config.model_presets.items():
        resolved_provider = config.get_provider_name(preset.model, preset=preset) or preset.provider
        presets.append({
            "name": name, "label": name, "active": active_preset_name == name, "is_default": False,
            "model": preset.model, "provider": preset.provider, "resolved_provider": resolved_provider,
            "max_tokens": preset.max_tokens, "context_window_tokens": preset.context_window_tokens,
            "temperature": preset.temperature, "reasoning_effort": preset.reasoning_effort,
            "supports_vision": preset.supports_vision,
            "reasoning_effort_values": reasoning_effort_values_for(resolved_provider, preset.model),
        })
    providers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec in PROVIDERS:
        if spec.settings_alias_for or spec.name in seen:
            continue
        provider_config = getattr(config.providers, spec.name, None)
        if isinstance(provider_config, ProviderConfig):
            providers.append({"name": spec.name, "configured": provider_configured(spec, provider_config)})
            seen.add(spec.name)
    for name, provider_config in _dynamic_provider_items(config):
        spec = create_dynamic_spec(name, display_name=provider_config.display_name or "", thinking_style=provider_config.thinking_style or "")
        providers.append({"name": name, "configured": provider_configured(spec, provider_config)})
    return {
        "status": "ok",
        "model_presets": presets,
        "image_analysis": {
            "enabled": config.tools.image_analysis.enabled,
            "model_preset": config.tools.image_analysis.model_preset,
            "max_image_mb": config.tools.image_analysis.max_image_mb,
            "max_images": config.tools.image_analysis.max_images,
        },
        "subagent_roles": [
            {"name": name, **metadata, "model_preset": config.subagent_roles.get(name, SubagentRoleConfig()).model_preset}
            for name, metadata in SUBAGENT_ROLES.items()
        ],
        "max_concurrent_subagents": defaults.max_concurrent_subagents,
        "providers": providers,
    }
