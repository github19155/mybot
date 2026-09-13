from __future__ import annotations

import ast
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text.rstrip() + "\n", encoding="utf-8")


def drop_tests(path: str, *, names: tuple[str, ...] = (), tokens: tuple[str, ...] = ()) -> None:
    text = read(path)
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    ranges: list[tuple[int, int]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
            continue
        start = min([node.lineno, *[d.lineno for d in node.decorator_list]]) - 1
        end = node.end_lineno or node.lineno
        source = "".join(lines[start:end])
        if any(part in node.name for part in names) or any(token in source for token in tokens):
            ranges.append((start, end))
    for start, end in reversed(ranges):
        del lines[start:end]
    write(path, "".join(lines))


Path("tests/agent/test_runner_fallback.py").unlink(missing_ok=True)

p = "tests/agent/test_model_runtime_resolver.py"
s = read(p)
s = s.replace('loader.assert_called_once_with(preset_name="Fast", include_fallbacks=True)', 'loader.assert_called_once_with(preset_name="Fast")')
s = s.replace('    assert call.kwargs["include_fallbacks"] is True\n', '    assert "include_fallbacks" not in call.kwargs\n')
s = s.replace('    loader.assert_called_once_with(include_fallbacks=True)\n', '    loader.assert_called_once_with()\n')
write(p, s)
drop_tests(p, names=("forwards_explicit_fallback_policy",))

p = "tests/agent/test_runtime_refresh.py"
write(p, read(p).replace('loader.assert_called_once_with(include_fallbacks=True)', 'loader.assert_called_once_with()'))

p = "tests/agent/test_dream_control_plane.py"
write(p, read(p).replace('        include_fallbacks=False,\n', ''))

p = "tests/agent/test_runtime_resolution_integration.py"
write(p, read(p).replace('from nanobot.providers.fallback_provider import FallbackProvider\n', ''))
drop_tests(p, names=("main_and_dream_fallback_caches_are_order_independent",))
s = read(p)
insert = '''\n\n@pytest.mark.asyncio\n@pytest.mark.parametrize("dream_first", [False, True])\nasync def test_main_and_dream_share_one_resolved_preset_runtime(runtime_config, dream_first):\n    runtime_config.agents.defaults.dream.model_override = "worker"\n    original = runtime_config.model_dump()\n    resolver, loader = _resolver(runtime_config)\n    management = ModelManagement(runtime_config, runtime_resolver=resolver)\n\n    if dream_first:\n        dream = await management.resolve_dream_runtime("consolidation")\n        main = resolver.select_preset("worker")\n    else:\n        main = resolver.select_preset("worker")\n        dream = await management.resolve_dream_runtime("consolidation")\n\n    assert main is dream\n    assert main.provider.provider_name == "deepseek"\n    assert main.context_window_tokens == 48_000\n    assert main.snapshot_signature == provider_signature(runtime_config, preset_name="worker")\n    assert resolver.resolve_preset("worker") is main\n    assert await management.resolve_dream_runtime("consolidation") is main\n    assert resolver.runtime is main\n    assert loader.call_count == 1\n    assert runtime_config.model_dump() == original\n'''
marker = '\n\n@pytest.mark.asyncio\n@pytest.mark.parametrize(\n    ("role_selection", "selection", "expected_model", "expected_provider"),'
if marker not in s:
    raise RuntimeError("runtime integration insertion marker missing")
write(p, s.replace(marker, insert + marker, 1))

drop_tests("tests/agent/test_onboard_logic.py", tokens=("_handle_fallback_models_field", "fallback_models"))
drop_tests("tests/providers/test_custom_thinking_style.py", tokens=("fallbackModels",))

p = "tests/providers/test_llm_usage_observer.py"
s = read(p).replace('from types import SimpleNamespace\n', '').replace('from nanobot.providers.fallback_provider import FallbackProvider\n', '')
s = s.replace('\n\nclass _NoRetryProvider(_SequenceProvider):\n    _CHAT_RETRY_DELAYS = ()\n', '')
write(p, s)
drop_tests(p, names=("fallback_provider_propagates_observer_to_every_leaf",))

drop_tests("tests/utils/test_webui_turn_helpers.py", tokens=("build_webui_fallback_model_observer",))

p = "tests/webui/test_settings_api.py"
write(p, read(p).replace('from nanobot.config.schema import Config, InlineFallbackConfig, ModelPresetConfig', 'from nanobot.config.schema import Config, ModelPresetConfig'))
drop_tests(p, names=("model_call_order", "migrate_model_configurations"), tokens=("fallback_models", "InlineFallbackConfig", "fallbackModels"))
s = read(p)
s += '''\n\ndef test_update_model_call_order_selects_exactly_one_preset(tmp_path) -> None:\n    config = Config.model_validate({\n        "agents": {"defaults": {"modelPreset": "primary"}},\n        "modelPresets": {\n            "primary": {"model": "openai/gpt-4.1", "provider": "openai"},\n            "backup": {"model": "deepseek/deepseek-chat", "provider": "deepseek"},\n        },\n        "providers": {"openai": {"apiKey": "sk-test"}, "deepseek": {"apiKey": "sk-test"}},\n    })\n    path = tmp_path / "config.json"\n    from nanobot.config.loader import save_config\n    save_config(config, path)\n    payload = update_model_call_order({"order": [json.dumps(["backup"])]}, config_path=path)\n    assert payload["model_call_order"] == ["backup"]\n    saved = load_config(path)\n    assert saved.agents.defaults.model_preset == "backup"\n    assert not hasattr(saved.agents.defaults, "fallback_models")\n\n\ndef test_update_model_call_order_rejects_multiple_presets(tmp_path) -> None:\n    config = Config.model_validate({\n        "agents": {"defaults": {"modelPreset": "primary"}},\n        "modelPresets": {\n            "primary": {"model": "openai/gpt-4.1", "provider": "openai"},\n            "other": {"model": "openai/gpt-4o-mini", "provider": "openai"},\n        },\n        "providers": {"openai": {"apiKey": "sk-test"}},\n    })\n    path = tmp_path / "config.json"\n    from nanobot.config.loader import save_config\n    save_config(config, path)\n    with pytest.raises(WebUISettingsError, match="exactly one"):\n        update_model_call_order({"order": [json.dumps(["primary", "other"])]}, config_path=path)\n\n\ndef test_legacy_fallback_models_are_ignored_by_schema() -> None:\n    config = Config.model_validate({\n        "agents": {"defaults": {"model": "openai/gpt-4.1", "provider": "openai", "fallbackModels": ["missing"]}},\n        "providers": {"openai": {"apiKey": "sk-test"}},\n    })\n    assert not hasattr(config.agents.defaults, "fallback_models")\n'''
write(p, s)

print("fallback tests migrated")
