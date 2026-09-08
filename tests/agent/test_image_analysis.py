from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.image_analysis import ImageAnalysisTool, ImageAnalysisToolConfig
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import Config, ModelPresetConfig
from nanobot.providers.base import GenerationSettings, LLMResponse
from nanobot.providers.factory import ProviderSnapshot
from nanobot.utils.llm_runtime import LLMRuntime

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


def _snapshot(
    provider: _VisionProvider,
    *,
    supports_vision: bool = True,
) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider=provider,  # type: ignore[arg-type]
        model="vision-model",
        context_window_tokens=65_536,
        signature=("test", "vision-model", supports_vision),
        generation=provider.generation,
        model_preset="vision",
        supports_vision=supports_vision,
    )


@pytest.mark.asyncio
async def test_image_analysis_sends_local_image_as_data_url(tmp_path: Path) -> None:
    image = tmp_path / "chart.png"
    image.write_bytes(PNG_BYTES)
    provider = _VisionProvider()
    tool = ImageAnalysisTool(
        workspace=tmp_path,
        config=ImageAnalysisToolConfig(model_preset="vision"),
        provider_snapshot_loader=lambda **_kwargs: _snapshot(provider),
    )

    result = await tool.execute(
        image_paths=[str(image)],
        prompt="Extract the labels",
    )

    assert result == "image text"
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
    assert provider.calls[0]["model"] == "vision-model"


@pytest.mark.asyncio
async def test_image_analysis_rejects_unmarked_vision_preset(tmp_path: Path) -> None:
    image = tmp_path / "chart.png"
    image.write_bytes(PNG_BYTES)
    provider = _VisionProvider()
    tool = ImageAnalysisTool(
        workspace=tmp_path,
        config=ImageAnalysisToolConfig(model_preset="text"),
        provider_snapshot_loader=lambda **_kwargs: _snapshot(
            provider,
            supports_vision=False,
        ),
    )

    result = await tool.execute(image_paths=[str(image)], prompt="Read it")

    assert getattr(result, "is_error", False) is True
    assert "supportsVision=true" in str(result)
    assert provider.calls == []


@pytest.mark.asyncio
async def test_image_analysis_keeps_paths_inside_workspace(tmp_path: Path) -> None:
    image = tmp_path.parent / "outside.png"
    image.write_bytes(PNG_BYTES)
    provider = _VisionProvider()
    tool = ImageAnalysisTool(
        workspace=tmp_path,
        config=ImageAnalysisToolConfig(model_preset="vision"),
        provider_snapshot_loader=lambda **_kwargs: _snapshot(provider),
    )

    result = await tool.execute(image_paths=[str(image)], prompt="Read it")

    assert getattr(result, "is_error", False) is True
    assert "inside the workspace" in str(result)
    assert provider.calls == []


def test_text_runtime_gets_image_tool_but_native_vision_runtime_does_not() -> None:
    registry = ToolRegistry()
    registry.register(
        ImageAnalysisTool(
            workspace=Path("."),
            config=ImageAnalysisToolConfig(model_preset="vision"),
        )
    )
    text_runtime = LLMRuntime.capture(
        MagicMock(),
        "text-model",
        context_window_tokens=65_536,
        supports_vision=False,
    )
    vision_runtime = LLMRuntime.capture(
        MagicMock(),
        "vision-model",
        context_window_tokens=65_536,
        supports_vision=True,
    )

    assert AgentLoop._tools_for_runtime(registry, text_runtime) is registry
    assert AgentLoop._tools_for_runtime(registry, vision_runtime).has("image_analyze") is False
    assert registry.has("image_analyze") is True


def test_image_analysis_config_and_vision_preset_accept_camel_case() -> None:
    config = Config.model_validate(
        {
            "agents": {"defaults": {"supportsVision": True}},
            "modelPresets": {
                "vision": {
                    "model": "openai/gpt-4o",
                    "supportsVision": True,
                }
            },
            "tools": {"imageAnalysis": {"modelPreset": "vision"}},
        }
    )

    assert config.agents.defaults.supports_vision is True
    assert config.model_presets["vision"].supports_vision is True
    assert config.tools.image_analysis.model_preset == "vision"


def test_config_rejects_nonvision_image_analysis_preset() -> None:
    with pytest.raises(ValueError, match="must be marked supports_vision"):
        Config.model_validate(
            {
                "modelPresets": {
                    "text": {
                        "model": "openai/gpt-4o-mini",
                        "supportsVision": False,
                    }
                },
                "tools": {"imageAnalysis": {"modelPreset": "text"}},
            }
        )


def test_runtime_snapshot_carries_vision_capability() -> None:
    provider = _VisionProvider()
    snapshot = _snapshot(provider)
    runtime = LLMRuntime(
        provider=snapshot.provider,
        model=snapshot.model,
        generation=snapshot.generation or GenerationSettings(),
        context_window_tokens=snapshot.context_window_tokens,
        model_preset=snapshot.model_preset,
        snapshot_signature=snapshot.signature,
        supports_vision=snapshot.supports_vision,
    )

    assert runtime.supports_vision is True
    assert ModelPresetConfig(model="vision-model", supports_vision=True).supports_vision is True
