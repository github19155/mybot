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

# The canonical image/transcription selectors are model IDs. Provider/model are
# derived from Config.models and are never writable consumer identity.
edit(
    "nanobot/webui/settings_capabilities.py",
    (
        ("    resolve_transcription_provider,\n", ""),
        (
            '''    provider_name = query_first(query, "provider")\n    if provider_name is not None:\n        provider_name = provider_name.strip().lower()\n        if not provider_name:\n            raise WebUISettingsError("image generation provider is required")\n        if get_image_gen_provider(provider_name) is None:\n            raise WebUISettingsError("unknown image generation provider")\n        if image_config.provider != provider_name:\n            image_config.provider = provider_name\n            changed = True\n''',
            '''    model_id = query_first_alias(query, "model_id", "modelId")\n    if model_id is not None:\n        selected = model_id.strip() or None\n        if selected is not None:\n            model = config.models.get(selected)\n            if model is None:\n                raise WebUISettingsError("unknown image generation model_id")\n            if not model.capabilities.image_generation:\n                raise WebUISettingsError("selected model does not support image generation")\n        if image_config.model_id != selected:\n            image_config.model_id = selected\n            changed = True\n''',
        ),
        (
            '''    model = query_first(query, "model")\n    if model is not None:\n        model = model.strip()\n        if not model:\n            raise WebUISettingsError("image generation model is required")\n        if len(model) > 200:\n            raise WebUISettingsError("image generation model is too long")\n        if image_config.model != model:\n            image_config.model = model\n            changed = True\n\n''',
            "",
        ),
        (
            '''    if image_config.enabled:\n        selected_provider = next(\n            (\n                provider\n                for provider in _image_generation_provider_rows(\n                    config,\n                    oauth_status=oauth_status,\n                )\n                if provider["name"] == image_config.provider\n            ),\n            None,\n        )\n        if not selected_provider or not selected_provider["configured"]:\n            raise WebUISettingsError("image generation provider is not configured")\n''',
            '''    if image_config.enabled:\n        if image_config.model_id is None:\n            raise WebUISettingsError("image generation model_id is required")\n        image_model = config.models.get(image_config.model_id)\n        if image_model is None or not image_model.capabilities.image_generation:\n            raise WebUISettingsError("image generation model_id is invalid")\n        selected_provider = next(\n            (\n                provider\n                for provider in _image_generation_provider_rows(\n                    config,\n                    oauth_status=oauth_status,\n                )\n                if provider["name"] == image_model.provider\n            ),\n            None,\n        )\n        if not selected_provider or not selected_provider["configured"]:\n            raise WebUISettingsError("image generation provider is not configured")\n''',
        ),
        (
            '''    provider = query_first(query, "provider")\n    if provider is not None:\n        provider = provider.strip().lower()\n        provider_spec = resolve_transcription_provider(provider)\n        if provider_spec is None:\n            raise WebUISettingsError("unknown transcription provider")\n        provider = provider_spec.name\n        if transcription.provider != provider:\n            transcription.provider = provider\n            changed = True\n\n    model = query_first(query, "model")\n    if model is not None:\n        model = model.strip() or None\n        if model is not None and len(model) > 200:\n            raise WebUISettingsError("transcription model is too long")\n        if transcription.model != model:\n            transcription.model = model\n            changed = True\n''',
            '''    model_id = query_first_alias(query, "model_id", "modelId")\n    if model_id is not None:\n        selected = model_id.strip() or None\n        if selected is not None:\n            model = config.models.get(selected)\n            if model is None:\n                raise WebUISettingsError("unknown transcription model_id")\n            if not model.capabilities.transcription:\n                raise WebUISettingsError("selected model does not support transcription")\n        if transcription.model_id != selected:\n            transcription.model_id = selected\n            changed = True\n''',
        ),
    ),
)

# Model Management no longer owns runtime construction. Subagent/Dream resolver
# wiring remains legitimate and is intentionally untouched.
edit(
    "nanobot/agent/loop.py",
    (
        (
            '''            ModelManagement(\n                model_management_config,\n                runtime_resolver=self.runtime_resolver,\n                invalidate=self.invalidate_runtime_config,\n            )''',
            '''            ModelManagement(\n                model_management_config,\n                invalidate=self.invalidate_runtime_config,\n            )''',
        ),
    ),
)

# The first cleanup helper may be re-applied to a branch that already contains
# this ToolContext wiring. Keep the candidate idempotent rather than generating
# a duplicate keyword argument.
edit(
    "nanobot/agent/tools/subagent.py",
    (("            models=models,\n            models=models,\n", "            models=models,\n"),),
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

# Keep the import block Ruff-clean after the temporary test import cutover.
edit(
    "tests/webui/test_settings_api.py",
    (
        (
            "from nanobot.config.schema import Config\nfrom nanobot.model_domain import ModelConfig\nfrom nanobot.llm_usage import get_llm_usage_store\nfrom nanobot.llm_usage.models import LLMCallRecord",
            "from nanobot.config.schema import Config\nfrom nanobot.llm_usage import get_llm_usage_store\nfrom nanobot.llm_usage.models import LLMCallRecord\nfrom nanobot.model_domain import ModelConfig",
        ),
    ),
)

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
