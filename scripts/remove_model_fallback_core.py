from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def remove_span(text: str, start: str, end: str) -> str:
    a = text.index(start)
    b = text.index(end, a)
    return text[:a] + text[b:]


# Config schema: remove request-time model fallback configuration.
p = "nanobot/config/schema.py"
s = read(p)
s = remove_span(s, "class InlineFallbackConfig(Base):", "class FleetRetentionConfig(Base):")
s = s.replace("    fallback_models: list[FallbackCandidate] = Field(default_factory=list)\n", "")
s = s.replace(
    "        for fallback in self.agents.defaults.fallback_models:\n"
    "            if isinstance(fallback, str) and fallback not in self.model_presets:\n"
    "                raise ValueError(f\"fallback_models entry {fallback!r} not found in model_presets\")\n",
    "",
)
write(p, s)

# Provider factory: one selected preset -> one provider leaf (Fleet wrapper may remain).
p = "nanobot/providers/factory.py"
s = read(p)
s = s.replace(
    "from nanobot.config.schema import Config, InlineFallbackConfig, ModelPresetConfig, ProviderConfig\n",
    "from nanobot.config.schema import Config, ModelPresetConfig, ProviderConfig\n",
)
s = s.replace("from nanobot.providers.fallback_provider import FallbackProvider\n", "")
start = s.index("def _inline_fallback_preset(")
end = s.index("def build_unconfigured_provider_snapshot(", start)
make_provider = '''def make_provider(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
    model: str | None = None,
) -> LLMProvider:
    """Create the single provider selected by the resolved model runtime."""
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    return _make_provider_core(
        config,
        preset=resolved,
        model=model,
        preset_name=preset_name,
    )


'''
s = s[:start] + make_provider + s[end:]
start = s.index("def provider_signature(")
end = s.index("def build_provider_snapshot(", start)
provider_signature = '''def provider_signature(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
) -> tuple[object, ...]:
    """Return config fields that affect the selected provider runtime."""
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    p = config.get_provider(resolved.model, preset=resolved)
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
    )


'''
s = s[:start] + provider_signature + s[end:]
start = s.index("def build_provider_snapshot(")
end = s.index("def load_provider_snapshot(", start)
build_snapshot = '''def build_provider_snapshot(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
) -> ProviderSnapshot:
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    selected_preset = (
        config.agents.defaults.model_preset
        if preset_name is None and preset is None
        else preset_name
    )
    return ProviderSnapshot(
        provider=make_provider(
            config,
            preset=resolved,
            preset_name=selected_preset,
        ),
        model=resolved.model,
        context_window_tokens=resolved.context_window_tokens,
        signature=provider_signature(config, preset=resolved),
        generation=resolved.to_generation_settings(),
        model_preset=selected_preset,
        supports_vision=resolved.supports_vision,
        system_prompt_prefix=config.system_prompt_for(resolved.model),
    )


'''
s = s[:start] + build_snapshot + s[end:]
start = s.index("def load_provider_snapshot(")
load_snapshot = '''def load_provider_snapshot(
    config_path: Path | None = None,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
) -> ProviderSnapshot:
    from nanobot.config.loader import load_config, resolve_config_env_vars

    return build_provider_snapshot(
        resolve_config_env_vars(load_config(config_path), config_path=config_path),
        preset_name=preset_name,
        preset=preset,
    )
'''
s = s[:start] + load_snapshot
write(p, s)

# Runtime resolver: fallback policy disappears from selection API/cache identity.
p = "nanobot/agent/model_runtime.py"
s = read(p)
s = s.replace(
    "self._resolved_presets: dict[tuple[str, bool], LLMRuntime] = {}",
    "self._resolved_presets: dict[str, LLMRuntime] = {}",
)
s = s.replace("        include_fallbacks: bool = True,\n", "")
s = s.replace("                include_fallbacks=include_fallbacks,\n", "")
s = s.replace("            include_fallbacks=include_fallbacks,\n", "")
s = s.replace("        cache_key = (normalized, include_fallbacks)\n", "        cache_key = normalized\n")
s = s.replace(
    "        snapshot = self._provider_snapshot_loader(include_fallbacks=True)\n",
    "        snapshot = self._provider_snapshot_loader()\n",
)
s = s.replace("                include_fallbacks=True,\n", "")
write(p, s)

# Agent loop snapshot construction.
p = "nanobot/agent/loop.py"
s = read(p)
s = s.replace("provider_snapshot_loader(include_fallbacks=True)", "provider_snapshot_loader()")
s = s.replace("build_provider_snapshot(config, include_fallbacks=True)", "build_provider_snapshot(config)")
write(p, s)

# Dream continues choosing a preset before the request; no runtime fallback flag exists.
p = "nanobot/agent/model_management.py"
s = read(p)
s = s.replace("            include_fallbacks=False,\n", "")
s = s.replace(
    '                elif action in {"model_create", "model_update"}:\n'
    "                    selected = config.agents.defaults.model_preset\n"
    "                    fallbacks = list(config.agents.defaults.fallback_models)\n"
    "                    cast(Any, operation)(config, query)\n"
    '                    if action == "model_create":\n'
    "                        config.agents.defaults.model_preset = selected\n"
    "                        config.agents.defaults.fallback_models = fallbacks\n",
    '                elif action in {"model_create", "model_update"}:\n'
    "                    selected = config.agents.defaults.model_preset\n"
    "                    cast(Any, operation)(config, query)\n"
    '                    if action == "model_create":\n'
    "                        config.agents.defaults.model_preset = selected\n",
)
s = s.replace(
    "            self.config.agents.defaults.fallback_models = updated.agents.defaults.fallback_models\n",
    "",
)
write(p, s)

# Gateway: remove transparent fallback observer wiring; usage observer stays.
p = "nanobot/cli/gateway_runtime.py"
s = read(p)
s = s.replace("    from nanobot.providers.fallback_provider import FallbackProvider\n", "")
s = s.replace("        build_webui_fallback_model_observer,\n", "")
s = s.replace("    fallback_model_observer = build_webui_fallback_model_observer(bus)\n\n", "")
s = s.replace(
    "        if isinstance(snapshot.provider, FallbackProvider):\n"
    "            snapshot.provider.set_fallback_model_observer(fallback_model_observer)\n",
    "",
)
write(p, s)

# CLI wizard: remove fallback model field management.
p = "nanobot/cli/onboard.py"
s = read(p)
s = remove_span(s, "def _handle_fallback_models_field(", "def _handle_search_provider_field(")
s = s.replace('    "fallback_models": _handle_fallback_models_field,\n', "")
write(p, s)

# Fleet wrapper docs no longer imply it sits under FallbackProvider.
p = "nanobot/providers/fleet_controlled_provider.py"
s = read(p)
s = s.replace(
    "    the base retry policy sleeps. FallbackProvider can therefore wrap these\n"
    "    leaves and each fallback route keeps its own fleet identity.\n",
    "    the base provider retry policy sleeps. Runtime selection remains outside\n"
    "    this wrapper, so one request stays on one selected provider/model route.\n",
)
write(p, s)
