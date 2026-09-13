"""Create LLM providers from config."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nanobot.config.schema import Config, InlineFallbackConfig, ModelPresetConfig, ProviderConfig
from nanobot.model_fleet import get_model_fleet, offering_from_config
from nanobot.providers.base import GenerationSettings, LLMProvider
from nanobot.providers.fallback_provider import FallbackProvider
from nanobot.providers.fleet_controlled_provider import FleetControlledProvider
from nanobot.providers.registry import ProviderSpec, create_dynamic_spec, find_by_name


@dataclass(frozen=True)
class ProviderSnapshot:
    provider: LLMProvider
    model: str
    context_window_tokens: int
    signature: tuple[object, ...]
    generation: GenerationSettings | None = None
    model_preset: str | None = None
    system_prompt_prefix: str | None = None
    supports_vision: bool = False


@dataclass(frozen=True)
class _ProviderSetup:
    model: str
    provider_name: str
    provider_config: ProviderConfig | None
    spec: ProviderSpec | None
    backend: str


def _resolve_model_preset(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
) -> ModelPresetConfig:
    return preset if preset is not None else config.resolve_preset(preset_name)


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
    """Use a neutral client User-Agent for OpenAI-compatible endpoints."""
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


def _resolve_provider_setup(
    config: Config,
    *,
    preset: ModelPresetConfig,
    model: str | None = None,
) -> _ProviderSetup:
    """Resolve and validate provider configuration without constructing a client."""
    model = model or preset.model
    provider_name = config.get_provider_name(model, preset=preset)
    p = config.get_provider(model, preset=preset)
    if not provider_name:
        raise ValueError(f"No provider is configured for model '{model}'.")
    spec = _provider_spec_for_config(provider_name, p)
    if not spec and p:
        if not p.api_base:
            raise ValueError(f"Provider '{provider_name}' requires api_base in config.")
        spec = create_dynamic_spec(
            provider_name,
            display_name=(p.display_name or "") if p else "",
            thinking_style=(p.thinking_style or "") if p else "",
        )
    if spec and spec.is_transcription_only:
        raise ValueError(f"Provider '{provider_name}' only supports transcription.")
    backend = spec.backend if spec else "openai_compat"
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
        and spec
        and spec.is_direct
        and not spec.default_api_base
        and not (p and p.api_base)
    ):
        raise ValueError(f"Provider '{provider_name}' requires api_base in config.")
    elif backend in {"anthropic", "openai_compat"} and not (
        backend == "openai_compat" and model.startswith("bedrock/")
    ):
        needs_key = not (p and p.api_key)
        exempt = spec and (spec.is_oauth or spec.is_local or spec.is_direct)
        if needs_key and not exempt:
            raise ValueError(f"No API key configured for provider '{provider_name}'.")

    return _ProviderSetup(
        model=model,
        provider_name=provider_name,
        provider_config=p,
        spec=spec,
        backend=backend,
    )


def validate_provider_setup(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
    model: str | None = None,
) -> None:
    """Validate local provider/model settings without loading a provider client."""
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    _resolve_provider_setup(config, preset=resolved, model=model)


def _make_provider_core(
    config: Config,
    *,
    preset: ModelPresetConfig,
    model: str | None = None,
    preset_name: str | None = None,
) -> LLMProvider:
    """Create one physical provider leaf and bind it to its fleet offering."""
    setup = _resolve_provider_setup(config, preset=preset, model=model)
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
            api_base=config.get_api_base(model, preset=preset),
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
            api_base=config.get_api_base(model, preset=preset),
            default_model=model,
            extra_headers=_openai_compat_extra_headers(spec, p),
            spec=spec,
            extra_body=p.extra_body if p else None,
            api_type=p.api_type if p and provider_name == "openai" else "auto",
            extra_query=p.extra_query if p else None,
            proxy=p.proxy if p else None,
            provider_name=provider_name,
        )

    provider.generation = preset.to_generation_settings()
    fleet_cfg = getattr(config, "model_fleet", None)
    if bool(getattr(fleet_cfg, "enabled", True)):
        fleet = get_model_fleet(config)
        provider = FleetControlledProvider(
            provider,
            fleet=fleet,
            offering=offering_from_config(
                config,
                preset=preset,
                preset_name=preset_name,
                provider_name=provider_name,
            ),
        )
    return provider


def _inline_fallback_preset(
    primary: ModelPresetConfig,
    fallback: InlineFallbackConfig,
) -> ModelPresetConfig:
    return ModelPresetConfig(
        model=fallback.model,
        provider=fallback.provider,
        max_tokens=fallback.max_tokens if fallback.max_tokens is not None else primary.max_tokens,
        context_window_tokens=(fallback.context_window_tokens if fallback.context_window_tokens is not None else primary.context_window_tokens),
        temperature=(fallback.temperature if fallback.temperature is not None else primary.temperature),
        reasoning_effort=fallback.reasoning_effort,
        supports_vision=fallback.supports_vision,
    )


def _resolve_fallback_presets(
    config: Config,
    primary: ModelPresetConfig,
    *,
    include_fallbacks: bool = True,
) -> list[ModelPresetConfig]:
    if not include_fallbacks:
        return []
    presets: list[ModelPresetConfig] = []
    for fallback in config.agents.defaults.fallback_models:
        if isinstance(fallback, str):
            presets.append(config.model_presets[fallback])
        else:
            presets.append(_inline_fallback_preset(primary, fallback))
    return presets


def make_provider(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
    model: str | None = None,
    include_fallbacks: bool = True,
) -> LLMProvider:
    """Create the LLM provider implied by config, with fleet-controlled leaves."""
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    provider = _make_provider_core(
        config,
        preset=resolved,
        model=model,
        preset_name=preset_name,
    )
    fallback_presets = _resolve_fallback_presets(
        config,
        resolved,
        include_fallbacks=include_fallbacks,
    )

    if fallback_presets:
        provider = FallbackProvider(
            primary=provider,
            fallback_presets=fallback_presets,
            provider_factory=lambda fb: _make_provider_core(config, preset=fb),
            primary_context_window_tokens=resolved.context_window_tokens,
            primary_system_prompt_prefix=config.system_prompt_for(resolved.model),
            fallback_system_prompt_prefixes=[config.system_prompt_for(fb.model) for fb in fallback_presets],
        )
    return provider


def build_unconfigured_provider_snapshot(config: Config, setup_error: str) -> ProviderSnapshot:
    """Build a non-networking runtime so the WebUI can collect first-time setup."""
    from nanobot.providers.unconfigured_provider import UnconfiguredProvider

    preset = config.resolve_preset()
    provider = UnconfiguredProvider(preset.model)
    provider.generation = preset.to_generation_settings()
    return ProviderSnapshot(
        provider=provider,
        model=preset.model,
        context_window_tokens=preset.context_window_tokens,
        signature=("unconfigured", setup_error, preset.model),
        generation=provider.generation,
        supports_vision=preset.supports_vision,
    )


def _fleet_signature(
    config: Config,
    preset: ModelPresetConfig,
    provider_config: ProviderConfig | None,
) -> tuple[object, ...]:
    """Safe non-secret Fleet fields that affect a frozen provider leaf wrapper."""
    return (
        bool(config.model_fleet.enabled),
        preset.offering_id,
        tuple(preset.fleet_pools),
        preset.input_cost_per_million,
        preset.output_cost_per_million,
        preset.cached_input_cost_per_million,
        preset.max_concurrent_requests,
        provider_config.max_concurrent_requests if provider_config else None,
        provider_config.rate_limit_scope if provider_config else "provider",
    )


def provider_signature(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
    include_fallbacks: bool = True,
) -> tuple[object, ...]:
    """Return config fields that affect the active provider chain.

    The signature is only cache invalidation state. It can contain connection
    details from the existing provider contract and must never be logged or used
    as a Model Fleet identity. Fleet itself keys only safe provider/model data.
    """
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    p = config.get_provider(resolved.model, preset=resolved)
    fallback_presets = _resolve_fallback_presets(
        config,
        resolved,
        include_fallbacks=include_fallbacks,
    )

    def _fallback_signature(fallback: ModelPresetConfig) -> tuple[object, ...]:
        fp = config.get_provider(fallback.model, preset=fallback)
        provider_name = config.get_provider_name(fallback.model, preset=fallback)
        return (
            fallback.model,
            fallback.provider,
            provider_name,
            config.get_api_key(fallback.model, preset=fallback),
            config.get_api_base(fallback.model, preset=fallback),
            _provider_extra_headers(find_by_name(provider_name) if provider_name else None, fp),
            fp.extra_body if fp else None,
            fp.api_type if fp else "auto",
            fp.extra_query if fp else None,
            getattr(fp, "region", None) if fp else None,
            getattr(fp, "profile", None) if fp else None,
            fallback.max_tokens,
            fallback.temperature,
            fallback.reasoning_effort,
            fallback.context_window_tokens,
            fallback.supports_vision,
            config.system_prompt_for(fallback.model),
            getattr(fp, "proxy", None) if fp else None,
            fp.thinking_style if fp else None,
            _fleet_signature(config, fallback, fp),
        )

    provider_name = config.get_provider_name(resolved.model, preset=resolved)
    return (
        resolved.model,
        resolved.provider,
        provider_name,
        config.get_api_key(resolved.model, preset=resolved),
        config.get_api_base(resolved.model, preset=resolved),
        _provider_extra_headers(find_by_name(provider_name) if provider_name else None, p),
        p.extra_body if p else None,
        p.api_type if p else "auto",
        p.extra_query if p else None,
        getattr(p, "region", None) if p else None,
        getattr(p, "profile", None) if p else None,
        resolved.max_tokens,
        resolved.temperature,
        resolved.reasoning_effort,
        resolved.context_window_tokens,
        resolved.supports_vision,
        config.system_prompt_for(resolved.model),
        getattr(p, "proxy", None) if p else None,
        p.thinking_style if p else None,
        _fleet_signature(config, resolved, p),
        tuple(_fallback_signature(fallback) for fallback in fallback_presets),
    )


def build_provider_snapshot(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
    include_fallbacks: bool = True,
) -> ProviderSnapshot:
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    selected_preset = config.agents.defaults.model_preset if preset_name is None and preset is None else preset_name
    fallback_windows = [
        fallback.context_window_tokens
        for fallback in _resolve_fallback_presets(
            config,
            resolved,
            include_fallbacks=include_fallbacks,
        )
    ]
    return ProviderSnapshot(
        provider=make_provider(
            config,
            preset=resolved,
            preset_name=selected_preset,
            include_fallbacks=include_fallbacks,
        ),
        model=resolved.model,
        context_window_tokens=min([resolved.context_window_tokens, *fallback_windows]),
        signature=provider_signature(
            config,
            preset=resolved,
            include_fallbacks=include_fallbacks,
        ),
        generation=resolved.to_generation_settings(),
        model_preset=selected_preset,
        supports_vision=resolved.supports_vision,
        system_prompt_prefix=config.system_prompt_for(resolved.model),
    )


def load_provider_snapshot(
    config_path: Path | None = None,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
    include_fallbacks: bool = True,
) -> ProviderSnapshot:
    from nanobot.config.loader import load_config, resolve_config_env_vars

    return build_provider_snapshot(
        resolve_config_env_vars(load_config(config_path), config_path=config_path),
        preset_name=preset_name,
        preset=preset,
        include_fallbacks=include_fallbacks,
    )
