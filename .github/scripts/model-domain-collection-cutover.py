from __future__ import annotations

from pathlib import Path


def edit(path: str, replacements: tuple[tuple[str, str], ...]) -> None:
    target = Path(path)
    text = target.read_text()
    for old, new in replacements:
        text = text.replace(old, new)
    target.write_text(text)


# Remove stale production imports first. These are hard schema-cut consumers,
# not compatibility aliases; runtime semantics are cleaned up in the next pass.
edit(
    "nanobot/cli/onboard.py",
    (
        (
            "from nanobot.config.schema import Config, ModelPresetConfig",
            "from nanobot.config.schema import Config\nfrom nanobot.model_domain import ModelConfig",
        ),
        ("ModelPresetConfig(", "ModelConfig("),
    ),
)
edit(
    "nanobot/webui/settings_models.py",
    (
        ("    ModelPresetConfig,\n", ""),
        (
            "from nanobot.providers.image_generation import get_image_gen_provider",
            "from nanobot.model_domain import ModelConfig\nfrom nanobot.providers.image_generation import get_image_gen_provider",
        ),
        ("ModelPresetConfig(", "ModelConfig("),
    ),
)
edit(
    "nanobot/agent/model_presets.py",
    (
        (
            "from nanobot.config.schema import Config, ModelPresetConfig",
            "from nanobot.config.schema import Config\nfrom nanobot.model_domain import ModelConfig",
        ),
        ("ModelPresetConfig", "ModelConfig"),
    ),
)
edit(
    "nanobot/webui/settings_capabilities.py",
    (("    resolve_transcription_provider,\n", ""),),
)

# Tests must compile against the canonical domain after the schema cut. Their
# behavioral assertions are migrated separately; this pass deliberately does
# not restore production compatibility symbols.
for path in (
    "tests/agent/tools/test_self_tool.py",
    "tests/cli/test_tui_launcher.py",
    "tests/command/test_model_command.py",
    "tests/command/test_skill_command.py",
    "tests/webui/test_settings_api.py",
    "tests/webui/test_settings_models.py",
):
    target = Path(path)
    text = target.read_text()
    text = text.replace(
        "from nanobot.config.schema import Config, ModelPresetConfig",
        "from nanobot.config.schema import Config\nfrom nanobot.model_domain import ModelConfig",
    )
    text = text.replace(
        "from nanobot.config.schema import ModelPresetConfig",
        "from nanobot.model_domain import ModelConfig",
    )
    text = text.replace("ModelPresetConfig(", "ModelConfig(")
    target.write_text(text)

for path in (
    "tests/session/test_session_store.py",
    "tests/webui/test_session_list_index.py",
    "tests/webui/test_session_projection.py",
    "tests/command/test_model_command.py",
):
    target = Path(path)
    text = target.read_text()
    text = text.replace("SESSION_MODEL_PRESET_METADATA_KEY", "SESSION_MODEL_ID_METADATA_KEY")
    text = text.replace("model_preset_from_metadata", "model_id_from_metadata")
    target.write_text(text)
