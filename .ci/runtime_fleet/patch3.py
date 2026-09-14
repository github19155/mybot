from pathlib import Path


def edit(path, pairs):
    p = Path(path)
    text = p.read_text()
    for old, new in pairs:
        if old not in text:
            raise RuntimeError(f"{path}: missing {old[:100]!r}")
        text = text.replace(old, new)
    p.write_text(text)


edit("nanobot/agent/loop.py", [
    ("model_catalog_loader: preset_helpers.ModelCatalogLoader | None = None,", "model_catalog_loader: Callable[[], Mapping[str, ModelConfig]] | None = None,"),
    ("else defaults.context_window_tokens", "else 200_000"),
    ("Session '{}' references removed model preset '{}'; falling back to default", "Session '{}' references removed model_id '{}'; falling back to default"),
    ('"""Validate and persist one session\'s preset selection."""', '"""Validate and persist one session\'s canonical model selection."""'),
    ('name: str | None,\n        *,\n        publish_update: bool = True,', 'name: str,\n        *,\n        publish_update: bool = True,'),
    ('"""Select a named default runtime for future turns."""', '"""Select a canonical model ID for future turns."""'),
    ('    def set_runtime_model(self, model: str) -> LLMRuntime:\n        """Select a model on the current provider for future turns."""\n        return self.runtime_resolver.select_model(model)\n\n', ''),
])

edit("nanobot/agent/tools/self.py", [
    ('        "model_id":              {"type": str, "min_len": 1},\n', ''),
    ('- User asks to switch to a named model preset → set model_id to that preset name.', '- User asks to switch models → set model_id to a configured canonical model ID.'),
    ("Use 'model_id' to switch named model presets.", "Use 'model_id' to switch configured canonical models."),
    ('str for model/model_id', 'str for model_id'),
    ('        if key in {"model", "context_window_tokens"} and current_request_session_key():', '        if key == "context_window_tokens" and current_request_session_key():'),
    ('                "during an active session; use a configured model_id"', '                "during an active session; switch model_id instead"'),
    ('        if key == "model_id":\n            self._runtime_control.set_model_id(cast(str, value))\n        elif key == "context_window_tokens":', '        if key == "context_window_tokens":'),
])

# The SDK now exposes only canonical model IDs for ordinary runtime selection.
p = Path("nanobot/nanobot.py")
text = p.read_text()
text = text.replace("ensure_single_model_selector,\n", "")
text = text.replace("        model: str | None = None,\n        model_preset: str | None = None,", "        model_id: str | None = None,")
text = text.replace("            model: Override the instance default model.\n            model_preset: Override the instance default model preset.", "            model_id: Override the instance default canonical model ID.")
text = text.replace("        ensure_single_model_selector(model=model, model_preset=model_preset)\n", "")
text = text.replace("        if model is not None:\n            config.agents.defaults.model_preset = None\n            config.agents.defaults.model = model\n            config.agents.defaults.provider = \"auto\"\n        elif model_preset is not None:\n            config.agents.defaults.model_preset = model_preset\n", "        if model_id is not None:\n            from nanobot.model_domain import get_model\n            get_model(config.models, model_id)\n            config.agents.defaults.model_id = model_id\n")
text = text.replace("            model: Override the model for this run only.\n            model_preset: Override the model preset for this run only.", "            model_id: Override the canonical model ID for this run only.")
text = text.replace("        runtime = self._loop.runtime_resolver.resolve_override(\n            model=model,\n            model_preset=model_preset,\n        )", "        runtime = self._loop.runtime_resolver.resolve_override(model_id=model_id)")
text = text.replace("        override_runtime = self._loop.runtime_resolver.resolve_override(\n            model=model,\n            model_preset=model_preset,\n        )", "        override_runtime = self._loop.runtime_resolver.resolve_override(model_id=model_id)")
text = text.replace('"model_preset": runtime.model_preset,', '"model_id": runtime.model_id,')
text = text.replace("            model=model,\n            model_preset=model_preset,", "            model_id=model_id,")
p.write_text(text)
