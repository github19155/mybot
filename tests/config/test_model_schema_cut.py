from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from nanobot.config.schema import (
    AgentDefaults,
    ChannelsConfig,
    Config,
    DreamConfig,
    SubagentRoleConfig,
    SystemPromptOverrideConfig,
    TranscriptionConfig,
)


def _model() -> dict[str, object]:
    return {
        "displayName": "Main",
        "provider": "anthropic",
        "model": "claude-opus-4-5",
        "capabilities": {"text": True},
    }


def _config(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "models": {"main": _model()},
        "agents": {"defaults": {"modelId": "main"}},
    }
    value.update(overrides)
    return value


def test_models_key_must_be_canonical_model_id() -> None:
    with pytest.raises(ValidationError, match="models key 'Bad ID'"):
        Config.model_validate(
            {
                "models": {"Bad ID": _model()},
                "agents": {"defaults": {"modelId": "Bad ID"}},
            }
        )


def test_agents_default_must_reference_real_model() -> None:
    with pytest.raises(
        ValidationError,
        match=r"agents\.defaults\.model_id references unknown model_id 'missing'",
    ):
        Config.model_validate(
            {
                "models": {"main": _model()},
                "agents": {"defaults": {"modelId": "missing"}},
            }
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"agents": {"defaults": {"modelId": "main", "dream": {"modelId": "missing"}}}},
            r"dream\.model_id references unknown model_id 'missing'",
        ),
        (
            {
                "agents": {
                    "defaults": {
                        "modelId": "main",
                        "dream": {"fallbackModelId": "missing"},
                    }
                }
            },
            r"dream\.fallback_model_id references unknown model_id 'missing'",
        ),
        (
            {"subagentRoles": {"general": {"modelId": "missing"}}},
            r"subagent_roles\.general\.model_id references unknown model_id 'missing'",
        ),
        (
            {"transcription": {"modelId": "missing"}},
            r"transcription\.model_id references unknown model_id 'missing'",
        ),
        (
            {"systemPromptOverrides": [{"prompt": "x", "modelIds": ["missing"]}]},
            r"system_prompt_overrides\.model_ids references unknown model_id 'missing'",
        ),
    ],
)
def test_all_consumer_model_references_must_exist(
    payload: dict[str, object], message: str
) -> None:
    data = _config()
    data.update(payload)
    with pytest.raises(ValidationError, match=message):
        Config.model_validate(data)


def test_valid_consumer_references_are_accepted() -> None:
    config = Config.model_validate(
        _config(
            agents={
                "defaults": {
                    "modelId": "main",
                    "dream": {"modelId": "main", "fallbackModelId": "main"},
                },
            },
            subagentRoles={"general": {"modelId": "main"}},
            transcription={"modelId": "main"},
            systemPromptOverrides=[{"prompt": "bound", "modelIds": ["main"]}],
        )
    )
    assert config.agents.defaults.model_id == "main"
    assert config.agents.defaults.dream.model_id == "main"
    assert config.subagent_roles["general"].model_id == "main"
    assert config.transcription.model_id == "main"
    assert config.system_prompt_overrides[0].model_ids == ["main"]


def test_removed_fields_are_absent_from_schema() -> None:
    assert "model_presets" not in Config.model_fields
    assert {
        "model_preset",
        "model",
        "provider",
        "supports_vision",
        "max_tokens",
        "context_window_tokens",
        "temperature",
        "reasoning_effort",
    }.isdisjoint(AgentDefaults.model_fields)
    assert {"model", "model_preset"}.isdisjoint(SubagentRoleConfig.model_fields)
    assert {"model_override", "fallback_preset"}.isdisjoint(DreamConfig.model_fields)
    assert {"provider", "model"}.isdisjoint(TranscriptionConfig.model_fields)
    assert {"transcription_provider", "transcription_language"}.isdisjoint(
        ChannelsConfig.model_fields
    )
    assert "models" not in SystemPromptOverrideConfig.model_fields
    assert "model_ids" in SystemPromptOverrideConfig.model_fields


@pytest.mark.parametrize(
    ("model_type", "payload"),
    [
        (AgentDefaults, {"modelId": "main", "provider": "auto"}),
        (AgentDefaults, {"modelId": "main", "model": "raw-model"}),
        (AgentDefaults, {"modelId": "main", "modelPreset": "legacy"}),
        (AgentDefaults, {"modelId": "main", "temperature": 0.2}),
        (DreamConfig, {"modelOverride": "main"}),
        (DreamConfig, {"fallbackPreset": "main"}),
        (SubagentRoleConfig, {"model": "raw-model"}),
        (SubagentRoleConfig, {"modelPreset": "legacy"}),
        (TranscriptionConfig, {"provider": "groq"}),
        (TranscriptionConfig, {"model": "whisper-large-v3"}),
        (SystemPromptOverrideConfig, {"prompt": "x", "models": ["main"]}),
    ],
)
def test_removed_consumer_fields_are_rejected(
    model_type: type[BaseModel], payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        model_type.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"transcriptionProvider": "groq"},
        {"transcription_provider": "groq"},
        {"transcriptionLanguage": "en"},
        {"transcription_language": "en"},
    ],
)
def test_config_rejects_removed_channel_transcription_fields(
    payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(_config(channels=payload))


def test_root_rejects_model_presets_in_both_old_spellings() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(_config(model_presets={"legacy": {"model": "x"}}))
    with pytest.raises(ValidationError):
        Config.model_validate(_config(modelPresets={"legacy": {"model": "x"}}))


def test_config_serialization_contains_only_new_model_schema() -> None:
    dumped: dict[str, Any] = Config.model_validate(_config()).model_dump(by_alias=True)
    assert "models" in dumped
    assert "modelPresets" not in dumped
    defaults = dumped["agents"]["defaults"]
    assert defaults["modelId"] == "main"
    for legacy in (
        "modelPreset",
        "model",
        "provider",
        "supportsVision",
        "maxTokens",
        "contextWindowTokens",
        "temperature",
        "reasoningEffort",
    ):
        assert legacy not in defaults
