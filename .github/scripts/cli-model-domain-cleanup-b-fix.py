from pathlib import Path
import re

# Fix canonicalized onboard fixtures and terminology after the primary migration.
path = Path("tests/agent/test_onboard_logic.py")
text = path.read_text()
text = re.sub(
    r'ModelConfig\(\n\s+display_name="Test", provider="openai", model=("[^"]+"),\n\s+provider=("[^"]+"),\n\s+\)',
    r'ModelConfig(\n            display_name="Test", provider=\2, model=\1,\n        )',
    text,
)
text = text.replace(
    'config.agents.defaults.model = "test/provider-model"',
    'config.agents.defaults.model_id = "alternate"',
)
text = text.replace(
    "test_quick_start_provider_choice_asks_for_model_id",
    "test_quick_start_provider_choice_asks_for_upstream_model",
)
text = text.replace(
    "test_quick_start_custom_base_url_asks_for_model_id",
    "test_quick_start_custom_base_url_asks_for_upstream_model",
)
text = text.replace(
    "test_quick_start_requires_model_id_before_setting_defaults",
    "test_quick_start_requires_upstream_model_before_setting_defaults",
)
text = text.replace('[("Model ID", "", "openrouter")]', '[("Upstream model", "", "openrouter")]')
text = text.replace(
    '("Model ID", "openai-codex/gpt-5.6-sol", "openai_codex")',
    '("Upstream model", "openai-codex/gpt-5.6-sol", "openai_codex")',
)
path.write_text(text)

path = Path("nanobot/cli/onboard.py")
text = path.read_text()
text = text.replace(
    'model = _input_model_with_autocomplete(\n            "Model ID",',
    'model = _input_model_with_autocomplete(\n            "Upstream model",',
)
text = text.replace(
    'console.print("[yellow]! Model ID is required for Quick Start[/yellow]")',
    'console.print("[yellow]! Upstream model is required for Quick Start[/yellow]")',
)
path.write_text(text)

# The provider-error test must let AgentLoop initialize its canonical main model;
# the simulated provider failure belongs to the attempted session switch only.
path = Path("tests/command/test_model_command.py")
text = path.read_text()
text = text.replace(
    '''        selected = model_config or catalog[model_id]\n        resolved_id = model_id or next(\n            key for key, value in catalog.items() if value is selected\n        )\n''',
    '''        if model_config is None:\n            assert model_id is not None\n            selected = catalog[model_id]\n            resolved_id = model_id\n        else:\n            selected = model_config\n            resolved_id = model_id or next(\n                key for key, value in catalog.items() if value is selected\n            )\n''',
)
old = '''@pytest.mark.asyncio
async def test_model_command_reports_provider_configuration_errors(tmp_path) -> None:
    def fail_model(**_kwargs):
        raise ValueError("No API key configured for provider 'openai'.")
    loop = _make_loop(tmp_path, provider_snapshot_loader=fail_model)
    switched = await cmd_model(_ctx(loop, "/model fast", args="fast"))
    assert "Could not switch model" in switched.content
    assert "No API key configured for provider 'openai'." in switched.content
'''
new = '''@pytest.mark.asyncio
async def test_model_command_reports_provider_configuration_errors(tmp_path) -> None:
    main_provider = _provider("base-model", max_tokens=123)

    def fail_fast(*, model_id=None, **_kwargs):
        if model_id == "fast":
            raise ValueError("No API key configured for provider 'openai'.")
        return ProviderSnapshot(
            model_id="main",
            provider=main_provider,
            model="base-model",
            context_window_tokens=1000,
            signature=("main", "base-model"),
            generation=main_provider.generation,
        )

    loop = _make_loop(tmp_path, provider_snapshot_loader=fail_fast)
    switched = await cmd_model(_ctx(loop, "/model fast", args="fast"))
    assert "Could not switch model" in switched.content
    assert "No API key configured for provider 'openai'." in switched.content
'''
if old not in text:
    raise RuntimeError("provider-error test pattern not found")
path.write_text(text.replace(old, new))
