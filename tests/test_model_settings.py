from nanobot.config.schema import Config
from nanobot.model_settings import (
    ModelSettingsError,
    create_model_configuration,
    update_model_configuration,
)


def test_core_model_configuration_create_and_update() -> None:
    config = Config()

    model_id = create_model_configuration(
        config,
        {
            "model_id": ["fast"],
            "display_name": ["Fast"],
            "model": ["example/model"],
            "provider": ["openai"],
        },
    )

    assert model_id == "fast"
    assert config.models["fast"].model == "example/model"
    assert update_model_configuration(
        config,
        {
            "model_id": ["fast"],
            "display_name": ["Fast"],
            "model": ["example/model-v2"],
        },
    )
    assert config.models["fast"].model == "example/model-v2"


def test_core_model_configuration_error_is_transport_neutral() -> None:
    config = Config()

    try:
        create_model_configuration(
            config,
            {
                "model_id": ["Fast Model"],
                "display_name": ["Fast Model"],
                "model": ["example/model"],
                "provider": ["openai"],
            },
        )
    except ModelSettingsError as exc:
        assert exc.message == "model_id must match [a-z][a-z0-9_-]{0,63}"
        assert exc.status == 400
    else:
        raise AssertionError("expected ModelSettingsError")