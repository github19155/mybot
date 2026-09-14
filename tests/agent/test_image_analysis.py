from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from nanobot.agent.tools.image_analysis import ImageAnalysisTool, ImageAnalysisToolConfig
from nanobot.config.schema import Config
from nanobot.model_domain import ModelCapabilities, ModelConfig
from nanobot.providers.base import GenerationSettings, LLMResponse

PNG_BYTES = b"\x89PNG\r\n\x1a\nminimal"


class _VisionProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.generation = GenerationSettings(
            temperature=0.2,
            max_tokens=321,
            reasoning_effort="low",
        )

    async def chat_with_retry(self, **kwargs: object) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(content="image text", tool_calls=[])


def _model(*, vision: bool) -> ModelConfig:
    return ModelConfig(
        display_name="Vision" if vision else "Text",
        provider="openrouter",
        model="openai/gpt-4o" if vision else "openai/gpt-4o-mini",
        capabilities=ModelCapabilities(text=True, vision=vision),
    )


def _snapshot(provider: _VisionProvider):
    return SimpleNamespace(
        provider=provider,
        model="openai/gpt-4o",
        generation=provider.generation,
    )


@pytest.mark.asyncio
async def test_image_analysis_sends_local_image_as_data_url_and_model_id(
    tmp_path: Path,
) -> None:
    image = tmp_path / "chart.png"
    image.write_bytes(PNG_BYTES)
    provider = _VisionProvider()
    loader_calls: list[dict[str, object]] = []

    def loader(**kwargs: object):
        loader_calls.append(kwargs)
        return _snapshot(provider)

    tool = ImageAnalysisTool(
        workspace=tmp_path,
        config=ImageAnalysisToolConfig(model_id="vision"),
        models={"vision": _model(vision=True)},
        provider_snapshot_loader=loader,
    )

    result = await tool.execute(
        image_paths=[str(image)],
        prompt="Extract the labels",
    )

    assert result == "image text"
    assert loader_calls == [{"model_id": "vision"}]
    assert len(provider.calls) == 1
    messages = provider.calls[0]["messages"]
    assert isinstance(messages, list)
    content = messages[0]["content"]
    assert content[-1] == {"type": "text", "text": "Extract the labels"}
    image_block = content[0]
    assert image_block["type"] == "image_url"
    assert image_block["image_url"]["url"].startswith("data:image/png;base64,")
    assert image_block["_meta"]["path"] == str(image.resolve())
    assert provider.calls[0]["tools"] is None
    assert provider.calls[0]["model"] == "openai/gpt-4o"


@pytest.mark.asyncio
async def test_image_analysis_rejects_nonvision_model_before_runtime_load(
    tmp_path: Path,
) -> None:
    image = tmp_path / "chart.png"
    image.write_bytes(PNG_BYTES)
    provider = _VisionProvider()
    loader_calls: list[dict[str, object]] = []

    tool = ImageAnalysisTool(
        workspace=tmp_path,
        config=ImageAnalysisToolConfig(model_id="text"),
        models={"text": _model(vision=False)},
        provider_snapshot_loader=lambda **kwargs: (
            loader_calls.append(kwargs) or _snapshot(provider)
        ),
    )

    result = await tool.execute(image_paths=[str(image)], prompt="Read it")

    assert getattr(result, "is_error", False) is True
    assert "model_id 'text' does not support vision" in str(result)
    assert loader_calls == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_image_analysis_tool_argument_overrides_configured_model_id(
    tmp_path: Path,
) -> None:
    image = tmp_path / "chart.png"
    image.write_bytes(PNG_BYTES)
    provider = _VisionProvider()
    loader_calls: list[dict[str, object]] = []

    def loader(**kwargs: object):
        loader_calls.append(kwargs)
        return _snapshot(provider)

    tool = ImageAnalysisTool(
        workspace=tmp_path,
        config=ImageAnalysisToolConfig(model_id="vision-a"),
        models={
            "vision-a": _model(vision=True),
            "vision-b": _model(vision=True),
        },
        provider_snapshot_loader=loader,
    )

    result = await tool.execute(
        image_paths=[str(image)],
        prompt="Read it",
        model_id="vision-b",
    )

    assert result == "image text"
    assert loader_calls == [{"model_id": "vision-b"}]


@pytest.mark.asyncio
async def test_image_analysis_keeps_paths_inside_workspace(tmp_path: Path) -> None:
    image = tmp_path.parent / "outside.png"
    image.write_bytes(PNG_BYTES)
    provider = _VisionProvider()
    tool = ImageAnalysisTool(
        workspace=tmp_path,
        config=ImageAnalysisToolConfig(model_id="vision"),
        models={"vision": _model(vision=True)},
        provider_snapshot_loader=lambda **_kwargs: _snapshot(provider),
    )

    result = await tool.execute(image_paths=[str(image)], prompt="Read it")

    assert getattr(result, "is_error", False) is True
    assert "inside the workspace" in str(result)
    assert provider.calls == []


def test_image_analysis_config_accepts_model_id_and_rejects_legacy_model_preset() -> None:
    config = ImageAnalysisToolConfig.model_validate({"modelId": "vision"})
    assert config.model_id == "vision"

    with pytest.raises(ValidationError):
        ImageAnalysisToolConfig.model_validate({"modelPreset": "vision"})


def test_config_requires_vision_capability_for_image_analysis_model_id() -> None:
    with pytest.raises(ValueError, match="does not support vision"):
        Config.model_validate(
            {
                "models": {
                    "main": {
                        "displayName": "Main",
                        "provider": "openrouter",
                        "model": "openai/gpt-4o-mini",
                        "capabilities": {"text": True},
                    },
                    "image-reader": {
                        "displayName": "Reader",
                        "provider": "openrouter",
                        "model": "openai/gpt-4o-mini",
                        "capabilities": {"text": True, "vision": False},
                    },
                },
                "tools": {"imageAnalysis": {"modelId": "image-reader"}},
            }
        )
