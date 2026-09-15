"""Create LLM providers from canonical model configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nanobot.config.schema import Config, ProviderConfig
from nanobot.model_domain import ModelConfig, require_model_capability
from nanobot.model_fleet import get_model_fleet, offering_from_config
from nanobot.providers.base import GenerationSettings, LLMProvider
from nanobot.providers.fleet_controlled_provider import FleetControlledProvider
from nanobot.providers.registry import ProviderSpec, create_dynamic_spec, find_by_name


@dataclass(frozen=True)
class ProviderSnapshot:
    model_id: str
    provider: LLMProvider
    model: str
    context_window_tokens: int
    signature: tuple[object, ...]
    generation: GenerationSettings | None = None
    system_prompt_prefix: str | None = None
    supports_vision: bool = False


@dataclass(frozen=True)
class _ProviderSetup:
    model_id: str
    model_config: ModelConfig
    model: str
    provider_name: str
    provider_config: ProviderConfig | None
    spec: ProviderSpec | None
    backend: str


def _resolve_model(config: Config, model_id: str | None) -> tuple[str, ModelConfig]:
    selected = config.agents.defaults.model_id if model_id is None else model_id
    return selected, require_model_capability(config.models, selected, "text")


def _provider_config_for_name(config: Config, provider_name: str) -> ProviderConfig | None:
    spec = find_by_name(provider_name)
    if spec is not None:
        candidate = getattr(config.providers, spec.name, None)
        if isinstance(candidate, ProviderConfig):
            return candidate
    normalized = provider_name.replace("-", "_").lower()
    for name, candidate in (config.providers.model_extra or {}).items():
        if (
            isinstance(candidate, ProviderConfig)
            and name.replace("-", "_").lower() == normalized
        ):
            return candidate
    return None


def _provider_extra_headers(
    spec: ProviderSpec | None,
    provider_config: ProviderConfig | None,
) -> dict[str, str] | None:
    headers = dict(spec.default_extra_headers) if spec else {}
    if provider_config and provider_config.extra_headers:
        headers.update(provider_config.extra_headers)
    return headers or None


def _openai_compat_extra_headers(
    spec: ProviderSpec | None,
    provider_config: ProviderConfig | None,
) -> dict[str, str]:
    headers = {"User-Agent": "nanobot"}
    configured = _provider_extra_headers(spec, provider_config)
    if configured:
        headers.update(configured)
    return headers


def _provider_spec_for_config(
    provider_name: str,
    provider_config: ProviderConfig | None,
) -> ProviderSpec | None:
    spec = find_by_name(provider_name)
    if (
        spec is not None
        and spec.name == "orcarouter"
        and provider_config is not None
        and provider_config.api_base
        and provider_config.api_base.rstrip("/").lower()
        != spec.default_api_base.rstrip("/").lower()
    ):
        return create_dynamic_spec(
            provider_name,
            display_name=provider_config.display_name or "",
            thinking_style=provider_config.thinking_style or "",
        )
    return spec


def _api_base(spec: ProviderSpec | None, provider_config: ProviderConfig | None) -> str | None:
    if provider_config and provider_config.api_base:
        return provider_config.api_base
    if spec and spec.default_api_base:
        return spec.default_api_base
    return None


def _generation_settings(model_config: ModelConfig) -> GenerationSettings:
    defaults = model_config.generation_defaults
    return GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        reasoning_effort=defaults.reasoning_effort,
    )


def _system_prompt_for(config: Config, model_id: str) -> str | None:
    for override in config.system_prompt_overrides:
        if model_id in override.model_ids:
            return override.prompt
    return None


def _resolve_provider_setup(config: Config, *, model_id: str | None = None) -> _ProviderSetup:
    """Resolve provider configuration from canonical identity only."""
    selected_id, model_config = _resolve_model(config, model_id)
    provider_name = model_config.provider
    p = _provider_config_for_name(config, provider_name)
    spec = _provider_spec_for_config(provider_name, p)
    if spec is None:
        if p is None:
            raise ValueError(f"Provider {provider_name!r} is not configured.")
        if not p.api_base:
            raise ValueError(f"Provider {provider_name!r} requires api_base in config.")
        spec = create_dynamic_spec(
            provider_name,
            display_name=p.display_name or "",
            thinking_style=p.thinking_style or "",
        )
    if spec.is_transcription_only:
        raise ValueError(f"Provider {provider_name!r} only supports transcription.")
    backend = spec.backend
    if p and p.proxy and backend not in {"openai_compat", "openai_codex", "xai_grok"}:
        raise ValueError(
            f"providers.{provider_name}.proxy is only supported for "
            "OpenAI-compatible providers, OpenAI Codex, and xAI Grok."
        )
    if backend == "azure_openai":
        if not p or not p.api_base:
            raise ValueError("Azure OpenAI requires api_base in config.")
    elif (
        backend == "openai_compat"
        and spec.is_direct
        and not spec.default_api_base
        and not (p and p.api_base)
    ):
        raise ValueError(f"Provider {provider_name!r} requires api_base in config.")
    elif backend in {"anthropic", "openai_compat"}:
        needs_key = not (p and p.api_key)
        exempt = spec.is_oauth or spec.is_local or spec.is_direct
        if needs_key and not exempt:
            raise ValueError(f"No API key configured for provider {provider_name!r}.")
    return _ProviderSetup(
        model_id=selected_id,
        model_config=model_config,
        model=model_config.model,
        provider_name=provider_name,
        provider_config=p,
        spec=spec,
        backend=backend,
    )


def validate_provider_setup(config: Config, *, model_id: str | None = None) -> None:
    _resolve_provider_setup(config, model_id=model_id)


def _make_provider_core(config: Config, *, setup: _ProviderSetup) -> LLMProvider:
    model = setup.model
    provider_name = setup.provider_name
    p = setup.provider_config
    spec = setup.spec
    backend = setup.backend
    if backend == "openai_codex":
        from nanobot.providers.openai_codex_provider import OpenAICodexProvider
        provider = OpenAICodexProvider(
            default_model=model,
            proxy=getattr(p, "proxy", None) if p else None,
            extra_body=p.extra_body if p else None,
            provider_name=provider_name,
        )
    elif backend == "xai_grok":
        from nanobot.providers.xai_grok_provider import XAIGrokProvider
        provider = XAIGrokProvider(
            default_model=model,
            proxy=getattr(p, "proxy", None) if p else None,
            extra_body=p.extra_body if p else None,
            provider_name=provider_name,
        )
    elif backend == "azure_openai":
        from nanobot.providers.azure_openai_provider import AzureOpenAIProvider
        if p is None or p.api_base is None:
            raise RuntimeError("validated Azure provider setup is missing api_base")
        provider = AzureOpenAIProvider(
            api_key=p.api_key or "",
            api_base=p.api_base,
            default_model=model,
            provider_name=provider_name,
        )
    elif backend == "github_copilot":
        from nanobot.providers.github_copilot_provider import GitHubCopilotProvider
        provider = GitHubCopilotProvider(default_model=model, provider_name=provider_name)
    elif backend == "anthropic":
        from nanobot.providers.anthropic_provider import AnthropicProvider
        provider = AnthropicProvider(
            api_key=p.api_key if p else None,
            api_base=_api_base(spec, p),
            default_model=model,
            extra_headers=_provider_extra_headers(spec, p),
            provider_name=provider_name,
        )
    elif backend == "bedrock":
        from nanobot.providers.bedrock_provider import BedrockProvider
        provider = BedrockProvider(
            api_key=p.api_key if p else None,
            api_base=p.api_base if p else None,
            default_model=model,
            region=getattr(p, "region", None) if p else None,
            profile=getattr(p, "profile", None) if p else None,
            extra_body=p.extra_body if p else None,
            provider_name=provider_name,
        )
    else:
        from nanobot.providers.openai_compat_provider import OpenAICompatProvider
        provider = OpenAICompatProvider(
            api_key=p.api_key if p else None,
            api_base=_api_base(spec, p),
            default_model=model,
            extra_headers=_openai_compat_extra_headers(spec, p),
            spec=spec,
            extra_body=p.extra_body if p else None,
            api_type=p.api_type if p and provider_name == "openai" else "auto",
            extra_query=p.extra_query if p else None,
            proxy=p.proxy if p else None,
            provider_name=provider_name,
        )
    provider.generation = _generation_settings(setup.model_config)
    if config.model_fleet.enabled:
        provider = FleetControlledProvider(
            provider,
            fleet=get_model_fleet(config),
            offering=offering_from_config(config, model_id=setup.model_id),
        )
    return provider


def make_provider(config: Config, *, model_id: str | None = None) -> LLMProvider:
    """Create the provider selected by a canonical model ID."""
    return _make_provider_core(config, setup=_resolve_provider_setup(config, model_id=model_id))


def build_unconfigured_provider_snapshot(config: Config, setup_error: str) -> ProviderSnapshot:
    """Build a non-networking runtime so first-time setup can still render."""
    from nanobot.providers.unconfigured_provider import UnconfiguredProvider

    model_id, model_config = _resolve_model(config, None)
    provider = UnconfiguredProvider(model_config.model)
    provider.generation = _generation_settings(model_config)
    return ProviderSnapshot(
        model_id=model_id,
        provider=provider,
        model=model_config.model,
        context_window_tokens=model_config.context_window_tokens,
        signature=("unconfigured", setup_error, model_id, model_config.model),
        generation=provider.generation,
        system_prompt_prefix=_system_prompt_for(config, model_id),
        supports_vision=model_config.capabilities.vision,
    )


def _fleet_signature(
    config: Config,
    model_config: ModelConfig,
    provider_config: ProviderConfig | None,
) -> tuple[object, ...]:
    pricing = model_config.pricing
    return (
        config.model_fleet.enabled,
        model_config.offering_id,
        tuple(model_config.pools),
        pricing.input,
        pricing.output,
        pricing.cache_read,
        model_config.max_concurrent_requests,
        provider_config.max_concurrent_requests if provider_config else None,
        provider_config.rate_limit_scope if provider_config else "provider",
    )


def provider_signature(config: Config, *, model_id: str | None = None) -> tuple[object, ...]:
    setup = _resolve_provider_setup(config, model_id=model_id)
    p = setup.provider_config
    model_config = setup.model_config
    return (
        setup.model_id,
        setup.model,
        setup.provider_name,
        p.api_key if p else None,
        _api_base(setup.spec, p),
        _provider_extra_headers(setup.spec, p),
        p.extra_body if p else None,
        p.api_type if p else "auto",
        p.extra_query if p else None,
        getattr(p, "region", None) if p else None,
        getattr(p, "profile", None) if p else None,
        model_config.generation_defaults.max_tokens,
        model_config.generation_defaults.temperature,
        model_config.generation_defaults.reasoning_effort,
        model_config.context_window_tokens,
        model_config.capabilities.vision,
        _system_prompt_for(config, setup.model_id),
        getattr(p, "proxy", None) if p else None,
        p.thinking_style if p else None,
        _fleet_signature(config, model_config, p),
    )


def build_provider_snapshot(config: Config, *, model_id: str | None = None) -> ProviderSnapshot:
    selected_id, model_config = _resolve_model(config, model_id)
    return ProviderSnapshot(
        model_id=selected_id,
        provider=make_provider(config, model_id=selected_id),
        model=model_config.model,
        context_window_tokens=model_config.context_window_tokens,
        signature=provider_signature(config, model_id=selected_id),
        generation=_generation_settings(model_config),
        system_prompt_prefix=_system_prompt_for(config, selected_id),
        supports_vision=model_config.capabilities.vision,
    )


def load_provider_snapshot(
    config_path: Path | None = None,
    *,
    model_id: str | None = None,
) -> ProviderSnapshot:
    from nanobot.config.loader import load_config, resolve_config_env_vars

    config = resolve_config_env_vars(load_config(config_path), config_path=config_path)
    return build_provider_snapshot(config, model_id=model_id)
