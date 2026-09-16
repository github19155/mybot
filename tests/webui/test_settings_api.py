from __future__ import annotations

import builtins
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config
from nanobot.llm_usage import get_llm_usage_store
from nanobot.llm_usage.models import LLMCallRecord
from nanobot.model_domain import ModelCapabilities, ModelConfig
from nanobot.providers.base import LLMUsage
from nanobot.providers.oauth_model_catalog import OAuthModelCatalogSnapshot
from nanobot.providers.registry import ProviderModelSpec, find_by_name
from nanobot.webui.settings_api import (
    WebUISettingsError,
    _docs_version,
    _model_catalog_kind,
    _oauth_provider_status,
    _reasoning_effort_values_for,
    complete_oauth_provider,
    create_model_configuration,
    create_provider_settings,
    delete_model_configuration,
    login_oauth_provider,
    logout_oauth_provider,
    provider_models_payload,
    settings_payload,
    settings_usage_payload,
    update_agent_settings,
    update_api_settings,
    update_image_generation_settings,
    update_model_configuration,
    update_network_safety_settings,
    update_provider_settings,
    update_transcription_settings,
    update_web_search_settings,
)
from nanobot.webui.settings_services import WebUIOAuthFlowRegistry

DYNAMIC_PROVIDER_NAME = "my-company-api"
DYNAMIC_PROVIDER_API_BASE = "https://example.test/v1"


@pytest.fixture
def oauth_flows() -> WebUIOAuthFlowRegistry:
    return WebUIOAuthFlowRegistry()




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
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    monkeypatch.setattr("nanobot.webui.settings_api.__version__", "0.2.3")

    payload = settings_payload()

    assert payload["docs"] == {
        "version": "0.2.3",
        "base_url": "https://nanobot.wiki/docs/0.2.3",
        "chat_apps_url": "https://nanobot.wiki/docs/0.2.3/getting-started/chat-apps",
        "latest_url": "https://nanobot.wiki/docs/latest",
    }


def test_settings_payload_exposes_edenai_provider(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.edenai.api_key = "eden-test-key"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = settings_payload()
    edenai = next(row for row in payload["providers"] if row["name"] == "edenai")

    assert edenai["label"] == "Eden AI"
    assert edenai["configured"] is True
    assert edenai["default_api_base"] == "https://api.edenai.run/v3"
    assert edenai["model_catalog"] == "catalog"
    assert edenai["model_selectable"] is True


def test_settings_payload_exposes_orcarouter_provider(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.orcarouter.api_key = "sk-orca-test"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = settings_payload()
    orcarouter = next(row for row in payload["providers"] if row["name"] == "orcarouter")

    assert orcarouter["label"] == "OrcaRouter"
    assert orcarouter["configured"] is True
    assert orcarouter["default_api_base"] == "https://api.orcarouter.ai/v1"
    assert orcarouter["model_catalog"] == "catalog"
    assert orcarouter["model_selectable"] is True


def test_settings_payload_includes_relocated_capabilities(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.api.port = 9910
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")

    payload = settings_payload()

    assert payload["api"]["port"] == 9910
    assert payload["api"]["api_key_hint"] is None
    assert payload["observability"]["provider"] == "langfuse"
    assert payload["observability"]["configured"] is True




def test_update_api_settings_requires_key_for_network_access(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

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


def test_update_api_settings_requires_key_for_specific_network_interface(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    with pytest.raises(WebUISettingsError, match="API key"):
        update_api_settings({"host": ["192.168.1.10"], "port": ["8900"]})


def test_update_api_settings_allows_alternate_loopback_without_key(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    update_api_settings({"host": ["127.0.0.2"], "port": ["8900"]})

    assert load_config(config_path).api.host == "127.0.0.2"




































def test_update_provider_settings_updates_dynamic_custom_provider(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(_dynamic_provider_config(api_base="https://old.example/v1"), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = update_provider_settings(
        {
            "provider": [DYNAMIC_PROVIDER_NAME],
            "apiBase": ["https://new.example/v1"],
            "apiKey": ["sk-test"],
        }
    )

    providers = {row["name"]: row for row in payload["providers"]}
    assert providers[DYNAMIC_PROVIDER_NAME]["api_base"] == "https://new.example/v1"
    assert providers[DYNAMIC_PROVIDER_NAME]["api_key_hint"] == "••••"
    saved = load_config(config_path)
    dynamic_provider = saved.providers.model_extra[DYNAMIC_PROVIDER_NAME]
    assert dynamic_provider.api_base == "https://new.example/v1"
    assert dynamic_provider.api_key == "sk-test"


def test_create_provider_settings_persists_custom_advanced_options(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = create_provider_settings(
        {
            "name": ["Company Gateway"],
            "apiBase": ["https://gateway.example/v1"],
            "apiKey": ["sk-company"],
            "proxy": ["http://127.0.0.1:7890"],
            "extraHeaders": [json.dumps({"X-Tenant": "engineering"})],
            "extraBody": [json.dumps({"service_tier": "priority"})],
            "extraQuery": [json.dumps({"api-version": "2026-01-01"})],
            "thinkingStyle": ["enable_thinking"],
        }
    )

    provider_name = payload["created_provider"]
    row = next(provider for provider in payload["providers"] if provider["name"] == provider_name)
    assert row["label"] == "Company Gateway"
    assert row["is_custom"] is True
    assert row["advanced_fields"] == [
        "extra_headers",
        "extra_body",
        "extra_query",
        "proxy",
        "thinking_style",
    ]
    assert row["extra_headers"] == {"X-Tenant": "engineering"}
    assert row["extra_body"] == {"service_tier": "priority"}
    assert row["extra_query"] == {"api-version": "2026-01-01"}

    saved = load_config(config_path).providers.model_extra[provider_name]
    assert saved.display_name == "Company Gateway"
    assert saved.api_key == "sk-company"
    assert saved.api_base == "https://gateway.example/v1"
    assert saved.proxy == "http://127.0.0.1:7890"
    assert saved.extra_headers == {"X-Tenant": "engineering"}
    assert saved.extra_body == {"service_tier": "priority"}
    assert saved.extra_query == {"api-version": "2026-01-01"}
    assert saved.thinking_style == "enable_thinking"


def test_provider_settings_redacts_and_preserves_structured_secrets(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.openai.api_key = "sk-openai"
    config.providers.openai.extra_headers = {
        "Authorization": "Bearer header-secret",
        "X-Trace": "visible",
    }
    config.providers.openai.extra_body = {
        "access_token": "body-secret",
        "metadata": {
            "client_secret": "nested-secret",
            "label": "visible",
        },
    }
    config.providers.openai.extra_query = {
        "api_key": "query-secret",
        "api-version": "2026-01-01",
    }
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = settings_payload()
    row = next(provider for provider in payload["providers"] if provider["name"] == "openai")
    serialized_row = json.dumps(row, ensure_ascii=False)

    assert "header-secret" not in serialized_row
    assert "body-secret" not in serialized_row
    assert "nested-secret" not in serialized_row
    assert "query-secret" not in serialized_row
    assert row["extra_headers"]["X-Trace"] == "visible"
    assert row["extra_body"]["metadata"]["label"] == "visible"
    assert row["extra_query"]["api-version"] == "2026-01-01"

    row["extra_headers"]["X-Trace"] = "updated"
    row["extra_body"]["access_token"] = "replacement-secret"
    row["extra_body"]["metadata"]["label"] = "updated"
    row["extra_query"]["api-version"] = "2026-07-24"
    update_provider_settings(
        {
            "provider": ["openai"],
            "extraHeaders": [json.dumps(row["extra_headers"], ensure_ascii=False)],
            "extraBody": [json.dumps(row["extra_body"], ensure_ascii=False)],
            "extraQuery": [json.dumps(row["extra_query"], ensure_ascii=False)],
        }
    )

    saved = load_config(config_path).providers.openai
    assert saved.extra_headers == {
        "Authorization": "Bearer header-secret",
        "X-Trace": "updated",
    }
    assert saved.extra_body == {
        "access_token": "replacement-secret",
        "metadata": {
            "client_secret": "nested-secret",
            "label": "updated",
        },
    }
    assert saved.extra_query == {
        "api_key": "query-secret",
        "api-version": "2026-07-24",
    }


def test_update_provider_settings_persists_provider_specific_advanced_options(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.openai.api_key = "sk-openai"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    update_provider_settings(
        {
            "provider": ["openai"],
            "apiType": ["responses"],
            "proxy": ["http://127.0.0.1:7890"],
            "extraHeaders": [json.dumps({"X-Trace": "enabled"})],
            "extraBody": [json.dumps({"service_tier": "priority"})],
            "extraQuery": [json.dumps({"trace": "true"})],
        }
    )
    update_provider_settings(
        {
            "provider": ["bedrock"],
            "region": ["us-west-2"],
            "profile": ["production"],
            "extraBody": [json.dumps({"guardrailIdentifier": "guardrail-1"})],
        }
    )

    saved = load_config(config_path)
    assert saved.providers.openai.api_type == "responses"
    assert saved.providers.openai.proxy == "http://127.0.0.1:7890"
    assert saved.providers.openai.extra_headers == {"X-Trace": "enabled"}
    assert saved.providers.openai.extra_body == {"service_tier": "priority"}
    assert saved.providers.openai.extra_query == {"trace": "true"}
    assert saved.providers.bedrock.region == "us-west-2"
    assert saved.providers.bedrock.profile == "production"
    assert saved.providers.bedrock.extra_body == {"guardrailIdentifier": "guardrail-1"}


@pytest.mark.parametrize(
    ("provider_name", "config_attr"),
    [
        ("openai_codex", "openai_codex"),
        ("xai_grok", "xai_grok"),
    ],
)
def test_update_provider_settings_updates_and_clears_oauth_proxy(
    provider_name: str,
    config_attr: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    getattr(config.providers, config_attr).proxy = "http://127.0.0.1:7000"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    monkeypatch.setattr(
        "nanobot.webui.settings_api._oauth_provider_status",
        lambda _spec: {
            "configured": False,
            "account": None,
            "expires_at": None,
            "login_supported": True,
        },
    )

    payload = update_provider_settings(
        {
            "provider": [provider_name],
            "proxy": [" http://127.0.0.1:7890 "],
            "extraBody": [json.dumps({"tools": []})],
        }
    )

    providers = {row["name"]: row for row in payload["providers"]}
    assert providers[provider_name]["proxy"] == "http://127.0.0.1:7890"
    assert getattr(load_config(config_path).providers, config_attr).proxy == (
        "http://127.0.0.1:7890"
    )
    assert providers[provider_name]["extra_body"] == {"tools": []}
    assert getattr(load_config(config_path).providers, config_attr).extra_body == {"tools": []}

    cleared = update_provider_settings({"provider": [provider_name], "proxy": ["  "]})

    providers = {row["name"]: row for row in cleared["providers"]}
    assert providers[provider_name]["proxy"] is None
    assert getattr(load_config(config_path).providers, config_attr).proxy is None


def test_update_provider_settings_keeps_oauth_credentials_read_only(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    with pytest.raises(WebUISettingsError, match="only supports proxy and extra_body settings"):
        update_provider_settings({"provider": ["openai_codex"], "apiKey": ["not-allowed"]})




def test_update_agent_settings_marks_timezone_as_manual(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nanobot.config.timezone.get_localzone_name",
        lambda: "Asia/Shanghai",
    )
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = update_agent_settings({"timezone": ["Asia/Shanghai"]})

    assert payload["requires_restart"] is False
    saved = load_config(config_path)
    assert saved.agents.defaults.timezone == "Asia/Shanghai"
    assert saved.agents.defaults.timezone_mode == "manual"








def test_settings_payload_includes_oauth_provider_status(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    def fake_oauth_status(spec):
        if spec.name == "openai_codex":
            return {
                "configured": True,
                "account": "acct-test",
                "expires_at": 123,
                "login_supported": True,
            }
        return {
            "configured": False,
            "account": None,
            "expires_at": None,
            "login_supported": True,
        }

    monkeypatch.setattr("nanobot.webui.settings_api._oauth_provider_status", fake_oauth_status)

    payload = settings_payload()
    providers = {row["name"]: row for row in payload["providers"]}

    assert providers["openai_codex"]["auth_type"] == "oauth"
    assert providers["openai_codex"]["configured"] is True
    assert providers["openai_codex"]["oauth_account"] == "acct-test"


def test_settings_payload_includes_dynamic_custom_provider(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(_dynamic_provider_config(defaults=True), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = settings_payload()
    providers = {row["name"]: row for row in payload["providers"]}

    assert payload["agent"]["provider"] == DYNAMIC_PROVIDER_NAME
    assert payload["agent"]["model_id"] == "tenant-default"
    assert providers[DYNAMIC_PROVIDER_NAME]["configured"] is True
    assert providers[DYNAMIC_PROVIDER_NAME]["api_key_required"] is False
    assert providers[DYNAMIC_PROVIDER_NAME]["api_base"] == DYNAMIC_PROVIDER_API_BASE




def test_settings_payload_groups_opencode_compatibility_alias(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = settings_payload()
    opencode_rows = [row for row in payload["providers"] if row["label"].startswith("OpenCode")]

    assert [(row["name"], row["label"]) for row in opencode_rows] == [
        ("opencode", "OpenCode Zen"),
        ("opencode_go", "OpenCode Go"),
    ]




def test_settings_payload_marks_dynamic_custom_provider_without_api_base_unconfigured(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config.model_validate(
        {
            "providers": {
                DYNAMIC_PROVIDER_NAME: {
                    "apiKey": "sk-test",
                }
            }
        }
    )
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = settings_payload()
    providers = {row["name"]: row for row in payload["providers"]}

    assert providers[DYNAMIC_PROVIDER_NAME]["configured"] is False
    assert providers[DYNAMIC_PROVIDER_NAME]["api_key_hint"] == "••••"
    assert providers[DYNAMIC_PROVIDER_NAME]["api_base"] is None


def test_settings_payload_includes_network_safety_fields(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.tools.webui_allow_local_service_access = False
    config.tools.ssrf_whitelist = ["100.64.0.0/10"]
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    monkeypatch.setattr("nanobot.webui.workspaces.get_webui_dir", lambda: tmp_path / "webui")

    payload = settings_payload()

    assert payload["advanced"]["webui_allow_local_service_access"] is False
    assert payload["advanced"]["allow_local_preview_access"] is False
    assert payload["advanced"]["webui_default_access_mode"] == "default"
    assert payload["advanced"]["private_service_protection_enabled"] is True
    assert payload["advanced"]["ssrf_whitelist_count"] == 1


def test_settings_payload_includes_exec_path_flags(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.tools.exec.path_prepend = "/venv/bin"
    config.tools.exec.path_append = "/usr/sbin"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    monkeypatch.setattr("nanobot.webui.workspaces.get_webui_dir", lambda: tmp_path / "webui")

    payload = settings_payload()

    assert payload["advanced"]["exec_path_prepend_set"] is True
    assert payload["advanced"]["exec_path_append_set"] is True


def test_update_web_search_settings_accepts_keenable_without_api_key(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.tools.web.search.provider = "brave"
    config.tools.web.search.api_key = "brave-key"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = update_web_search_settings({"provider": ["keenable"]})

    saved = load_config(config_path)
    assert saved.tools.web.search.provider == "keenable"
    assert saved.tools.web.search.api_key == ""
    option = next(item for item in payload["web_search"]["providers"] if item["name"] == "keenable")
    assert option["credential"] == "optional_api_key"


def test_update_web_search_settings_can_clear_optional_api_key(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.tools.web.search.provider = "keenable"
    config.tools.web.search.api_key = "keen-key"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    update_web_search_settings({"provider": ["keenable"], "api_key": [""]})

    saved = load_config(config_path)
    assert saved.tools.web.search.provider == "keenable"
    assert saved.tools.web.search.api_key == ""






















def test_update_transcription_settings_validates_language(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    with pytest.raises(WebUISettingsError, match="transcription language"):
        update_transcription_settings({"language": ["en-US"]})


def test_settings_payload_includes_token_usage_summary(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    get_llm_usage_store().record(
        LLMCallRecord(
            started_at_ms=int(time.time() * 1000),
            duration_ms=1,
            provider="openai",
            model="gpt-5",
            source="user",
            stream=False,
            finish_reason="stop",
            usage=LLMUsage.reported(input_tokens=10, output_tokens=5),
        )
    )

    payload = settings_payload()

    assert payload["usage"]["total_tokens_30d"] == 15
    assert payload["usage"]["total_tokens"] == 15
    assert payload["usage"]["peak_day_tokens"] == 15
    assert payload["usage"]["current_streak_days"] == 1
    assert payload["usage"]["longest_streak_days"] == 1
    assert payload["usage"]["active_days_30d"] == 1
    assert payload["usage"]["requests_30d"] == 1


def test_settings_usage_payload_returns_lightweight_token_usage(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    get_llm_usage_store().record(
        LLMCallRecord(
            started_at_ms=int(time.time() * 1000),
            duration_ms=1,
            provider="openai",
            model="gpt-5",
            source="user",
            stream=False,
            finish_reason="stop",
            usage=LLMUsage.reported(input_tokens=20, output_tokens=2),
        )
    )

    payload = settings_usage_payload()

    assert payload["total_tokens"] == 22
    assert payload["requests_30d"] == 1
    assert "agent" not in payload


def test_update_network_safety_settings_writes_local_service_flag(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    monkeypatch.setattr("nanobot.webui.workspaces.get_webui_dir", lambda: tmp_path / "webui")

    payload = update_network_safety_settings(
        {
            "webui_allow_local_service_access": ["false"],
            "webui_default_access_mode": ["full"],
        }
    )

    saved = load_config(config_path)
    saved_raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved.tools.webui_allow_local_service_access is False
    assert saved_raw["tools"]["webuiAllowLocalServiceAccess"] is False
    assert "allowLocalPreviewAccess" not in saved_raw["tools"]
    assert payload["advanced"]["webui_allow_local_service_access"] is False
    assert payload["advanced"]["webui_default_access_mode"] == "full"
    assert payload["requires_restart"] is True


def test_update_network_safety_settings_accepts_legacy_restricted_default_access(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    monkeypatch.setattr("nanobot.webui.workspaces.get_webui_dir", lambda: tmp_path / "webui")

    payload = update_network_safety_settings({"webui_default_access_mode": ["restricted"]})

    assert payload["advanced"]["webui_default_access_mode"] == "default"


def test_update_network_safety_settings_default_access_is_webui_only(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    before = config_path.read_text(encoding="utf-8")
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    monkeypatch.setattr("nanobot.webui.workspaces.get_webui_dir", lambda: tmp_path / "webui")

    payload = update_network_safety_settings({"webui_default_access_mode": ["full"]})

    saved = load_config(config_path)
    assert config_path.read_text(encoding="utf-8") == before
    assert saved.tools.restrict_to_workspace is False
    assert payload["advanced"]["webui_default_access_mode"] == "full"
    assert payload["requires_restart"] is False


def test_openai_codex_oauth_status_uses_available_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = type(
        "Token",
        (),
        {
            "access": "access-token",
            "refresh": "refresh-token",
            "expires": 2_000_000_000_000,
            "account_id": "acct-codex",
        },
    )()
    monkeypatch.setattr("oauth_cli_kit.storage.FileTokenStorage.load", lambda _self: token)

    status = _oauth_provider_status(find_by_name("openai_codex"))

    assert status["configured"] is True
    assert status["account"] == "acct-codex"


def test_openai_codex_oauth_status_uses_refreshable_expired_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = type(
        "Token",
        (),
        {
            "access": "access-token",
            "refresh": "refresh-token",
            "expires": 1,
            "account_id": "acct-codex",
        },
    )()
    monkeypatch.setattr("oauth_cli_kit.storage.FileTokenStorage.load", lambda _self: token)

    status = _oauth_provider_status(find_by_name("openai_codex"))

    assert status["configured"] is True
    assert status["expires_at"] == 1


def test_openai_codex_oauth_status_rejects_unavailable_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_load(_self):
        raise RuntimeError("refresh failed")

    monkeypatch.setattr("oauth_cli_kit.storage.FileTokenStorage.load", fake_load)

    status = _oauth_provider_status(find_by_name("openai_codex"))

    assert status["configured"] is False
    assert status["account"] is None


def test_xai_grok_status_accepts_refreshable_login(
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

    assert status == {
        "configured": True,
        "account": "user@example.com",
        "expires_at": 1,
        "login_supported": True,
    }


def test_openai_codex_oauth_login_passes_configured_proxy(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    oauth_flows: WebUIOAuthFlowRegistry,
) -> None:
    proxy = "http://127.0.0.1:23458"
    config_path = tmp_path / "config.json"
    save_config(
        Config.model_validate({"providers": {"openaiCodex": {"proxy": "${CODEX_PROXY_TEST}"}}}),
        config_path,
    )
    monkeypatch.setenv("CODEX_PROXY_TEST", proxy)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    captured: dict[str, object] = {}

    class FakeFlow:
        authorization_url = "https://auth.openai.com/oauth/authorize?state=test"
        remaining_seconds = 600
        expired = False

        def cancel(self) -> None:
            captured["cancelled"] = True

    def fake_start(*, proxy=None, timeout_s=None, open_browser=None):
        captured.update(
            proxy=proxy,
            timeout_s=timeout_s,
            open_browser=open_browser,
        )
        return FakeFlow()

    monkeypatch.setattr(
        "nanobot.providers.openai_codex_oauth.start_openai_codex_oauth_login",
        fake_start,
    )

    payload = login_oauth_provider(
        {"provider": ["openai-codex"]},
        oauth_flows=oauth_flows,
    )

    assert captured == {
        "proxy": proxy,
        "timeout_s": 600,
        "open_browser": True,
    }
    assert payload["status"] == "authorization_required"
    assert payload["provider"] == "openai_codex"
    assert payload["authorization_url"] == FakeFlow.authorization_url
    assert payload["completion_input"] == "callback_url"

    callbacks: list[str | None] = []

    def fake_complete(_flow, callback):
        callbacks.append(callback)
        if callback is None:
            return None
        return SimpleNamespace(access="access-token")

    monkeypatch.setattr(
        "nanobot.providers.openai_codex_oauth.complete_openai_codex_oauth_login",
        fake_complete,
    )
    monkeypatch.setattr(
        "nanobot.webui.settings_api.settings_payload",
        lambda **_kwargs: {"settings": "ready"},
    )

    pending = complete_oauth_provider(
        {"provider": ["openai-codex"], "flow_id": [payload["flow_id"]]},
        oauth_flows=oauth_flows,
    )
    completed = complete_oauth_provider(
        {"provider": ["openai-codex"], "flow_id": [payload["flow_id"]]},
        "http://localhost:1455/auth/callback?code=secret&state=test",
        oauth_flows=oauth_flows,
    )

    assert pending == {
        "status": "pending",
        "provider": "openai_codex",
        "flow_id": payload["flow_id"],
    }
    assert completed == {"settings": "ready"}
    assert callbacks == [
        None,
        "http://localhost:1455/auth/callback?code=secret&state=test",
    ]


def test_openai_codex_remote_login_uses_headless_dependency_mode(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    oauth_flows: WebUIOAuthFlowRegistry,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    captured: dict[str, object] = {}

    class FakeFlow:
        authorization_url = "https://auth.openai.com/oauth/authorize?state=test"
        remaining_seconds = 600
        expired = False

        def cancel(self) -> None:
            captured["cancelled"] = True

    def fake_start(**kwargs):
        captured.update(kwargs)
        return FakeFlow()

    monkeypatch.setattr(
        "nanobot.providers.openai_codex_oauth.start_openai_codex_oauth_login",
        fake_start,
    )

    try:
        payload = login_oauth_provider(
            {"provider": ["openai-codex"], "remote_browser": ["true"]},
            oauth_flows=oauth_flows,
        )
    finally:
        oauth_flows.clear("openai_codex")

    assert payload["completion_input"] == "callback_url"
    assert captured["open_browser"] is False
    assert captured["cancelled"] is True


def test_openai_codex_oauth_login_reports_missing_oauth_cli_kit(
    monkeypatch: pytest.MonkeyPatch,
    oauth_flows: WebUIOAuthFlowRegistry,
) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "nanobot.providers.openai_codex_oauth":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(WebUISettingsError) as exc:
        login_oauth_provider(
            {"provider": ["openai-codex"]},
            oauth_flows=oauth_flows,
        )

    assert str(exc.value) == (
        "This nanobot installation is missing the required oauth-cli-kit package. "
        "Reinstall or upgrade nanobot-ai using the same installation method."
    )


def test_github_copilot_oauth_login_reports_missing_oauth_cli_kit(
    monkeypatch: pytest.MonkeyPatch,
    oauth_flows: WebUIOAuthFlowRegistry,
) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "nanobot.providers.github_copilot_provider":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(WebUISettingsError) as exc:
        login_oauth_provider(
            {"provider": ["github-copilot"]},
            oauth_flows=oauth_flows,
        )

    assert str(exc.value) == (
        "This nanobot installation is missing the required oauth-cli-kit package. "
        "Reinstall or upgrade nanobot-ai using the same installation method."
    )


def test_xai_grok_login_starts_fresh_browser_flow_with_proxy(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    oauth_flows: WebUIOAuthFlowRegistry,
) -> None:
    proxy = "http://127.0.0.1:23458"
    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({"providers": {"xaiGrok": {"proxy": proxy}}}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    captured: dict[str, object] = {}

    class FakeFlow:
        authorization_url = "https://auth.x.ai/oauth2/authorize?state=test"
        remaining_seconds = 600
        expired = False

        def cancel(self) -> None:
            captured["cancelled"] = True

    def fake_start(*, proxy=None, timeout_s=None):
        captured.update(proxy=proxy, timeout_s=timeout_s)
        return FakeFlow()

    monkeypatch.setattr("nanobot.providers.xai_oauth.start_xai_oauth_login", fake_start)

    payload = login_oauth_provider(
        {"provider": ["xai-grok"]},
        oauth_flows=oauth_flows,
    )

    assert captured["proxy"] == proxy
    assert captured["timeout_s"] == 600
    assert payload["status"] == "authorization_required"
    assert payload["provider"] == "xai_grok"
    assert payload["authorization_url"] == FakeFlow.authorization_url
    assert payload["completion_input"] == "authorization_code"
    assert payload["flow_id"]

    callbacks: list[str | None] = []

    def fake_complete(_flow, callback):
        callbacks.append(callback)
        if callback is None:
            return None
        return SimpleNamespace(access="access-token")

    monkeypatch.setattr(
        "nanobot.providers.xai_oauth.complete_xai_oauth_login",
        fake_complete,
    )
    monkeypatch.setattr(
        "nanobot.webui.settings_api.settings_payload",
        lambda **_kwargs: {"settings": "ready"},
    )

    pending = complete_oauth_provider(
        {"provider": ["xai-grok"], "flow_id": [payload["flow_id"]]},
        oauth_flows=oauth_flows,
    )
    completed = complete_oauth_provider(
        {"provider": ["xai-grok"], "flow_id": [payload["flow_id"]]},
        "secret",
        oauth_flows=oauth_flows,
    )

    assert pending == {
        "status": "pending",
        "provider": "xai_grok",
        "flow_id": payload["flow_id"],
    }
    assert completed == {"settings": "ready"}
    assert callbacks == [None, "secret"]


def test_xai_grok_login_reports_upstream_failure_as_bad_gateway(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    oauth_flows: WebUIOAuthFlowRegistry,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    failure = RuntimeError("Could not reach xAI sign-in: ConnectError.")

    def fake_start(**_kwargs):
        raise failure

    monkeypatch.setattr("nanobot.providers.xai_oauth.start_xai_oauth_login", fake_start)

    with pytest.raises(WebUISettingsError) as exc:
        login_oauth_provider(
            {"provider": ["xai-grok"]},
            oauth_flows=oauth_flows,
        )

    assert exc.value.status == 502
    assert str(exc.value) == ("xAI OAuth login failed: Could not reach xAI sign-in: ConnectError.")
    assert exc.value.__cause__ is failure


def test_xai_grok_logout_removes_token_through_shared_lock(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    oauth_flows: WebUIOAuthFlowRegistry,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    token_path = tmp_path / "auth" / "xai.json"
    token_path.parent.mkdir(parents=True)
    token_path.write_text("{}", encoding="utf-8")
    token_path.with_suffix(".lock").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "nanobot.providers.xai_oauth.get_xai_oauth_storage_path",
        lambda: token_path,
    )

    logout_oauth_provider(
        {"provider": ["xai-grok"]},
        oauth_flows=oauth_flows,
    )

    assert not token_path.exists()


def test_provider_models_payload_fetches_openai_compatible_models(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.deepseek.api_key = "sk-test"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    def fake_get(url: str, **kwargs):
        assert url == "https://api.deepseek.com/models"
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "deepseek-chat", "owned_by": "deepseek"},
                    {"id": "deepseek-reasoner", "context_window": 65536},
                ]
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("nanobot.webui.settings_api.httpx.get", fake_get)

    payload = provider_models_payload({"provider": ["deepseek"]})

    assert payload["status"] == "available"
    assert payload["catalog_kind"] == "official"
    assert payload["model_count"] == 2
    assert payload["models"][0]["id"] == "deepseek-chat"
    assert payload["models"][1]["context_window"] == 65536


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
                    description="Latest frontier agentic coding model.",
                    owned_by="OpenAI Codex",
                    context_window=272_000,
                    reasoning_efforts=("low", "medium", "high", "xhigh", "max", "ultra"),
                ),
            ),
            source="remote",
            fetched_at=123,
        ),
    )

    payload = provider_models_payload({"provider": ["openai_codex"]})

    assert payload["status"] == "available"
    assert payload["catalog_kind"] == "hybrid"
    assert payload["source"] == "remote"
    assert payload["model_count"] == 1
    assert payload["models"][0] == {
        "id": "openai-codex/gpt-5.6-sol",
        "label": "GPT-5.6-Sol",
        "description": "Latest frontier agentic coding model.",
        "owned_by": "OpenAI Codex",
        "context_window": 272000,
        "reasoning_efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
        "supports_backend_search": False,
    }


def test_provider_models_payload_returns_online_github_copilot_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nanobot.webui.settings_models.get_oauth_model_catalog",
        lambda *_args, **_kwargs: OAuthModelCatalogSnapshot(
            models=(
                ProviderModelSpec(
                    id="github-copilot/claude-sonnet",
                    label="Claude Sonnet",
                    owned_by="GitHub Copilot",
                    context_window=200_000,
                ),
            ),
            source="remote",
            fetched_at=123,
        ),
    )

    payload = provider_models_payload({"provider": ["github_copilot"]})

    assert payload["status"] == "available"
    assert payload["catalog_kind"] == "hybrid"
    assert payload["source"] == "remote"
    assert payload["models"][0]["id"] == "github-copilot/claude-sonnet"


def test_provider_models_payload_returns_online_xai_grok_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nanobot.webui.settings_models.get_oauth_model_catalog",
        lambda *_args, **_kwargs: OAuthModelCatalogSnapshot(
            models=(
                ProviderModelSpec(
                    id="xai-grok/grok-4.6",
                    label="Grok 4.6",
                    description="Latest frontier model",
                    owned_by="xAI",
                    context_window=500_000,
                    reasoning_efforts=("xhigh", "high", "medium", "low"),
                    supports_backend_search=True,
                ),
                ProviderModelSpec(
                    id="xai-grok/grok-4.5",
                    label="Grok 4.5",
                    owned_by="xAI",
                    context_window=500_000,
                    reasoning_efforts=("high", "medium", "low"),
                    supports_backend_search=True,
                ),
            ),
            source="remote",
            fetched_at=123,
        ),
    )

    payload = provider_models_payload({"provider": ["xai_grok"]})

    assert payload["status"] == "available"
    assert payload["catalog_kind"] == "hybrid"
    assert payload["source"] == "remote"
    assert payload["fetched_at"] == 123
    assert payload["models"] == [
        {
            "id": "xai-grok/grok-4.6",
            "label": "Grok 4.6",
            "description": "Latest frontier model",
            "owned_by": "xAI",
            "context_window": 500000,
            "reasoning_efforts": ["xhigh", "high", "medium", "low"],
            "supports_backend_search": True,
        },
        {
            "id": "xai-grok/grok-4.5",
            "label": "Grok 4.5",
            "description": None,
            "owned_by": "xAI",
            "context_window": 500000,
            "reasoning_efforts": ["high", "medium", "low"],
            "supports_backend_search": True,
        },
    ]


def test_provider_models_payload_fetches_dynamic_custom_provider_models(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(_dynamic_provider_config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

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
    assert payload["catalog_kind"] == "custom"
    assert payload["models"][0]["id"] == "custom-gpt"


@pytest.mark.parametrize(
    ("api_base", "expected_url"),
    [
        ("https://api.minimaxi.com/anthropic", "https://api.minimaxi.com/anthropic/v1/models"),
        ("https://api.minimaxi.com/anthropic/v1", "https://api.minimaxi.com/anthropic/v1/models"),
    ],
)
def test_provider_models_payload_fetches_minimax_anthropic_models(
    api_base: str,
    expected_url: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.minimax_anthropic.api_key = "sk-test"
    config.providers.minimax_anthropic.api_base = api_base
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    def fake_get(url: str, **kwargs):
        assert url == expected_url
        assert kwargs["headers"]["X-Api-Key"] == "sk-test"
        assert "Authorization" not in kwargs["headers"]
        return httpx.Response(
            200,
            json={"data": [{"id": "MiniMax-M2.7-highspeed"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("nanobot.webui.settings_api.httpx.get", fake_get)

    payload = provider_models_payload({"provider": ["minimax_anthropic"]})

    assert payload["status"] == "available"
    assert payload["catalog_kind"] == "official"
    assert payload["models"] == [
        {
            "id": "MiniMax-M2.7-highspeed",
            "label": None,
            "owned_by": None,
            "context_window": None,
        }
    ]


def test_provider_models_payload_requires_gateway_key(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = provider_models_payload({"provider": ["openrouter"]})

    assert payload["status"] == "not_configured"
    assert payload["catalog_kind"] == "catalog"
    assert payload["models"] == []


def test_provider_models_payload_fetches_orcarouter_catalog(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.orcarouter.api_key = "sk-orca-test"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    def fake_get(url: str, **kwargs):
        assert url == "https://api.orcarouter.ai/v1/models"
        assert kwargs["headers"]["Authorization"] == "Bearer sk-orca-test"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "orcarouter/auto", "owned_by": "orcarouter"},
                    {"id": "anthropic/claude-sonnet-4.6", "owned_by": "anthropic"},
                ]
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("nanobot.webui.settings_api.httpx.get", fake_get)

    payload = provider_models_payload({"provider": ["orcarouter"]})

    assert payload["status"] == "available"
    assert payload["catalog_kind"] == "catalog"
    assert [model["id"] for model in payload["models"]] == [
        "orcarouter/auto",
        "anthropic/claude-sonnet-4.6",
    ]


def test_model_catalog_kind_uses_provider_spec_metadata() -> None:
    assert _model_catalog_kind(find_by_name("skywork")) == "official"
    assert _model_catalog_kind(find_by_name("anthropic")) == "unsupported"
    assert _model_catalog_kind(find_by_name("openrouter")) == "catalog"
    assert _model_catalog_kind(find_by_name("orcarouter")) == "catalog"
    assert _model_catalog_kind(find_by_name("openai_codex")) == "hybrid"
    assert _model_catalog_kind(find_by_name("xai_grok")) == "hybrid"
    assert _model_catalog_kind(find_by_name("github_copilot")) == "hybrid"




# ---------------------------------------------------------------------------
# Azure OpenAI: settings contract for static-key vs AAD (DefaultAzureCredential)
# ---------------------------------------------------------------------------


def test_settings_payload_azure_openai_with_api_key_is_configured(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Static-key mode: api_key + api_base both set -> configured."""
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.azure_openai.api_key = "k"
    config.providers.azure_openai.api_base = "https://r.openai.azure.com"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = settings_payload()
    azure = next(row for row in payload["providers"] if row["name"] == "azure_openai")

    assert azure["configured"] is True
    assert azure["api_key_required"] is False
    assert azure["auth_type"] == "api_key"
    assert azure["api_base"] == "https://r.openai.azure.com"


def test_settings_payload_azure_openai_aad_mode_is_configured(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AAD mode: only api_base set (no api_key) -> still configured."""
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.azure_openai.api_base = "https://r.openai.azure.com"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = settings_payload()
    azure = next(row for row in payload["providers"] if row["name"] == "azure_openai")

    assert azure["configured"] is True
    assert azure["api_key_required"] is False
    assert azure["api_base"] == "https://r.openai.azure.com"
    assert azure["api_key_hint"] is None


def test_settings_payload_azure_openai_missing_base_not_configured(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """api_key alone (no api_base) is NOT a working config -> not configured."""
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.azure_openai.api_key = "k"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = settings_payload()
    azure = next(row for row in payload["providers"] if row["name"] == "azure_openai")

    assert azure["configured"] is False






def test_azure_openai_spec_no_longer_requires_api_key() -> None:
    """Contract guard: api_key is optional for azure_openai (AAD fallback)."""
    from nanobot.webui.settings_api import _provider_requires_api_key

    spec = find_by_name("azure_openai")
    assert spec is not None
    assert _provider_requires_api_key(spec) is False



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
    assert set(payload["transcription"]) == {
        "enabled",
        "model_id",
        "language",
        "max_duration_sec",
        "max_upload_mb",
    }
    assert "provider" not in payload["transcription"]
    assert "model" not in payload["transcription"]
    assert "providers" not in payload["transcription"]

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
    assert set(payload["transcription"]) == {
        "enabled",
        "model_id",
        "language",
        "max_duration_sec",
        "max_upload_mb",
    }
    assert "provider" not in payload["transcription"]
    assert "model" not in payload["transcription"]
    assert "providers" not in payload["transcription"]

def test_update_transcription_settings_rejects_model_without_capability(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    _select_config(monkeypatch, config_path)

    with pytest.raises(WebUISettingsError, match="does not support transcription"):
        update_transcription_settings({"model_id": ["main"]})

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
    assert set(payload["image_generation"]) == {
        "enabled",
        "model_id",
        "default_aspect_ratio",
        "default_image_size",
        "max_images_per_turn",
        "save_dir",
    }
    assert "provider" not in payload["image_generation"]
    assert "model" not in payload["image_generation"]
    assert "providers" not in payload["image_generation"]

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
    assert set(payload["image_generation"]) == {
        "enabled",
        "model_id",
        "default_aspect_ratio",
        "default_image_size",
        "max_images_per_turn",
        "save_dir",
    }
    assert "provider" not in payload["image_generation"]
    assert "model" not in payload["image_generation"]
    assert "providers" not in payload["image_generation"]

def test_settings_payload_keeps_configured_opencode_legacy_alias(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config = Config.model_validate(
        {"providers": {"opencodeZen": {"apiKey": "legacy-key"}}}
    )
    config.models["zen"] = ModelConfig(
        display_name="OpenCode Zen",
        provider="opencode_zen",
        model="opencode/deepseek-v4-pro",
    )
    config.agents.defaults.model_id = "zen"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = settings_payload()
    zen_rows = [row for row in payload["providers"] if row["label"] == "OpenCode Zen"]

    assert len(zen_rows) == 1
    assert zen_rows[0]["name"] == "opencode_zen"
    assert zen_rows[0]["configured"] is True



def test_settings_payload_exposes_canonical_assemblyai_transcription_model(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.assemblyai.api_key = "aai-test"
    config.models["assemblyai-speech"] = ModelConfig(
        display_name="AssemblyAI Speech",
        provider="assemblyai",
        model="universal-3-pro",
        capabilities=ModelCapabilities(transcription=True),
    )
    config.transcription.model_id = "assemblyai-speech"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = settings_payload()

    assert payload["transcription"]["model_id"] == "assemblyai-speech"
    assert set(payload["transcription"]) == {
        "enabled",
        "model_id",
        "language",
        "max_duration_sec",
        "max_upload_mb",
    }


def test_transcription_model_selection_uses_model_config_provider_credentials(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.openai.api_key = "${WEBUI_TRANSCRIPTION_KEY}"
    config.models["speech-env"] = ModelConfig(
        display_name="Speech env",
        provider="openai",
        model="whisper-1",
        capabilities=ModelCapabilities(transcription=True),
    )
    config.transcription.model_id = "speech-env"
    save_config(config, config_path)
    monkeypatch.setenv("WEBUI_TRANSCRIPTION_KEY", "sk-from-env")
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = settings_payload()

    assert payload["transcription"]["model_id"] == "speech-env"
    assert "provider" not in payload["transcription"]
    assert "model" not in payload["transcription"]
    assert "provider_configured" not in payload["transcription"]


def test_transcription_model_selection_keeps_payload_canonical(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.openai.api_key = "${WEBUI_TRANSCRIPTION_KEY}"
    config.models["speech-env"] = ModelConfig(
        display_name="Speech env",
        provider="openai",
        model="whisper-1",
        capabilities=ModelCapabilities(transcription=True),
    )
    config.transcription.model_id = "speech-env"
    save_config(config, config_path)
    monkeypatch.setenv("WEBUI_TRANSCRIPTION_KEY", "sk-from-env")
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    payload = settings_payload()

    assert payload["transcription"]["model_id"] == "speech-env"
    assert set(payload["transcription"]) == {
        "enabled",
        "model_id",
        "language",
        "max_duration_sec",
        "max_upload_mb",
    }


def test_openai_codex_remote_login_rejects_invalid_boolean(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    oauth_flows: WebUIOAuthFlowRegistry,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    with pytest.raises(WebUISettingsError, match="remote_browser"):
        login_oauth_provider(
            {"provider": ["openai-codex"], "remote_browser": ["sometimes"]},
            oauth_flows=oauth_flows,
        )
