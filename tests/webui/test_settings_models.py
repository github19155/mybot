from __future__ import annotations

from typing import Any

import pytest

from nanobot.config.schema import Config
from nanobot.model_domain import ModelCapabilities, ModelConfig
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
    config.models["fast"] = ModelConfig(
        display_name="Fast",
        provider="openrouter",
        model="openai/gpt-5.4",
        capabilities=ModelCapabilities(text=True),
    )

    agent_changed = update_agent_model_settings(
        config,
        {"model_id": ["fast"]},
        oauth_status=_oauth_status,
    )
    provider_changed, restart_required = update_provider_settings(
        config,
        {"provider": ["openrouter"], "api_key": ["sk-after"]},
    )
    payload = model_settings_payload(config, oauth_status=_oauth_status)

    assert agent_changed is True
    assert provider_changed is True
    assert restart_required is False
    assert config.agents.defaults.model_id == "fast"
    assert config.providers.openrouter.api_key == "sk-after"
    assert payload["agent"]["model_id"] == "fast"
    assert payload["agent"]["provider"] == "openrouter"
    assert payload["agent"]["model"] == "openai/gpt-5.4"
    assert "model_presets" not in payload


def test_model_settings_configure_vision_fallback_by_model_id() -> None:
    config = Config()
    config.models["vision"] = ModelConfig(
        display_name="Vision",
        provider="openrouter",
        model="openai/gpt-4o",
        capabilities=ModelCapabilities(vision=True),
    )

    changed = update_agent_model_settings(
        config,
        {"image_analysis_model_id": ["vision"]},
        oauth_status=_oauth_status,
    )
    payload = model_settings_payload(config, oauth_status=_oauth_status)

    assert changed is True
    assert config.tools.image_analysis.model_id == "vision"
    assert payload["image_analysis"]["model_id"] == "vision"
    vision = next(row for row in payload["models"] if row["model_id"] == "vision")
    assert vision["capabilities"]["vision"] is True


def test_model_settings_reject_nonvision_fallback() -> None:
    config = Config()
    config.models["text"] = ModelConfig(
        display_name="Text",
        provider="openai",
        model="gpt-4.1-mini",
        capabilities=ModelCapabilities(text=True),
    )

    with pytest.raises(WebUISettingsError, match="support vision"):
        update_agent_model_settings(
            config,
            {"image_analysis_model_id": ["text"]},
            oauth_status=_oauth_status,
        )


def test_model_delete_rejects_configured_image_fallback() -> None:
    config = Config()
    config.models["vision"] = ModelConfig(
        display_name="Vision",
        provider="openai",
        model="gpt-4o",
        capabilities=ModelCapabilities(vision=True),
    )
    config.tools.image_analysis.model_id = "vision"

    with pytest.raises(WebUISettingsError) as error:
        delete_model_configuration(config, {"model_id": ["vision"]})

    assert error.value.status == 409


def test_model_settings_payload_exposes_image_generation_capability() -> None:
    config = Config()
    config.models["image"] = ModelConfig(
        display_name="Image",
        provider="openai",
        model="gpt-image-1",
        capabilities=ModelCapabilities(image_generation=True),
    )

    payload = model_settings_payload(config, oauth_status=_oauth_status)
    image_model = next(row for row in payload["models"] if row["model_id"] == "image")

    assert image_model["capabilities"]["image_generation"] is True


def test_model_configuration_create_and_update_image_generation_capability() -> None:
    config = Config()

    model_id = create_model_configuration(
        config,
        {
            "model_id": ["image"],
            "display_name": ["Image"],
            "model": ["gpt-image-1"],
            "provider": ["openai"],
            "capabilities": ["{\"image_generation\":true}"],
        },
        oauth_status=_oauth_status,
    )

    assert model_id == "image"
    assert config.models["image"].capabilities.image_generation is True

    changed = update_model_configuration(
        config,
        {
            "model_id": ["image"],
            "display_name": ["Image v2"],
            "capabilities": ["{\"image_generation\":false}"],
        },
        oauth_status=_oauth_status,
    )

    assert changed is True
    assert config.models["image"].display_name == "Image v2"
    assert config.models["image"].capabilities.image_generation is False


def test_model_configuration_rejects_model_id_rename() -> None:
    config = Config()

    with pytest.raises(WebUISettingsError, match="cannot be renamed"):
        update_model_configuration(
            config,
            {"model_id": ["main"], "new_name": ["renamed"]},
            oauth_status=_oauth_status,
        )
