from __future__ import annotations

from typing import Any

import pytest

from nanobot.config.schema import Config, ModelPresetConfig
from nanobot.webui.settings_contracts import WebUISettingsError
from nanobot.webui.settings_models import (
    create_model_configuration,
    delete_model_configuration,
    model_settings_payload,
    update_agent_model_settings,
    update_model_configuration,
    update_provider_settings,
)


def _oauth_status(_spec: Any) -> dict[str, Any]:
    return {
        "configured": False,
        "account": None,
        "expires_at": None,
        "login_supported": True,
    }


def test_model_domain_owns_dto_and_config_updates() -> None:
    config = Config()
    config.providers.openrouter.api_key = "sk-before"

    agent_changed = update_agent_model_settings(
        config,
        {
            "model": ["openai/gpt-5.4"],
            "provider": ["openrouter"],
            "context_window_tokens": ["200000"],
        },
        oauth_status=_oauth_status,
    )
    provider_changed, restart_required = update_provider_settings(
        config,
        {
            "provider": ["openrouter"],
            "api_key": ["sk-after"],
        },
    )
    payload = model_settings_payload(config, oauth_status=_oauth_status)

    assert agent_changed is True
    assert provider_changed is True
    assert restart_required is False
    assert config.agents.defaults.model == "openai/gpt-5.4"
    assert config.agents.defaults.provider == "openrouter"
    assert config.agents.defaults.context_window_tokens == 200_000
    assert config.providers.openrouter.api_key == "sk-after"
    assert payload["agent"]["model"] == "openai/gpt-5.4"


def test_model_settings_configure_vision_fallback() -> None:
    config = Config()
    config.model_presets["vision"] = ModelPresetConfig(
        model="openai/gpt-4o",
        provider="openrouter",
        supports_vision=True,
    )

    changed = update_agent_model_settings(
        config,
        {"image_analysis_model_preset": ["vision"]},
        oauth_status=_oauth_status,
    )
    payload = model_settings_payload(config, oauth_status=_oauth_status)

    assert changed is True
    assert config.tools.image_analysis.model_preset == "vision"
    assert payload["image_analysis"]["model_preset"] == "vision"
    assert payload["model_presets"][-1]["supports_vision"] is True


def test_model_settings_reject_nonvision_fallback() -> None:
    config = Config()
    config.model_presets["text"] = ModelPresetConfig(model="openai/gpt-4o-mini")

    with pytest.raises(WebUISettingsError, match="supportsVision=true"):
        update_agent_model_settings(
            config,
            {"image_analysis_model_preset": ["text"]},
            oauth_status=_oauth_status,
        )


def test_model_preset_delete_rejects_configured_image_fallback() -> None:
    config = Config()
    config.model_presets["vision"] = ModelPresetConfig(
        model="openai/gpt-4o",
        supports_vision=True,
    )
    config.tools.image_analysis.model_preset = "vision"

    with pytest.raises(WebUISettingsError, match="image analysis model preset"):
        delete_model_configuration(config, {"name": ["vision"]})


def test_model_settings_payload_exposes_image_generation_capability() -> None:
    config = Config()
    config.model_presets["image"] = ModelPresetConfig(
        model="openai/gpt-5.4-image-2",
        provider="auto",
        supports_image_generation=True,
    )

    payload = model_settings_payload(config, oauth_status=_oauth_status)
    image_preset = next(row for row in payload["model_presets"] if row["name"] == "image")

    assert image_preset["supports_image_generation"] is True


def test_model_configuration_create_and_update_image_generation_capability() -> None:
    config = Config()

    name = create_model_configuration(
        config,
        {
            "name": ["image"],
            "model": ["openai/gpt-5.4-image-2"],
            "provider": ["auto"],
            "supportsImageGeneration": ["true"],
        },
        oauth_status=_oauth_status,
    )

    assert name == "image"
    assert config.model_presets["image"].supports_image_generation is True

    changed = update_model_configuration(
        config,
        {
            "name": ["image"],
            "supports_image_generation": ["false"],
        },
        oauth_status=_oauth_status,
    )

    assert changed is True
    assert config.model_presets["image"].supports_image_generation is False
