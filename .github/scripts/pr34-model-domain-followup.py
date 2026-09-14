from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected pattern not found in {path}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Reuse the canonical shared model usage/deletion API from nanobot.model_settings.
onboard = Path("nanobot/cli/onboard.py")
replace_once(
    onboard,
    "from nanobot.model_domain import ModelCapabilities, ModelConfig, validate_model_id\n",
    "from nanobot.model_domain import ModelCapabilities, ModelConfig, validate_model_id\n"
    "from nanobot.model_settings import delete_model, find_model_usages\n",
)
replace_once(
    onboard,
    '''            if action == "Delete":
                if model_id == config.agents.defaults.model_id:
                    console.print("[yellow]! Select another default model before deleting this model[/yellow]")
                    _pause()
                    continue
                confirm = _get_questionary().confirm(
                    f"Delete model '{model_id}'?", default=False
                ).ask()
                if confirm:
                    del config.models[model_id]
                    _sync_model_id_cache(config)
                    last_model_id = None
                continue
''',
    '''            if action == "Delete":
                usages = find_model_usages(config, model_id)
                if usages:
                    rendered_usages = ", ".join(usages)
                    console.print(
                        f"[yellow]! Cannot delete model '{model_id}'; it is still used by: "
                        f"{escape(rendered_usages)}[/yellow]"
                    )
                    _pause()
                    continue
                confirm = _get_questionary().confirm(
                    f"Delete model '{model_id}'?", default=False
                ).ask()
                if confirm:
                    delete_model(config, model_id=model_id)
                    _sync_model_id_cache(config)
                    last_model_id = None
                continue
''',
)

# Add an onboard regression test proving the wizard consumes the shared usage API
# and blocks every canonical consumer class without editing those consumers.
onboard_tests = Path("tests/agent/test_onboard_logic.py")
replace_once(
    onboard_tests,
    '''    def test_provider_field_handler(self, monkeypatch):
''',
    '''    def test_delete_model_blocks_all_canonical_usages(self, monkeypatch):
        from nanobot.config.schema import SystemPromptOverrideConfig

        config = Config()
        config.models["fast"] = ModelConfig(
            display_name="Fast",
            provider="openai",
            model="gpt-fast",
            capabilities=ModelCapabilities(
                text=True,
                vision=True,
                image_generation=True,
                transcription=True,
            ),
        )
        config.agents.defaults.model_id = "fast"
        config.agents.defaults.dream.model_id = "fast"
        config.agents.defaults.dream.fallback_model_id = "fast"
        config.subagent_roles["general"].model_id = "fast"
        config.transcription.model_id = "fast"
        config.tools.image_analysis.model_id = "fast"
        config.tools.image_generation.model_id = "fast"
        config.system_prompt_overrides = [
            SystemPromptOverrideConfig(prompt="bound", model_ids=["fast"])
        ]

        answers = iter(["fast - gpt-fast", "Delete", "<- Back"])
        printed: list[str] = []
        pauses: list[bool] = []

        class NoConfirm:
            @staticmethod
            def confirm(*_args, **_kwargs):
                raise AssertionError("referenced models must be rejected before confirmation")

        monkeypatch.setattr(onboard_wizard.console, "clear", lambda: None)
        monkeypatch.setattr(
            onboard_wizard.console,
            "print",
            lambda message, *args, **kwargs: printed.append(str(message)),
        )
        monkeypatch.setattr(onboard_wizard, "_show_section_header", lambda *a, **kw: None)
        monkeypatch.setattr(
            onboard_wizard,
            "_select_with_back",
            lambda *a, **kw: next(answers),
        )
        monkeypatch.setattr(onboard_wizard, "_get_questionary", lambda: NoConfirm())
        monkeypatch.setattr(onboard_wizard, "_pause", lambda *a, **kw: pauses.append(True))

        onboard_wizard._configure_models(config)

        assert "fast" in config.models
        assert pauses == [True]
        message = "\n".join(printed)
        for usage in (
            "agents.defaults.model_id",
            "dream.model_id",
            "dream.fallback_model_id",
            "subagent_roles.general.model_id",
            "transcription.model_id",
            "system_prompt_overrides[0].model_ids",
            "tools.image_analysis.model_id",
            "tools.image_generation.model_id",
        ):
            assert usage in message

    def test_provider_field_handler(self, monkeypatch):
''',
)

# Restore the timezone tests that were valid independently of the removed preset world.
timezone_tests = Path("tests/config/test_timezone.py")
text = timezone_tests.read_text(encoding="utf-8")
text = text.replace(
    "import json\n\n",
    "import json\nimport os\nimport subprocess\nimport sys\nimport textwrap\n\nimport pytest\n\n",
    1,
)
append = '''\n\ndef test_agent_timezone_rejects_unknown_iana_name() -> None:
    with pytest.raises(ValueError, match="unknown timezone"):
        Config.model_validate({"agents": {"defaults": {"timezone": "Not/AZone"}}})


def test_agent_timezones_use_packaged_data_without_system_database() -> None:
    script = textwrap.dedent(
        """\\
        from zoneinfo import TZPATH

        from nanobot.config.schema import Config

        assert not TZPATH
        for name in ("UTC", "Asia/Shanghai"):
            config = Config.model_validate({"agents": {"defaults": {"timezone": name}}})
            serialized = config.model_dump(mode="json", by_alias=True)
            restored = Config.model_validate(serialized)
            assert restored.agents.defaults.timezone == name
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=os.environ | {"PYTHONTZPATH": ""},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
'''
if "def test_agent_timezone_rejects_unknown_iana_name" in text:
    raise RuntimeError("timezone tests already migrated")
timezone_tests.write_text(text.rstrip() + append, encoding="utf-8")

# Restore provider-schema tests that are independent of legacy presets/provider guessing.
provider_tests = Path("tests/config/test_provider_config.py")
if provider_tests.exists():
    raise RuntimeError("tests/config/test_provider_config.py already exists")
provider_tests.write_text(
    '''import pytest

from nanobot.config.schema import Config


def test_provider_api_type_accepts_exact_values_only() -> None:
    config = Config.model_validate(
        {
            "providers": {
                "openai": {
                    "apiKey": "sk-test",
                    "apiType": "responses",
                }
            }
        }
    )
    assert config.providers.openai.api_type == "responses"

    with pytest.raises(ValueError):
        Config.model_validate(
            {
                "providers": {
                    "openai": {
                        "apiKey": "sk-test",
                        "apiType": "response",
                    }
                }
            }
        )


def test_provider_api_type_is_openai_only() -> None:
    with pytest.raises(ValueError, match="only supported"):
        Config.model_validate(
            {
                "providers": {
                    "custom": {
                        "apiBase": "https://example.test/v1",
                        "apiType": "responses",
                    }
                }
            }
        )

    with pytest.raises(ValueError, match="only supported"):
        Config.model_validate(
            {
                "providers": {
                    "my-company-api": {
                        "apiBase": "https://example.test/v1",
                        "apiType": "responses",
                    }
                }
            }
        )


@pytest.mark.parametrize("provider_name", ["openai-codex", "github-copilot", "lm-studio"])
def test_dynamic_custom_provider_rejects_builtin_provider_aliases(provider_name: str) -> None:
    with pytest.raises(ValueError, match="conflicts with built-in provider"):
        Config.model_validate(
            {
                "providers": {
                    provider_name: {
                        "apiBase": "https://example.test/v1",
                    }
                }
            }
        )
''',
    encoding="utf-8",
)
