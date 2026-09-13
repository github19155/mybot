import pytest

from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config
from nanobot.webui import settings_api
from nanobot.webui.settings_contracts import WebUISettingsError


def test_webui_model_create_uses_core_and_persists(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)

    payload = settings_api.create_model_configuration(
        {"name": ["fast"], "model": ["example/model"], "provider": ["auto"]},
        config_path=config_path,
    )

    assert payload["created_model_preset"] == "fast"
    assert load_config(config_path).model_presets["fast"].model == "example/model"


def test_webui_maps_core_model_error_to_http_error(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)

    with pytest.raises(WebUISettingsError) as error:
        settings_api.create_model_configuration(
            {"name": ["default"], "model": ["example/model"], "provider": ["auto"]},
            config_path=config_path,
        )

    assert error.value.status == 400
    assert error.value.message == "configuration name is reserved"
