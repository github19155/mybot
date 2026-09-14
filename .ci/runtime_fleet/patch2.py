from pathlib import Path
import re


def edit(path, fn):
    p = Path(path)
    text = p.read_text()
    new = fn(text)
    if new == text:
        raise RuntimeError(f"no changes for {path}")
    p.write_text(new)


def once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError(f"expected one match, got {text.count(old)}: {old[:80]!r}")
    return text.replace(old, new, 1)


def fleet(text):
    text = once(text, "from loguru import logger\n", "from loguru import logger\n\nfrom nanobot.config.schema import ProviderConfig\nfrom nanobot.model_domain import get_model\nfrom nanobot.providers.registry import find_by_name\n")
    text = text.replace("preset_name", "model_id")
    text = text.replace('"preset": offering.model_id', '"model_id": offering.model_id')
    text = text.replace('"preset": item.model_id', '"model_id": item.model_id')
    start = text.index("def offering_from_config(\n")
    end = text.index("\n\ndef current_fleet_priority", start)
    replacement = '''def offering_from_config(\n    config: Any,\n    *,\n    model_id: str,\n) -> ModelOffering:\n    model_config = get_model(config.models, model_id)\n    provider_name = model_config.provider\n    spec = find_by_name(provider_name)\n    provider_cfg = (\n        getattr(config.providers, spec.name, None)\n        if spec is not None\n        else (config.providers.model_extra or {}).get(provider_name)\n    )\n    if not isinstance(provider_cfg, ProviderConfig):\n        provider_cfg = None\n    pricing = model_config.pricing\n    return ModelOffering(\n        offering_id=model_config.offering_id or f"{provider_name}:{model_config.model}",\n        provider=provider_name,\n        model=model_config.model,\n        model_id=model_id,\n        pools=tuple(model_config.pools),\n        input_cost_per_million=pricing.input,\n        output_cost_per_million=pricing.output,\n        cached_input_cost_per_million=pricing.cache_read,\n        max_concurrent_requests=model_config.max_concurrent_requests,\n        provider_max_concurrent_requests=(\n            provider_cfg.max_concurrent_requests if provider_cfg else None\n        ),\n        rate_limit_scope=(provider_cfg.rate_limit_scope if provider_cfg else "provider"),\n        supports_vision=model_config.capabilities.vision,\n        context_window_tokens=model_config.context_window_tokens,\n    )\n'''
    text = text[:start] + replacement + text[end:]
    return text


def runtime_events(text):
    return text.replace("model_preset", "model_id")


def loop(text):
    text = text.replace("from nanobot.agent import model_presets as preset_helpers\n", "")
    text = text.replace("from nanobot.config.schema import AgentDefaults, Config, ModelPresetConfig", "from nanobot.config.schema import AgentDefaults, Config\nfrom nanobot.model_domain import ModelConfig")
    text = text.replace("SESSION_MODEL_PRESET_METADATA_KEY", "SESSION_MODEL_ID_METADATA_KEY")
    text = text.replace("model_preset_from_metadata", "model_id_from_metadata")
    text = text.replace("model_presets", "models")
    text = text.replace("ModelPresetConfig", "ModelConfig")
    text = text.replace("preset_catalog_loader", "model_catalog_loader")
    text = text.replace("PresetCatalogLoader", "ModelCatalogLoader")
    text = text.replace("model_preset", "model_id")
    text = text.replace("resolve_preset", "resolve_model")
    text = text.replace("select_preset", "select_model")
    text = text.replace("set_model_id(name)", "set_model_id(name)")
    text = text.replace("configured_presets = models or {}", "configured_models = models or {}")
    text = text.replace("models=configured_presets,", "models=configured_models,")
    text = text.replace("models=preset_helpers.configured_models(config),", "models=config.models,")
    text = text.replace("model_catalog_loader=preset_helpers.ModelCatalogLoader", "model_catalog_loader=None")
    # from_config must not accept raw upstream model selection.
    old = '''        model_override = extra.pop("model", None)\n        preset_override = extra.pop("model_id", None)\n        context_window_override = extra.pop("context_window_tokens", None)\n        if model_override is not None and preset_override is not None:\n            raise ValueError("model and model_id are mutually exclusive")\n'''
    new = '''        model_id_override = extra.pop("model_id", None)\n        if "model" in extra:\n            raise ValueError("raw model overrides are not supported; use model_id")\n        context_window_override = extra.pop("context_window_tokens", None)\n'''
    if old in text:
        text = text.replace(old, new, 1)
    text = text.replace("model=model_override if explicit_provider is not None else None,", "model=None,")
    text = text.replace("        if model_override is not None and explicit_provider is None:\n            loop.runtime_resolver.select_model(model_override)\n        elif preset_override is not None:\n            loop.set_model_id(preset_override, publish_update=False)\n", "        if model_id_override is not None:\n            loop.set_model_id(model_id_override, publish_update=False)\n")
    text = text.replace("runtime.model_id or \"default\"", "runtime.model_id or \"unbound\"")
    text = text.replace("preset={}", "model_id={}")
    return text


def runtime_control(text):
    text = text.replace("ModelPresetConfig", "ModelConfig")
    text = text.replace("model_presets", "models")
    text = text.replace("model_preset", "model_id")
    text = text.replace("set_model(self, model: str)", "set_model_id(self, model_id: str)")
    text = text.replace("set_runtime_model(model)", "set_model_id(model_id)")
    text = text.replace("set_model_id(self, name: str, *, session_key", "set_model_id(self, name: str, *, session_key")
    text = text.replace("set_session_model_id", "set_session_model_id")
    text = text.replace("_snapshot_models(target.models)", "_snapshot_models(target.models)")
    text = text.replace("def _snapshot_model_ids", "def _snapshot_models")
    text = text.replace("def _snapshot_models(presets: Mapping[str, ModelConfig])", "def _snapshot_models(models: Mapping[str, ModelConfig])")
    text = text.replace("for name, preset in presets.items()", "for name, preset in models.items()")
    text = text.replace('"max_tokens": preset.max_tokens,', '"max_tokens": preset.generation_defaults.max_tokens,')
    text = text.replace('"temperature": preset.temperature,', '"temperature": preset.generation_defaults.temperature,')
    text = text.replace('"reasoning_effort": preset.reasoning_effort,', '"reasoning_effort": preset.generation_defaults.reasoning_effort,')
    return text


def self_tool(text):
    text = text.replace("model_presets", "models").replace("model_preset", "model_id")
    text = text.replace('"model":                 {"type": str, "min_len": 1},\n', '"model_id":              {"type": str, "min_len": 1},\n')
    text = text.replace('"model",\n        "model_id",', '"model",\n        "model_id",')
    text = text.replace("def _modify_model_id", "def _modify_model_id")
    text = text.replace("set_model(cast(str, value))", "set_model_id(cast(str, value))")
    text = text.replace("if key == \"model\":", "if key == \"model_id\":")
    text = text.replace("direct 'model' changes", "direct 'model' changes")
    return text

edit("nanobot/model_fleet.py", fleet)
edit("nanobot/bus/runtime_events.py", runtime_events)
edit("nanobot/agent/loop.py", loop)
edit("nanobot/agent/tools/runtime_control.py", runtime_control)
edit("nanobot/agent/tools/self.py", self_tool)
