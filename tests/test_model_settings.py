from nanobot.config.schema import Config
from nanobot.model_settings import (
    ModelSettingsError,
    create_model_configuration,
    update_model_configuration,
)


def test_core_model_configuration_create_and_update() -> None:
    config = Config()

    name = create_model_configuration(
        config,
        {"name": ["fast"], "model": ["example/model"], "provider": ["auto"]},
    )

    assert name == "fast"
    assert config.model_presets["fast"].model == "example/model"
    assert update_model_configuration(
        config,
        {"name": ["fast"], "model": ["example/model-v2"]},
    )
    assert config.model_presets["fast"].model == "example/model-v2"


def test_core_model_configuration_error_is_transport_neutral() -> None:
    config = Config()

    try:
        create_model_configuration(
            config,
            {"name": ["default"], "model": ["example/model"], "provider": ["auto"]},
        )
    except ModelSettingsError as exc:
        assert exc.message == "configuration name is reserved"
        assert exc.status == 400
    else:
        raise AssertionError("expected ModelSettingsError")
