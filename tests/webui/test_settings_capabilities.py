from __future__ import annotations

from typing import Any

from nanobot.config.schema import Config
from nanobot.model_domain import ModelCapabilities, ModelConfig
from nanobot.webui.settings_capabilities import (
    capability_settings_payload,
    update_api_settings,
    update_image_generation_settings,
    update_network_safety_settings,
    update_transcription_settings,
    update_web_search_settings,
)


def _oauth_status(_spec: Any) -> dict[str, Any]:
    return {"configured": False}


def test_capability_domain_updates_representative_settings() -> None:
    config = Config()
    config.providers.openrouter.api_key = "sk-test"
    config.models["image"] = ModelConfig(
        display_name="Image",
        provider="openrouter",
        model="google/gemini-2.5-flash-image",
        capabilities=ModelCapabilities(image_generation=True),
    )
    config.models["speech"] = ModelConfig(
        display_name="Speech",
        provider="openrouter",
        model="openai/whisper-large-v3",
        capabilities=ModelCapabilities(transcription=True),
    )

    web_changed, web_restart = update_web_search_settings(
        config,
        {
            "provider": ["duckduckgo"],
            "max_results": ["7"],
            "use_jina_reader": ["false"],
        },
    )
    update_api_settings(
        config,
        {"host": ["127.0.0.2"], "port": ["8900"], "timeout": ["90"]},
    )
    image_changed = update_image_generation_settings(
        config,
        {"enabled": ["true"], "model_id": ["image"]},
        oauth_status=_oauth_status,
    )
    transcription_changed = update_transcription_settings(
        config,
        {"model_id": ["speech"]},
    )
    network_changed, access_mode = update_network_safety_settings(
        config,
        {
            "webui_allow_local_service_access": ["false"],
            "webui_default_access_mode": ["restricted"],
        },
    )
    payload = capability_settings_payload(config, oauth_status=_oauth_status)

    assert (web_changed, web_restart) == (True, True)
    assert image_changed is True
    assert transcription_changed is True
    assert (network_changed, access_mode) == (True, "default")
    assert payload["web_search"]["max_results"] == 7
    assert payload["api"]["host"] == "127.0.0.2"
    assert payload["api"]["port"] == 8900
    assert payload["image_generation"]["model_id"] == "image"
    assert payload["image_generation"]["provider"] == "openrouter"
    assert payload["image_generation"]["model"] == "google/gemini-2.5-flash-image"
    assert payload["transcription"]["model_id"] == "speech"
    assert payload["transcription"]["provider"] == "openrouter"
    assert payload["transcription"]["model"] == "openai/whisper-large-v3"


def test_tavily_web_search_keeps_custom_base_url() -> None:
    config = Config()

    changed, _restart = update_web_search_settings(
        config,
        {
            "provider": ["tavily"],
            "api_key": ["tavily-key"],
            "base_url": ["https://tavily.example"],
        },
    )

    assert changed is True
    assert config.tools.web.search.base_url == "https://tavily.example"
