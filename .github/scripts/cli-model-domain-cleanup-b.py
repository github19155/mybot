from __future__ import annotations

from pathlib import Path
import re


def read(path: str) -> str:
    return Path(path).read_text()


def write(path: str, text: str) -> None:
    Path(path).write_text(text)


def replace(path: str, old: str, new: str, *, count: int = -1) -> None:
    text = read(path)
    if old not in text:
        raise RuntimeError(f"pattern not found in {path}: {old[:120]!r}")
    write(path, text.replace(old, new, count))


# Gateway already gets Config.models through AgentLoop.from_config; remove the old catalog glue.
replace("nanobot/cli/gateway_runtime.py", "    from nanobot.agent.model_presets import load_model_preset_catalog\n", "")
replace("nanobot/cli/gateway_runtime.py", "        preset_catalog_loader=load_model_preset_catalog,\n", "")

# OAuth --set-main writes one canonical model definition instead of parallel provider/model fields.
replace(
    "nanobot/cli/provider.py",
    "    from nanobot.config.loader import get_config_path, load_config, save_config, set_config_path\n",
    "    from nanobot.config.loader import get_config_path, load_config, save_config, set_config_path\n"
    "    from nanobot.model_domain import ModelCapabilities, ModelConfig\n",
)
replace(
    "nanobot/cli/provider.py",
    '''    config.agents.defaults.model_preset = None\n    config.agents.defaults.provider = provider_name\n    config.agents.defaults.model = selected_model\n    if provider_name == "xai_grok" and selected_model in {\n        "xai-grok/grok-4.5",\n        "xai-grok/grok-4.6",\n    }:\n        config.agents.defaults.context_window_tokens = 500_000\n''',
    '''    model_id = "main"\n    context_window_tokens = (\n        500_000\n        if provider_name == "xai_grok" and selected_model in {\n            "xai-grok/grok-4.5",\n            "xai-grok/grok-4.6",\n        }\n        else 200_000\n    )\n    config.models[model_id] = ModelConfig(\n        display_name="Main",\n        provider=provider_name,\n        model=selected_model,\n        capabilities=ModelCapabilities(text=True),\n        context_window_tokens=context_window_tokens,\n    )\n    config.agents.defaults.model_id = model_id\n''',
)

# CLI display derives upstream model from the canonical selected model ID.
replace(
    "nanobot/cli/runtime_config.py",
    "from nanobot.config.schema import Config\n",
    "from nanobot.config.schema import Config\nfrom nanobot.model_domain import get_model\n",
)
replace(
    "nanobot/cli/runtime_config.py",
    '''def _model_display(config: Config) -> tuple[str, str]:\n    """Return (resolved_model_name, preset_tag) for display strings."""\n    resolved = config.resolve_preset()\n    name = config.agents.defaults.model_preset\n    tag = f" (preset: {name})" if name else ""\n    return resolved.model, tag\n''',
    '''def _model_display(config: Config) -> tuple[str, str]:\n    """Return the selected upstream model and canonical model ID tag."""\n    model_id = config.agents.defaults.model_id\n    model = get_model(config.models, model_id)\n    return model.model, f" (model_id: {model_id})"\n''',
)

# TUI receives the canonical identity, while upstream model remains display-only.
replace(
    "nanobot/cli/tui_launcher.py",
    '                "NANOBOT_TUI_MODEL_PRESET": config.agents.defaults.model_preset or "default",\n',
    '                "NANOBOT_TUI_MODEL_ID": config.agents.defaults.model_id,\n',
)

# Slash model command is model_id-only and does not synthesize a default selector.
p = Path("nanobot/command/builtin.py")
text = p.read_text()
text = text.replace('        "Switch model preset",\n        "Show or switch the active model preset.",\n        "brain",\n        "[preset]",',
                    '        "Switch model",\n        "Show or switch the active canonical model ID.",\n        "brain",\n        "[model_id]",')
start = text.index("def _format_preset_names(")
end = text.index("\n\nasync def cmd_dream_prompt", start)
replacement = '''def _format_model_ids(model_ids: list[str]) -> str:\n    return ", ".join(f"`{model_id}`" for model_id in model_ids) if model_ids else "(none configured)"\n\n\ndef _model_ids(loop: AgentLoop) -> list[str]:\n    return sorted(loop.models)\n\n\ndef _command_error_message(exc: Exception) -> str:\n    return str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)\n\n\ndef _model_command_status(loop: AgentLoop, session: Session) -> str:\n    model_ids = _model_ids(loop)\n    try:\n        runtime = loop.runtime_for_session(session, recover_removed=False)\n    except (KeyError, ValueError) as exc:\n        return "\\n".join([\n            "## Model",\n            f"- Current selection error: {_command_error_message(exc)}",\n            f"- Available model IDs: {_format_model_ids(model_ids)}",\n            "- Switch with `/model <model_id>`.",\n        ])\n    active = runtime.model_id or loop.model_id\n    return "\\n".join([\n        "## Model",\n        f"- Current model ID: `{active}`",\n        f"- Upstream model: `{runtime.model}`",\n        f"- Available model IDs: {_format_model_ids(model_ids)}",\n    ])\n\n\nasync def cmd_model(ctx: CommandContext) -> OutboundMessage:\n    """Show or switch the canonical model ID for this session."""\n    loop = ctx.loop\n    model_id = ctx.args.strip()\n    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}\n\n    if not model_id:\n        session = ctx.session or loop.sessions.get_or_create(ctx.key)\n        return OutboundMessage(\n            channel=ctx.msg.channel,\n            chat_id=ctx.msg.chat_id,\n            content=_model_command_status(loop, session),\n            metadata=metadata,\n        )\n\n    try:\n        runtime = loop.set_session_model_id(ctx.key, model_id)\n    except (KeyError, ValueError) as exc:\n        return OutboundMessage(\n            channel=ctx.msg.channel,\n            chat_id=ctx.msg.chat_id,\n            content=(\n                f"Could not switch model: {_command_error_message(exc)}\\n\\n"\n                f"Available model IDs: {_format_model_ids(_model_ids(loop))}"\n            ),\n            metadata=metadata,\n        )\n\n    max_tokens = runtime.generation.max_tokens\n    lines = [\n        f"Switched model to `{runtime.model_id}`.",\n        "- Scope: current session",\n        f"- Upstream model: `{runtime.model}`",\n        f"- Context window: {runtime.context_window_tokens}",\n        f"- Max output tokens: {max_tokens}",\n    ]\n    return OutboundMessage(\n        channel=ctx.msg.channel,\n        chat_id=ctx.msg.chat_id,\n        content="\\n".join(lines),\n        metadata=metadata,\n    )\n'''
text = text[:start] + replacement + text[end:]
p.write_text(text)

# Onboarding: one model-id cache, canonical ModelConfig CRUD, no preset compatibility.
p = Path("nanobot/cli/onboard.py")
text = p.read_text()
text = text.replace(
    "from nanobot.config.schema import Config, ModelPresetConfig\n",
    "from nanobot.config.schema import Config\n"
    "from nanobot.model_domain import ModelCapabilities, ModelConfig, validate_model_id\n",
)
text = text.replace(
    "# Cache of model-preset names populated at runtime so that field handlers can\n"
    "# offer existing presets as choices (e.g. AgentDefaults.model_preset).\n"
    "_MODEL_PRESET_CACHE: set[str] = set()",
    "# Canonical model IDs available to model_id fields during this wizard session.\n"
    "_MODEL_ID_CACHE: set[str] = set()",
)
old_handler_start = text.index("def _handle_model_preset_field(")
old_handler_end = text.index("\n\ndef _set_field_from_choices", old_handler_start)
new_handler = '''def _handle_model_id_field(\n    working_model: BaseModel, field_name: str, field_display: str, current_value: Any\n) -> None:\n    """Handle a canonical model_id field using Config.models IDs."""\n    field_info = type(working_model).model_fields.get(field_name)\n    optional = bool(field_info and _is_str_or_none(field_info.annotation))\n    model_ids = sorted(_MODEL_ID_CACHE)\n    choices = ([_CLEAR_CHOICE] if optional else []) + model_ids\n    if not choices:\n        return\n    default_choice = str(current_value) if current_value in choices else choices[0]\n    new_value = _select_with_back(field_display, choices, default=default_choice)\n    if new_value is _BACK_PRESSED or new_value is None:\n        return\n    setattr(working_model, field_name, None if new_value == _CLEAR_CHOICE else new_value)\n'''
text = text[:old_handler_start] + new_handler + text[old_handler_end:]
text = text.replace('    "model_preset": _handle_model_preset_field,', '    "model_id": _handle_model_id_field,')
text = text.replace(
    '    choices = ["auto"] + sorted(_get_provider_names().keys())\n    default_choice = str(current_value) if current_value else "auto"\n',
    '    choices = sorted(_get_provider_names().keys())\n    if not isinstance(working_model, ModelConfig):\n        choices = ["auto", *choices]\n    default_choice = str(current_value) if current_value in choices else choices[0]\n',
)
text = text.replace(
    '    from nanobot.config.schema import AgentDefaults\n\n    default_context = AgentDefaults.model_fields["context_window_tokens"].default\n',
    '    default_context = ModelConfig.model_fields["context_window_tokens"].default\n',
)
section_start = text.index("# --- Model Preset Configuration ---")
section_end = text.index("# --- Provider Configuration ---", section_start)
models_section = '''# --- Model Configuration ---\n\n\ndef _sync_model_id_cache(config: Config) -> None:\n    """Synchronize the module-level model ID cache from Config.models."""\n    _MODEL_ID_CACHE.clear()\n    _MODEL_ID_CACHE.update(config.models)\n\n\ndef _validate_model_id_input(text: str) -> bool | str:\n    try:\n        validate_model_id(text.strip())\n    except ValueError as exc:\n        return str(exc)\n    return True\n\n\ndef _configure_models(config: Config) -> None:\n    """Configure canonical model definitions keyed by model_id."""\n    _sync_model_id_cache(config)\n\n    def get_model_choices() -> tuple[list[str], dict[str, str]]:\n        choices: list[str] = []\n        choice_to_id: dict[str, str] = {}\n        for model_id, model in config.models.items():\n            choice = f"{model_id} - {model.model}"\n            choices.append(choice)\n            choice_to_id[choice] = model_id\n        choices.extend(["[+] Add new model", "<- Back"])\n        return choices, choice_to_id\n\n    last_model_id: str | None = None\n    while True:\n        try:\n            console.clear()\n            _show_section_header(\n                "Models",\n                "Create, edit or delete canonical model definitions",\n            )\n            choices, choice_to_id = get_model_choices()\n            default_choice = next(\n                (choice for choice, mid in choice_to_id.items() if mid == last_model_id),\n                None,\n            )\n            answer = _select_with_back("Select model:", choices, default=default_choice)\n            if answer is _BACK_PRESSED or answer is None or answer == "<- Back":\n                break\n            assert isinstance(answer, str)\n\n            if answer == "[+] Add new model":\n                model_id_input = _get_questionary().text(\n                    "Model ID:", validate=_validate_model_id_input\n                ).ask()\n                if not model_id_input:\n                    continue\n                model_id = model_id_input.strip()\n                if model_id in config.models:\n                    console.print(f"[yellow]! Model ID '{model_id}' already exists[/yellow]")\n                    _pause()\n                    continue\n                provider = _select_with_back(\n                    "Provider:", sorted(_get_provider_names().keys())\n                )\n                if provider is _BACK_PRESSED or provider is None:\n                    continue\n                upstream = _input_model_with_autocomplete("Upstream model", "", str(provider))\n                if upstream is _BACK_PRESSED or not upstream:\n                    continue\n                display_name = _get_questionary().text(\n                    "Display name:", default=model_id\n                ).ask()\n                if display_name is None:\n                    continue\n                candidate = ModelConfig(\n                    display_name=display_name.strip() or model_id,\n                    provider=str(provider),\n                    model=str(upstream),\n                    capabilities=ModelCapabilities(text=True),\n                )\n                _try_auto_fill_context_window(candidate, str(upstream))\n                updated = _configure_pydantic_model(candidate, f"New Model: {model_id}")\n                if updated is not None:\n                    config.models[model_id] = updated\n                    _sync_model_id_cache(config)\n                    last_model_id = model_id\n                continue\n\n            model_id = choice_to_id.get(answer)\n            if model_id is None:\n                continue\n            model = config.models.get(model_id)\n            if model is None:\n                continue\n            last_model_id = model_id\n            actions = ["Edit", "Delete", "Cancel"]\n            action = _select_with_back(f"Model: {model_id}", actions, default="Edit")\n            if action is _BACK_PRESSED or action in {None, "Cancel"}:\n                continue\n            if action == "Delete":\n                if model_id == config.agents.defaults.model_id:\n                    console.print("[yellow]! Select another default model before deleting this model[/yellow]")\n                    _pause()\n                    continue\n                confirm = _get_questionary().confirm(\n                    f"Delete model '{model_id}'?", default=False\n                ).ask()\n                if confirm:\n                    del config.models[model_id]\n                    _sync_model_id_cache(config)\n                    last_model_id = None\n                continue\n            updated = _configure_pydantic_model(model, f"Edit Model: {model_id}")\n            if updated is not None:\n                config.models[model_id] = updated\n                _sync_model_id_cache(config)\n        except KeyboardInterrupt:\n            console.print("\\n[dim]Returning to main menu...[/dim]")\n            break\n\n\n'''
text = text[:section_start] + models_section + text[section_end:]
text = text.replace("    # Model Presets\n    preset_rows: list[tuple[str, str]] = []\n    for name, preset in config.model_presets.items():\n        preset_rows.append((name, f\"{preset.model} - ctx {preset.context_window_tokens}\"))\n    _print_summary_panel(preset_rows, \"Model Presets\")\n",
                    "    # Models\n    model_rows: list[tuple[str, str]] = []\n    for model_id, model in config.models.items():\n        model_rows.append((model_id, f\"{model.provider} / {model.model} - ctx {model.context_window_tokens}\"))\n    _print_summary_panel(model_rows, \"Models\")\n")
text = text.replace(
    '''def _set_primary_quick_start_preset(config: Config, provider_name: str, model: str) -> None:\n    """Store the primary preset used by Quick Start."""\n    config.model_presets["primary"] = ModelPresetConfig(\n        model=model,\n        provider=provider_name,\n    )\n    config.agents.defaults.model_preset = "primary"\n    _sync_preset_cache(config)\n''',
    '''def _set_quick_start_model(config: Config, provider_name: str, model: str) -> None:\n    """Store Quick Start's canonical main model."""\n    model_id = "main"\n    config.models[model_id] = ModelConfig(\n        display_name="Main",\n        provider=provider_name,\n        model=model,\n        capabilities=ModelCapabilities(text=True),\n        context_window_tokens=get_model_context_limit(model, provider_name) or 200_000,\n    )\n    config.agents.defaults.model_id = model_id\n    _sync_model_id_cache(config)\n''',
)
text = text.replace("_set_primary_quick_start_preset(", "_set_quick_start_model(")
text = text.replace(
    '    preset = config.model_presets.get("primary")\n',
    '    selected_model = config.models.get(config.agents.defaults.model_id)\n',
)
text = text.replace("    if preset:\n        provider_config = getattr(config.providers, preset.provider, None)\n        provider_info = _get_quick_start_provider_info().get(preset.provider)\n",
                    "    if selected_model:\n        provider_config = getattr(config.providers, selected_model.provider, None)\n        provider_info = _get_quick_start_provider_info().get(selected_model.provider)\n")
text = text.replace("_quick_start_oauth_is_authenticated(config, preset.provider)", "_quick_start_oauth_is_authenticated(config, selected_model.provider)")
text = text.replace("_get_provider_names().get(preset.provider, preset.provider)", "_get_provider_names().get(selected_model.provider, selected_model.provider)")
text = text.replace('        "[M] Model Presets",', '        "[M] Models",')
text = text.replace('            "[M] Model Presets": lambda: _configure_model_presets(config),', '            "[M] Models": lambda: _configure_models(config),')
text = text.replace("    _sync_preset_cache(config)\n", "    _sync_model_id_cache(config)\n")
p.write_text(text)

# Self-tool tests: runtime control is already canonical; align mocks and read-only catalog tests.
p = Path("tests/agent/tools/test_self_tool.py")
text = p.read_text()
text = text.replace("from nanobot.config.schema import ModelPresetConfig\n", "from nanobot.model_domain import ModelConfig\n")
text = text.replace("    loop.model_preset = None\n    loop.model_presets = {}\n", "    loop.model_id = \"main\"\n    loop.models = {}\n")
text = text.replace("    loop.set_runtime_model.side_effect = lambda value: setattr(loop, \"model\", value)\n", "    loop.set_model_id.side_effect = lambda value, **_kwargs: setattr(loop, \"model_id\", value)\n")
text = text.replace("test_modify_model_presets_dotpath_blocked", "test_modify_models_dotpath_blocked")
text = text.replace("test_inspect_read_only_model_preset_dotpath", "test_inspect_read_only_model_dotpath")
text = text.replace("model_presets=presets", "models=presets")
text = text.replace('key="model_presets.other"', 'key="models.other"')
text = text.replace('key="model_presets.fast.model"', 'key="models.fast.model"')
text = text.replace('"model_presets.fast.model: \'fast-model\'"', '"models.fast.model: \'fast-model\'"')
text = text.replace('ModelPresetConfig(model="fast-model")', 'ModelConfig(display_name="Fast", provider="openai", model="fast-model")')
p.write_text(text)

# TUI launcher test uses model_id env and a valid canonical model definition.
p = Path("tests/cli/test_tui_launcher.py")
text = p.read_text()
text = text.replace("from nanobot.config.schema import Config, ModelPresetConfig\n", "from nanobot.config.schema import Config\nfrom nanobot.model_domain import ModelCapabilities, ModelConfig\n")
text = text.replace("test_launcher_passes_the_canonical_model_preset_to_the_tui", "test_launcher_passes_the_canonical_model_id_to_the_tui")
text = text.replace('    config.model_presets["Deep Research"] = ModelPresetConfig(model="openai/gpt-5.6")\n    config.agents.defaults.model_preset = "Deep Research"\n',
                    '    config.models["deep-research"] = ModelConfig(\n        display_name="Deep Research",\n        provider="openai",\n        model="gpt-5.6",\n        capabilities=ModelCapabilities(text=True),\n    )\n    config.agents.defaults.model_id = "deep-research"\n')
text = text.replace('    assert captured["NANOBOT_TUI_MODEL"] == "openai/gpt-5.6"\n    assert captured["NANOBOT_TUI_MODEL_PRESET"] == "Deep Research"\n',
                    '    assert captured["NANOBOT_TUI_MODEL"] == "gpt-5.6"\n    assert captured["NANOBOT_TUI_MODEL_ID"] == "deep-research"\n    assert "NANOBOT_TUI_MODEL_PRESET" not in captured\n')
p.write_text(text)

# Skill command fixture only needs a canonical catalog; no selector is involved.
p = Path("tests/command/test_skill_command.py")
text = p.read_text()
text = text.replace("from nanobot.config.schema import ModelPresetConfig\n", "from nanobot.model_domain import ModelConfig\n")
text = text.replace('        model_presets={\n            "default": ModelPresetConfig(\n                model="test-model",\n                max_tokens=4096,\n                context_window_tokens=8000,\n            ),\n        },',
                    '        models={\n            "main": ModelConfig(\n                display_name="Main",\n                provider="anthropic",\n                model="test-model",\n                context_window_tokens=8000,\n            ),\n        },')
p.write_text(text)

# Model command tests are replaced only through their model-command section; goal tests stay intact.
p = Path("tests/command/test_model_command.py")
text = p.read_text()
marker = "\n\n@pytest.mark.asyncio\nasync def test_goal_command_shows_usage_without_args"
suffix = text[text.index(marker):]
prefix = '''from types import SimpleNamespace\nfrom unittest.mock import MagicMock\n\nimport pytest\n\nfrom nanobot.agent.loop import AgentLoop\nfrom nanobot.agent.permissions import current_permission_allowed\nfrom nanobot.bus.events import InboundMessage\nfrom nanobot.bus.queue import MessageBus\nfrom nanobot.command.builtin import (\n    build_help_text,\n    builtin_command_palette,\n    cmd_goal,\n    cmd_model,\n    register_builtin_commands,\n)\nfrom nanobot.command.router import CommandContext, CommandRouter\nfrom nanobot.model_domain import ModelConfig, ModelGenerationDefaults\nfrom nanobot.permission_types import GOAL_MUTATE\nfrom nanobot.providers.factory import ProviderSnapshot\nfrom nanobot.session.model_selection import model_id_from_metadata\n\n\ndef _provider(default_model: str, max_tokens: int = 123) -> MagicMock:\n    provider = MagicMock()\n    provider.get_default_model.return_value = default_model\n    provider.generation = SimpleNamespace(\n        max_tokens=max_tokens, temperature=0.1, reasoning_effort=None\n    )\n    return provider\n\n\ndef _make_loop(tmp_path, *, provider_snapshot_loader=None, models=None) -> AgentLoop:\n    catalog = models or {\n        "main": ModelConfig(\n            display_name="Main", provider="anthropic", model="base-model",\n            context_window_tokens=1000,\n            generation_defaults=ModelGenerationDefaults(max_tokens=123),\n        ),\n        "fast": ModelConfig(\n            display_name="Fast", provider="openai", model="gpt-4.1",\n            context_window_tokens=32_768,\n            generation_defaults=ModelGenerationDefaults(max_tokens=4096),\n        ),\n    }\n\n    def load_snapshot(*, model_id=None, model_config=None, **_kwargs):\n        selected = model_config or catalog[model_id]\n        resolved_id = model_id or next(\n            key for key, value in catalog.items() if value is selected\n        )\n        max_tokens = selected.generation_defaults.max_tokens or 123\n        provider = _provider(selected.model, max_tokens=max_tokens)\n        return ProviderSnapshot(\n            model_id=resolved_id, provider=provider, model=selected.model,\n            context_window_tokens=selected.context_window_tokens,\n            signature=(resolved_id, selected.model), generation=provider.generation,\n        )\n\n    return AgentLoop(\n        bus=MessageBus(), provider=_provider("base-model", max_tokens=123),\n        workspace=tmp_path, model="base-model", context_window_tokens=1000,\n        models=catalog, provider_snapshot_loader=provider_snapshot_loader or load_snapshot,\n        model_id="main",\n    )\n\n\ndef _ctx(loop: AgentLoop, raw: str, args: str = "") -> CommandContext:\n    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=raw)\n    return CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, args=args, loop=loop)\n\n\ndef _ctx_session(loop: AgentLoop, raw: str, args: str = "") -> CommandContext:\n    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=raw)\n    return CommandContext(\n        msg=msg, session=MagicMock(), key=msg.session_key, raw=raw, args=args, loop=loop,\n        is_user_turn=True,\n    )\n\n\ndef _saved_model_id(loop: AgentLoop, session_key: str = "cli:direct") -> str | None:\n    return model_id_from_metadata(loop.sessions.get_or_create(session_key).metadata)\n\n\n@pytest.mark.asyncio\nasync def test_model_command_lists_current_and_available_model_ids(tmp_path) -> None:\n    loop = _make_loop(tmp_path)\n    out = await cmd_model(_ctx(loop, "/model"))\n    assert "Current model ID: `main`" in out.content\n    assert "Upstream model: `base-model`" in out.content\n    assert "Available model IDs: `fast`, `main`" in out.content\n    assert out.metadata == {"render_as": "text"}\n\n\n@pytest.mark.asyncio\nasync def test_model_command_switches_model_id(tmp_path) -> None:\n    loop = _make_loop(tmp_path)\n    out = await cmd_model(_ctx(loop, "/model fast", args="fast"))\n    assert "Switched model to `fast`." in out.content\n    assert "Scope: current session" in out.content\n    assert "Upstream model: `gpt-4.1`" in out.content\n    assert _saved_model_id(loop) == "fast"\n    assert loop.model_id == "main"\n    assert loop.model == "base-model"\n\n    await loop.process_direct("/new", session_key="cli:direct")\n    assert _saved_model_id(loop) == "fast"\n    status = await loop.process_direct("/status", session_key="cli:direct")\n    assert status is not None and "gpt-4.1" in status.content\n\n\n@pytest.mark.asyncio\nasync def test_model_command_uses_exact_model_id_not_display_name(tmp_path) -> None:\n    loop = _make_loop(tmp_path, models={\n        "main": ModelConfig(display_name="Main", provider="anthropic", model="base-model"),\n        "deep-research": ModelConfig(\n            display_name="Deep Research", provider="openai", model="deep-model"\n        ),\n    })\n    out = await cmd_model(_ctx(loop, "/model Deep Research", args="Deep Research"))\n    assert "Could not switch model" in out.content\n    assert _saved_model_id(loop) is None\n    switched = await cmd_model(_ctx(loop, "/model deep-research", args="deep-research"))\n    assert "Switched model to `deep-research`." in switched.content\n    assert _saved_model_id(loop) == "deep-research"\n\n\n@pytest.mark.asyncio\nasync def test_model_command_switches_back_to_main(tmp_path) -> None:\n    loop = _make_loop(tmp_path)\n    await cmd_model(_ctx(loop, "/model fast", args="fast"))\n    out = await cmd_model(_ctx(loop, "/model main", args="main"))\n    assert "Switched model to `main`." in out.content\n    assert _saved_model_id(loop) == "main"\n    assert loop.model_id == "main"\n\n\n@pytest.mark.asyncio\nasync def test_model_command_unknown_model_id_keeps_old_state(tmp_path) -> None:\n    loop = _make_loop(tmp_path)\n    out = await cmd_model(_ctx(loop, "/model missing", args="missing"))\n    assert "Could not switch model" in out.content\n    assert "Available model IDs: `fast`, `main`" in out.content\n    assert _saved_model_id(loop) is None\n    assert loop.model_id == "main"\n\n\n@pytest.mark.asyncio\nasync def test_model_command_reports_provider_configuration_errors(tmp_path) -> None:\n    def fail_model(**_kwargs):\n        raise ValueError("No API key configured for provider 'openai'.")\n    loop = _make_loop(tmp_path, provider_snapshot_loader=fail_model)\n    switched = await cmd_model(_ctx(loop, "/model fast", args="fast"))\n    assert "Could not switch model" in switched.content\n    assert "No API key configured for provider 'openai'." in switched.content\n\n\n@pytest.mark.asyncio\nasync def test_model_command_does_not_depend_on_my_allow_set(tmp_path) -> None:\n    loop = _make_loop(tmp_path)\n    assert loop.tools_config.my.allow_set is False\n    await cmd_model(_ctx(loop, "/model fast", args="fast"))\n    assert _saved_model_id(loop) == "fast"\n\n\n@pytest.mark.asyncio\nasync def test_model_command_registered_as_exact_and_prefix(tmp_path) -> None:\n    router = CommandRouter()\n    register_builtin_commands(router)\n    loop = _make_loop(tmp_path)\n    out = await router.dispatch(_ctx(loop, "/model fast"))\n    assert out is not None\n    assert out.content == "\\n".join([\n        "Switched model to `fast`.",\n        "- Scope: current session",\n        "- Upstream model: `gpt-4.1`",\n        "- Context window: 32768",\n        "- Max output tokens: 4096",\n    ])\n    assert _saved_model_id(loop) == "fast"\n\n\n@pytest.mark.asyncio\nasync def test_model_command_does_not_change_another_session(tmp_path) -> None:\n    loop = _make_loop(tmp_path)\n    await cmd_model(_ctx(loop, "/model fast", args="fast"))\n    other = InboundMessage(channel="cli", sender_id="user", chat_id="other", content="/model")\n    out = await cmd_model(CommandContext(\n        msg=other, session=None, key=other.session_key, raw="/model", loop=loop\n    ))\n    assert "Current model ID: `main`" in out.content\n    assert _saved_model_id(loop) == "fast"\n\n\n@pytest.mark.asyncio\nasync def test_model_command_reports_removed_session_model_id(tmp_path) -> None:\n    loop = _make_loop(tmp_path)\n    session = loop.sessions.get_or_create("cli:direct")\n    session.metadata["_nanobot_model_id"] = "removed"\n    loop.sessions.save(session)\n    status = await loop.process_direct("/model", session_key="cli:direct")\n    switched = await loop.process_direct("/model main", session_key="cli:direct")\n    assert status is not None\n    assert "removed" in status.content\n    assert "Available model IDs: `fast`, `main`" in status.content\n    assert "Switch with `/model <model_id>`" in status.content\n    assert switched is not None and "Switched model to `main`." in switched.content\n    assert _saved_model_id(loop) == "main"\n\n\ndef test_model_command_in_help_and_palette() -> None:\n    model = next(item for item in builtin_command_palette() if item["command"] == "/model")\n    assert model["arg_hint"] == "[model_id]"\n    assert model["lifecycle"] == "side_channel"\n    assert model["accepts_args"] is True\n    assert "/model [model_id]" in build_help_text()\n'''
p.write_text(prefix + suffix)

# Onboard tests: migrate Quick Start identity and replace preset-wizard-only coverage.
p = Path("tests/agent/test_onboard_logic.py")
text = p.read_text()
text = text.replace("from nanobot.config.schema import Config, ModelPresetConfig\n", "from nanobot.config.schema import Config\nfrom nanobot.model_domain import ModelCapabilities, ModelConfig\n")
text = text.replace('config.model_presets["primary"]', 'config.models["main"]')
text = text.replace('config.agents.defaults.model_preset == "primary"', 'config.agents.defaults.model_id == "main"')
text = text.replace('"primary" not in config.model_presets', 'config.models["main"].provider == "anthropic"')
text = text.replace('config.model_presets', 'config.models')
text = text.replace('ModelPresetConfig(\n            model=', 'ModelConfig(\n            display_name="Test", provider="openai", model=')
# Remaining one-line constructors are in the legacy wizard block replaced below.
class_start = text.index("class TestModelPresetWizard:")
provider_test = text.index("    def test_provider_field_handler", class_start)
replacement = '''class TestModelWizard:\n    """Tests for canonical model CRUD in the onboard wizard."""\n\n    def test_sync_model_id_cache(self):\n        from nanobot.cli.onboard import _MODEL_ID_CACHE, _sync_model_id_cache\n        config = Config()\n        config.models["fast"] = ModelConfig(\n            display_name="Fast", provider="openai", model="gpt-4.1-mini"\n        )\n        _sync_model_id_cache(config)\n        assert _MODEL_ID_CACHE == {"main", "fast"}\n        _MODEL_ID_CACHE.clear()\n\n    def test_model_id_field_handler(self, monkeypatch):\n        from nanobot.cli.onboard import _MODEL_ID_CACHE, _handle_model_id_field\n        from nanobot.config.schema import AgentDefaults\n        _MODEL_ID_CACHE.clear()\n        _MODEL_ID_CACHE.update({"main", "fast"})\n        monkeypatch.setattr(onboard_wizard, "_select_with_back", lambda *a, **kw: "fast")\n        defaults = AgentDefaults()\n        _handle_model_id_field(defaults, "model_id", "Model ID", "main")\n        assert defaults.model_id == "fast"\n        _MODEL_ID_CACHE.clear()\n\n    def test_main_menu_dispatch_includes_models(self):\n        from nanobot.cli.onboard import _configure_models\n        assert callable(_configure_models)\n\n    def test_run_onboard_models_edit(self, monkeypatch):\n        initial_config = Config()\n        responses = iter([\n            "[A] Advanced Settings", "[M] Models", KeyboardInterrupt(), "[S] Save and Exit"\n        ])\n        def fake_select_with_back(*_args, **_kwargs):\n            response = next(responses)\n            if isinstance(response, BaseException):\n                raise response\n            return response\n        mutated = {"n": 0}\n        def fake_configure_models(config):\n            mutated["n"] += 1\n            config.models["test"] = ModelConfig(\n                display_name="Test", provider="openai", model="gpt-test",\n                capabilities=ModelCapabilities(text=True),\n            )\n        monkeypatch.setattr(onboard_wizard, "_select_with_back", fake_select_with_back)\n        monkeypatch.setattr(onboard_wizard, "_configure_models", fake_configure_models)\n        monkeypatch.setattr(onboard_wizard, "_show_main_menu_header", lambda: None)\n        monkeypatch.setattr(onboard_wizard, "_show_section_header", lambda *a, **kw: None)\n        monkeypatch.setattr(onboard_wizard, "console", SimpleNamespace(clear=lambda: None))\n        result = run_onboard(initial_config)\n        assert result.should_save is True\n        assert mutated["n"] == 1\n        assert "test" in result.config.models\n\n'''
text = text[:class_start] + replacement + text[provider_test:]
# The provider-field test now exercises ModelConfig, not removed AgentDefaults.provider.
text = text.replace('        from nanobot.config.schema import AgentDefaults\n\n        monkeypatch.setattr(onboard_wizard, "_select_with_back", lambda *a, **kw: "anthropic")\n\n        defaults = AgentDefaults()\n        _handle_provider_field(defaults, "provider", "Provider", "auto")\n        assert defaults.provider == "anthropic"',
                    '        monkeypatch.setattr(onboard_wizard, "_select_with_back", lambda *a, **kw: "anthropic")\n\n        model = ModelConfig(display_name="Test", provider="openai", model="gpt-test")\n        _handle_provider_field(model, "provider", "Provider", "openai")\n        assert model.provider == "anthropic"')
p.write_text(text)

# This file is exclusively the deleted preset compatibility contract; canonical domain tests live elsewhere.
Path("tests/config/test_model_presets.py").unlink()
Path("nanobot/agent/model_presets.py").unlink()
