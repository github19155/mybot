from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config
from nanobot.model_domain import ModelCapabilities, ModelConfig
from nanobot.providers.oauth_model_catalog import OAuthModelCatalogSnapshot
from nanobot.providers.registry import ProviderModelSpec, find_by_name
from nanobot.webui.settings_api import (
    WebUISettingsError,
    _docs_version,
    _model_catalog_kind,
    _oauth_provider_status,
    _provider_requires_api_key,
    _reasoning_effort_values_for,
    create_model_configuration,
    create_provider_settings,
    delete_model_configuration,
    provider_models_payload,
    settings_payload,
    update_agent_settings,
    update_api_settings,
    update_image_generation_settings,
    update_model_configuration,
    update_network_safety_settings,
    update_provider_settings,
    update_transcription_settings,
    update_web_search_settings,
)

DYNAMIC_PROVIDER_NAME = "my-company-api"
DYNAMIC_PROVIDER_API_BASE = "https://example.test/v1"


def _select_config(monkeypatch: pytest.MonkeyPatch, config_path) -> None:
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)


def _dynamic_provider_config(
    *,
    api_base: str = DYNAMIC_PROVIDER_API_BASE,
    defaults: bool = False,
) -> Config:
    config = Config.model_validate(
        {
            "providers": {
                DYNAMIC_PROVIDER_NAME: {
                    "apiBase": api_base,
                }
            }
        }
    )
    if defaults:
        config.models["tenant-default"] = ModelConfig(
            display_name="Tenant default",
            provider=DYNAMIC_PROVIDER_NAME,
            model="gpt-4o-mini",
        )
        config.agents.defaults.model_id = "tenant-default"
    return config


def test_settings_payload_uses_canonical_models_only(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.models["fast"] = ModelConfig(
        display_name="Fast",
        provider="openai",
        model="gpt-4.1-mini",
        capabilities=ModelCapabilities(text=True, vision=True),
    )
    config.agents.defaults.model_id = "fast"
    save_config(config, config_path)
    _select_config(monkeypatch, config_path)

    payload = settings_payload()

    assert payload["agent"]["model_id"] == "fast"
    assert payload["agent"]["display_name"] == "Fast"
    assert payload["agent"]["provider"] == "openai"
    assert payload["agent"]["model"] == "gpt-4.1-mini"
    assert payload["agent"]["capabilities"]["vision"] is True
    assert {row["model_id"] for row in payload["models"]} == {"fast", "main"}
    assert "model_presets" not in payload
    assert "model_preset" not in payload["agent"]


def test_docs_version_uses_released_versions_and_falls_back_for_dev() -> None:
    assert _docs_version("0.2.3") == "0.2.3"
    assert _docs_version("0.2.3.post1") == "0.2.3.post1"
    assert _docs_version("0.2.3.dev0") == "latest"
    assert _docs_version("0.2.3+editable") == "latest"


def test_kimi_k3_only_offers_supported_reasoning_effort_values() -> None:
    assert _reasoning_effort_values_for("moonshot", "kimi-k3") == ["", "max"]


def test_settings_payload_includes_versioned_docs(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    _select_config(monkeypatch, config_path)
    monkeypatch.setattr("nanobot.webui.settings_api.__version__", "0.2.3")

    payload = settings_payload()

    assert payload["docs"] == {
        "version": "0.2.3",
        "base_url": "https://nanobot.wiki/docs/0.2.3",
        "chat_apps_url": "https://nanobot.wiki/docs/0.2.3/getting-started/chat-apps",
        "latest_url": "https://nanobot.wiki/docs/latest",
    }


def test_create_model_configuration_persists_model_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    _select_config(monkeypatch, config_path)

    payload = create_model_configuration(
        {
            "model_id": ["fast-writing"],
            "display_name": ["Fast writing"],
            "provider": ["openai"],
            "model": ["gpt-4.1-mini"],
            "capabilities": [json.dumps({"text": True, "vision": True})],
            "context_window_tokens": ["128000"],
            "generation_defaults": [
                json.dumps(
                    {
                        "max_tokens": 8192,
                        "temperature": 0.4,
                        "reasoning_effort": "high",
                    }
                )
            ],
        }
    )

    assert payload["created_model_id"] == "fast-writing"
    row = next(row for row in payload["models"] if row["model_id"] == "fast-writing")
    assert row["display_name"] == "Fast writing"
    assert row["provider"] == "openai"
    assert row["model"] == "gpt-4.1-mini"
    assert row["capabilities"]["vision"] is True
    assert row["context_window_tokens"] == 128000
    assert row["generation_defaults"]["max_tokens"] == 8192
    assert load_config(config_path).models["fast-writing"].display_name == "Fast writing"


def test_create_model_configuration_rejects_duplicate_model_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    _select_config(monkeypatch, config_path)

    with pytest.raises(WebUISettingsError) as duplicate:
        create_model_configuration(
            {
                "model_id": ["main"],
                "display_name": ["Duplicate"],
                "provider": ["openai"],
                "model": ["gpt-4.1-mini"],
            }
        )

    assert duplicate.value.status == 409


def test_create_model_configuration_accepts_dynamic_custom_provider(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(_dynamic_provider_config(), config_path)
    _select_config(monkeypatch, config_path)

    payload = create_model_configuration(
        {
            "model_id": ["tenant-model"],
            "display_name": ["Tenant model"],
            "provider": [DYNAMIC_PROVIDER_NAME],
            "model": ["gpt-4o-mini"],
        }
    )

    saved = load_config(config_path)
    assert payload["created_model_id"] == "tenant-model"
    assert saved.models["tenant-model"].provider == DYNAMIC_PROVIDER_NAME
    assert saved.models["tenant-model"].model == "gpt-4o-mini"


def test_update_model_configuration_changes_fields_without_renaming_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.models["codex"] = ModelConfig(
        display_name="Codex",
        provider="openai",
        model="gpt-4.1",
    )
    save_config(config, config_path)
    _select_config(monkeypatch, config_path)

    payload = update_model_configuration(
        {
            "model_id": ["codex"],
            "display_name": ["Codex primary"],
            "provider": ["openai_codex"],
            "model": ["openai-codex/gpt-5.6-sol"],
            "context_window_tokens": ["272000"],
        }
    )

    row = next(row for row in payload["models"] if row["model_id"] == "codex")
    assert row["display_name"] == "Codex primary"
    assert row["provider"] == "openai_codex"
    assert row["model"] == "openai-codex/gpt-5.6-sol"
    assert row["context_window_tokens"] == 272000
    saved = load_config(config_path)
    assert "codex" in saved.models


def test_update_model_configuration_rejects_model_id_rename(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    _select_config(monkeypatch, config_path)

    with pytest.raises(WebUISettingsError, match="model_id cannot be renamed"):
        update_model_configuration(
            {
                "model_id": ["main"],
                "new_name": ["other"],
            }
        )


def test_delete_model_configuration_preserves_usage_protection(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.models["spare"] = ModelConfig(
        display_name="Spare",
        provider="openai",
        model="gpt-4.1-mini",
    )
    save_config(config, config_path)
    _select_config(monkeypatch, config_path)

    with pytest.raises(WebUISettingsError) as referenced:
        delete_model_configuration({"model_id": ["main"]})
    assert referenced.value.status == 409

    payload = delete_model_configuration({"model_id": ["spare"]})
    assert "spare" not in {row["model_id"] for row in payload["models"]}
    assert "spare" not in load_config(config_path).models


def test_update_agent_settings_selects_model_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.models["fast"] = ModelConfig(
        display_name="Fast",
        provider="openai",
        model="gpt-4.1-mini",
    )
    save_config(config, config_path)
    _select_config(monkeypatch, config_path)

    payload = update_agent_settings({"model_id": ["fast"]})

    assert payload["agent"]["model_id"] == "fast"
    assert load_config(config_path).agents.defaults.model_id == "fast"


def test_settings_payload_includes_dynamic_custom_provider(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(_dynamic_provider_config(defaults=True), config_path)
    _select_config(monkeypatch, config_path)

    payload = settings_payload()
    providers = {row["name"]: row for row in payload["providers"]}

    assert payload["agent"]["model_id"] == "tenant-default"
    assert payload["agent"]["provider"] == DYNAMIC_PROVIDER_NAME
    assert providers[DYNAMIC_PROVIDER_NAME]["configured"] is True
    assert providers[DYNAMIC_PROVIDER_NAME]["api_key_required"] is False
    assert providers[DYNAMIC_PROVIDER_NAME]["api_base"] == DYNAMIC_PROVIDER_API_BASE


def test_settings_payload_includes_canonical_transcription_model(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.openai.api_key = "sk-test"
    config.models["speech"] = ModelConfig(
        display_name="Speech",
        provider="openai",
        model="whisper-1",
        capabilities=ModelCapabilities(transcription=True),
    )
    config.transcription.enabled = True
    config.transcription.model_id = "speech"
    config.transcription.language = "en"
    save_config(config, config_path)
    _select_config(monkeypatch, config_path)

    payload = settings_payload()

    assert payload["transcription"]["enabled"] is True
    assert payload["transcription"]["model_id"] == "speech"
    assert payload["transcription"]["provider"] == "openai"
    assert payload["transcription"]["model"] == "whisper-1"
    assert payload["transcription"]["provider_configured"] is True
    assert payload["transcription"]["language"] == "en"


def test_update_transcription_settings_writes_model_id_only(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.openrouter.api_key = "sk-or-test"
    config.models["speech"] = ModelConfig(
        display_name="Speech",
        provider="openrouter",
        model="nvidia/parakeet-tdt-0.6b-v3",
        capabilities=ModelCapabilities(transcription=True),
    )
    save_config(config, config_path)
    _select_config(monkeypatch, config_path)

    payload = update_transcription_settings(
        {
            "enabled": ["true"],
            "model_id": ["speech"],
            "language": ["ko"],
            "maxDurationSec": ["90"],
            "maxUploadMb": ["20"],
        }
    )

    saved = load_config(config_path)
    assert saved.transcription.enabled is True
    assert saved.transcription.model_id == "speech"
    assert saved.transcription.language == "ko"
    assert saved.transcription.max_duration_sec == 90
    assert saved.transcription.max_upload_mb == 20
    assert payload["transcription"]["provider"] == "openrouter"
    assert payload["transcription"]["model"] == "nvidia/parakeet-tdt-0.6b-v3"


def test_update_transcription_settings_rejects_model_without_capability(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    _select_config(monkeypatch, config_path)

    with pytest.raises(WebUISettingsError, match="does not support transcription"):
        update_transcription_settings({"model_id": ["main"]})


def test_update_transcription_settings_validates_language(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    _select_config(monkeypatch, config_path)

    with pytest.raises(WebUISettingsError, match="transcription language"):
        update_transcription_settings({"language": ["en-US"]})


def test_settings_payload_includes_canonical_image_generation_model(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.openrouter.api_key = "sk-or-test"
    config.models["image"] = ModelConfig(
        display_name="Image",
        provider="openrouter",
        model="google/gemini-2.5-flash-image",
        capabilities=ModelCapabilities(image_generation=True),
    )
    config.tools.image_generation.model_id = "image"
    save_config(config, config_path)
    _select_config(monkeypatch, config_path)

    payload = settings_payload()

    assert payload["image_generation"]["model_id"] == "image"
    assert payload["image_generation"]["provider"] == "openrouter"
    assert payload["image_generation"]["model"] == "google/gemini-2.5-flash-image"


def test_update_image_generation_settings_writes_model_id_only(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.openrouter.api_key = "sk-or-test"
    config.models["image"] = ModelConfig(
        display_name="Image",
        provider="openrouter",
        model="google/gemini-2.5-flash-image",
        capabilities=ModelCapabilities(image_generation=True),
    )
    save_config(config, config_path)
    _select_config(monkeypatch, config_path)

    payload = update_image_generation_settings(
        {"enabled": ["true"], "model_id": ["image"]}
    )

    saved = load_config(config_path)
    assert saved.tools.image_generation.enabled is True
    assert saved.tools.image_generation.model_id == "image"
    assert payload["image_generation"]["provider"] == "openrouter"
    assert payload["image_generation"]["model"] == "google/gemini-2.5-flash-image"


def test_update_api_settings_requires_key_for_network_access(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    _select_config(monkeypatch, config_path)

    with pytest.raises(WebUISettingsError, match="API key"):
        update_api_settings({"host": ["0.0.0.0"], "port": ["8900"]})

    payload = update_api_settings(
        {
            "host": ["0.0.0.0"],
            "port": ["9900"],
            "api_key": ["secret-token"],
        }
    )
    saved = load_config(config_path)
    assert saved.api.host == "0.0.0.0"
    assert saved.api.port == 9900
    assert saved.api.api_key == "secret-token"
    assert payload["api"]["api_key_hint"]


def test_update_network_safety_settings_writes_local_service_flag(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    _select_config(monkeypatch, config_path)
    monkeypatch.setattr("nanobot.webui.workspaces.get_webui_dir", lambda: tmp_path / "webui")

    payload = update_network_safety_settings(
        {
            "webui_allow_local_service_access": ["false"],
            "webui_default_access_mode": ["full"],
        }
    )

    saved = load_config(config_path)
    assert saved.tools.webui_allow_local_service_access is False
    assert payload["advanced"]["webui_default_access_mode"] == "full"


def test_update_web_search_settings_accepts_keenable_without_api_key(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.tools.web.search.provider = "brave"
    config.tools.web.search.api_key = "brave-key"
    save_config(config, config_path)
    _select_config(monkeypatch, config_path)

    payload = update_web_search_settings({"provider": ["keenable"]})

    saved = load_config(config_path)
    assert saved.tools.web.search.provider == "keenable"
    assert saved.tools.web.search.api_key == ""
    option = next(
        item for item in payload["web_search"]["providers"] if item["name"] == "keenable"
    )
    assert option["credential"] == "optional_api_key"


def test_create_provider_settings_persists_custom_provider(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    _select_config(monkeypatch, config_path)

    payload = create_provider_settings(
        {
            "name": ["Company Gateway"],
            "apiBase": ["https://gateway.example/v1"],
            "apiKey": ["sk-company"],
        }
    )

    provider_name = payload["created_provider"]
    saved = load_config(config_path).providers.model_extra[provider_name]
    assert saved.display_name == "Company Gateway"
    assert saved.api_key == "sk-company"
    assert saved.api_base == "https://gateway.example/v1"


def test_update_provider_settings_updates_dynamic_custom_provider(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(_dynamic_provider_config(api_base="https://old.example/v1"), config_path)
    _select_config(monkeypatch, config_path)

    payload = update_provider_settings(
        {
            "provider": [DYNAMIC_PROVIDER_NAME],
            "apiBase": ["https://new.example/v1"],
            "apiKey": ["sk-test"],
        }
    )

    providers = {row["name"]: row for row in payload["providers"]}
    assert providers[DYNAMIC_PROVIDER_NAME]["api_base"] == "https://new.example/v1"
    saved = load_config(config_path).providers.model_extra[DYNAMIC_PROVIDER_NAME]
    assert saved.api_base == "https://new.example/v1"
    assert saved.api_key == "sk-test"


def test_settings_payload_includes_oauth_provider_status(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    _select_config(monkeypatch, config_path)

    def fake_oauth_status(spec):
        return {
            "configured": spec.name == "openai_codex",
            "account": "acct-test" if spec.name == "openai_codex" else None,
            "expires_at": 123 if spec.name == "openai_codex" else None,
            "login_supported": True,
        }

    monkeypatch.setattr("nanobot.webui.settings_api._oauth_provider_status", fake_oauth_status)

    payload = settings_payload()
    providers = {row["name"]: row for row in payload["providers"]}

    assert providers["openai_codex"]["auth_type"] == "oauth"
    assert providers["openai_codex"]["configured"] is True
    assert providers["openai_codex"]["oauth_account"] == "acct-test"


def test_oauth_status_accepts_refreshable_xai_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = SimpleNamespace(
        access="access-token",
        refresh="refresh-token",
        expires=1,
        account_id="user@example.com",
    )
    monkeypatch.setattr(
        "nanobot.providers.xai_oauth.get_xai_oauth_login_status",
        lambda: token,
    )

    status = _oauth_provider_status(find_by_name("xai_grok"))

    assert status["configured"] is True
    assert status["account"] == "user@example.com"


def test_provider_models_payload_fetches_openai_compatible_models(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.deepseek.api_key = "sk-test"
    save_config(config, config_path)
    _select_config(monkeypatch, config_path)

    def fake_get(url: str, **kwargs):
        assert url == "https://api.deepseek.com/models"
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={"data": [{"id": "deepseek-chat", "owned_by": "deepseek"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("nanobot.webui.settings_api.httpx.get", fake_get)

    payload = provider_models_payload({"provider": ["deepseek"]})

    assert payload["status"] == "available"
    assert payload["model_count"] == 1
    assert payload["models"][0]["id"] == "deepseek-chat"


def test_provider_models_payload_returns_online_openai_codex_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nanobot.webui.settings_models.get_oauth_model_catalog",
        lambda *_args, **_kwargs: OAuthModelCatalogSnapshot(
            models=(
                ProviderModelSpec(
                    id="openai-codex/gpt-5.6-sol",
                    label="GPT-5.6-Sol",
                    owned_by="OpenAI Codex",
                    context_window=272_000,
                ),
            ),
            source="remote",
            fetched_at=123,
        ),
    )

    payload = provider_models_payload({"provider": ["openai_codex"]})

    assert payload["status"] == "available"
    assert payload["source"] == "remote"
    assert payload["models"][0]["id"] == "openai-codex/gpt-5.6-sol"


def test_provider_models_payload_fetches_dynamic_custom_provider_models(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(_dynamic_provider_config(), config_path)
    _select_config(monkeypatch, config_path)

    def fake_get(url: str, **kwargs):
        assert url == f"{DYNAMIC_PROVIDER_API_BASE}/models"
        assert "Authorization" not in kwargs["headers"]
        return httpx.Response(
            200,
            json={"data": [{"id": "custom-gpt", "owned_by": "example"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("nanobot.webui.settings_api.httpx.get", fake_get)

    payload = provider_models_payload({"provider": [DYNAMIC_PROVIDER_NAME]})

    assert payload["provider"] == DYNAMIC_PROVIDER_NAME
    assert payload["status"] == "available"
    assert payload["models"][0]["id"] == "custom-gpt"


def test_model_catalog_kind_uses_provider_spec_metadata() -> None:
    assert _model_catalog_kind(find_by_name("anthropic")) == "unsupported"
    assert _model_catalog_kind(find_by_name("openrouter")) == "catalog"
    assert _model_catalog_kind(find_by_name("openai_codex")) == "hybrid"


def test_settings_payload_azure_openai_aad_mode_is_configured(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.azure_openai.api_base = "https://r.openai.azure.com"
    save_config(config, config_path)
    _select_config(monkeypatch, config_path)

    payload = settings_payload()
    azure = next(row for row in payload["providers"] if row["name"] == "azure_openai")

    assert azure["configured"] is True
    assert azure["api_key_required"] is False
    assert azure["api_base"] == "https://r.openai.azure.com"


def test_azure_openai_spec_does_not_require_api_key() -> None:
    spec = find_by_name("azure_openai")
    assert spec is not None
    assert _provider_requires_api_key(spec) is False
