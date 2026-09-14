import pytest

from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config
from nanobot.webui import settings_api
from nanobot.webui.settings_contracts import WebUISettingsError


def test_webui_model_create_uses_core_and_persists(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)

    payload = settings_api.create_model_configuration(
        {
            "model_id": ["fast"],
            "display_name": ["Fast"],
            "model": ["gpt-4.1-mini"],
            "provider": ["openai"],
        },
        config_path=config_path,
    )

    assert payload["created_model_id"] == "fast"
    assert payload["models"]
    assert "model_presets" not in payload
    saved = load_config(config_path)
    assert saved.models["fast"].display_name == "Fast"
    assert saved.models["fast"].provider == "openai"
    assert saved.models["fast"].model == "gpt-4.1-mini"


def test_webui_maps_core_model_error_to_http_error(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)

    with pytest.raises(WebUISettingsError) as error:
        settings_api.create_model_configuration(
            {
                "model_id": ["INVALID ID"],
                "display_name": ["Invalid"],
                "model": ["gpt-4.1"],
                "provider": ["openai"],
            },
            config_path=config_path,
        )

    assert error.value.status == 400
    assert "model_id must match" in error.value.message
