from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from nanobot.agent.tools.image_generation import ImageGenerationTool, ImageGenerationToolConfig
from nanobot.config.loader import set_config_path
from nanobot.config.schema import Config, ProviderConfig
from nanobot.model_domain import ModelCapabilities, ModelConfig
from nanobot.providers.image_generation import GeneratedImageResponse

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
    b"\x00\x00\x00\x0bIDATx\xdacd\xfc\xff\x1f\x00\x03\x03"
    b"\x02\x00\xef\xbf\xa7\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)
PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class FakeImageClient:
    instances: list["FakeImageClient"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        self.instances.append(self)

    async def generate(self, **kwargs: Any) -> GeneratedImageResponse:
        self.calls.append(kwargs)
        return GeneratedImageResponse(images=[PNG_DATA_URL], content="", raw={})


def _image_model(
    *,
    provider: str = "openrouter",
    upstream: str = "openai/gpt-5.4-image-2",
    capable: bool = True,
) -> ModelConfig:
    return ModelConfig(
        display_name="Image",
        provider=provider,
        model=upstream,
        capabilities=ModelCapabilities(image_generation=capable),
    )


@pytest.mark.asyncio
async def test_generate_image_tool_stores_canonical_provenance_and_source_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_config_path(tmp_path / "config.json")
    FakeImageClient.instances = []
    monkeypatch.setattr(
        "nanobot.agent.tools.image_generation.get_image_gen_provider",
        lambda name: FakeImageClient if name == "openrouter" else None,
    )
    ref = tmp_path / "ref.png"
    ref.write_bytes(PNG_BYTES)
    tool = ImageGenerationTool(
        workspace=tmp_path,
        config=ImageGenerationToolConfig(
            enabled=True,
            model_id="image-gen",
            max_images_per_turn=2,
        ),
        models={"image-gen": _image_model()},
        provider_configs={"openrouter": ProviderConfig(api_key="sk-or-test")},
    )

    result = await tool.execute(
        prompt="make this blue",
        reference_images=["ref.png"],
        aspect_ratio="16:9",
        image_size="2K",
        count=2,
    )

    payload = json.loads(result)
    artifacts = payload["artifacts"]
    assert len(artifacts) == 2
    assert Path(artifacts[0]["path"]).is_file()
    assert artifacts[0]["source_images"] == [str(ref.resolve())]
    assert artifacts[0]["provider"] == "openrouter"
    assert artifacts[0]["model"] == "openai/gpt-5.4-image-2"

    fake = FakeImageClient.instances[0]
    assert fake.kwargs["api_key"] == "sk-or-test"
    assert len(fake.calls) == 2
    assert fake.calls[0]["model"] == "openai/gpt-5.4-image-2"
    assert fake.calls[0]["aspect_ratio"] == "16:9"
    assert fake.calls[0]["image_size"] == "2K"


@pytest.mark.asyncio
async def test_generate_image_tool_rejects_non_image_generation_model(tmp_path: Path) -> None:
    tool = ImageGenerationTool(
        workspace=tmp_path,
        config=ImageGenerationToolConfig(enabled=True, model_id="text"),
        models={"text": _image_model(capable=False)},
    )

    result = await tool.execute(prompt="draw")

    assert "model_id 'text' does not support image_generation" in result


@pytest.mark.asyncio
async def test_generate_image_tool_uses_provider_identity_not_upstream_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_config_path(tmp_path / "config.json")
    FakeImageClient.instances = []
    adapter_lookups: list[str] = []

    def get_adapter(name: str):
        adapter_lookups.append(name)
        return FakeImageClient if name == "cpa" else None

    monkeypatch.setattr(
        "nanobot.agent.tools.image_generation.get_image_gen_provider",
        get_adapter,
    )
    tool = ImageGenerationTool(
        workspace=tmp_path,
        config=ImageGenerationToolConfig(enabled=True, model_id="image-cpa"),
        models={
            "image-cpa": _image_model(
                provider="cpa",
                upstream="openai/gpt-image",
            )
        },
        provider_configs={"cpa": ProviderConfig(api_key="cpa-key")},
    )

    result = await tool.execute(prompt="draw a poster")

    payload = json.loads(result)
    assert adapter_lookups == ["cpa"]
    assert FakeImageClient.instances[0].kwargs["api_key"] == "cpa-key"
    assert FakeImageClient.instances[0].calls[0]["model"] == "openai/gpt-image"
    assert payload["artifacts"][0]["provider"] == "cpa"
    assert payload["artifacts"][0]["model"] == "openai/gpt-image"


@pytest.mark.asyncio
async def test_generate_image_tool_errors_when_provider_has_no_adapter(tmp_path: Path) -> None:
    tool = ImageGenerationTool(
        workspace=tmp_path,
        config=ImageGenerationToolConfig(enabled=True, model_id="image-cpa"),
        models={
            "image-cpa": _image_model(
                provider="cpa",
                upstream="openai/gpt-image",
            )
        },
        provider_configs={"cpa": ProviderConfig(api_key="cpa-key")},
    )

    result = await tool.execute(prompt="draw")

    assert "provider 'cpa' has no image-generation adapter" in result
    assert "model_id 'image-cpa'" in result


def test_image_generation_tool_passes_provider_proxy_to_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeImageClient.instances = []
    monkeypatch.setattr(
        "nanobot.agent.tools.image_generation.get_image_gen_provider",
        lambda name: FakeImageClient if name == "openai_codex" else None,
    )
    proxy = "http://127.0.0.1:23458"
    tool = ImageGenerationTool(
        workspace=tmp_path,
        config=ImageGenerationToolConfig(enabled=True, model_id="codex-image"),
        models={
            "codex-image": _image_model(
                provider="openai_codex",
                upstream="gpt-5.4",
            )
        },
        provider_configs={"openai_codex": ProviderConfig(proxy=proxy)},
    )

    model_id, model = tool._resolve_model()
    client = tool._provider_client(model_id, model)

    assert client is not None
    assert FakeImageClient.instances[0].kwargs["proxy"] == proxy


@pytest.mark.asyncio
async def test_generate_image_tool_allows_ollama_without_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_config_path(tmp_path / "config.json")
    FakeImageClient.instances = []
    monkeypatch.setattr(
        "nanobot.agent.tools.image_generation.get_image_gen_provider",
        lambda name: FakeImageClient if name == "ollama" else None,
    )
    tool = ImageGenerationTool(
        workspace=tmp_path,
        config=ImageGenerationToolConfig(enabled=True, model_id="local-image"),
        models={
            "local-image": _image_model(
                provider="ollama",
                upstream="x/z-image-turbo",
            )
        },
        provider_configs={"ollama": ProviderConfig(api_base="http://localhost:11434/v1")},
    )

    result = await tool.execute(prompt="draw a cat")

    payload = json.loads(result)
    assert len(payload["artifacts"]) == 1
    fake = FakeImageClient.instances[0]
    assert fake.kwargs["api_key"] is None
    assert fake.kwargs["api_base"] == "http://localhost:11434/v1"
    assert fake.calls[0]["model"] == "x/z-image-turbo"


@pytest.mark.asyncio
async def test_generate_image_tool_rejects_reference_outside_workspace(tmp_path: Path) -> None:
    set_config_path(tmp_path / "config.json")
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(PNG_BYTES)
    tool = ImageGenerationTool(
        workspace=tmp_path,
        config=ImageGenerationToolConfig(enabled=True, model_id="image-gen"),
        models={"image-gen": _image_model()},
        provider_configs={"openrouter": ProviderConfig(api_key="sk-or-test")},
    )

    result = await tool.execute(prompt="edit", reference_images=[str(outside)])

    assert "reference_images must be inside the workspace" in result


def test_image_generation_config_accepts_model_id_and_rejects_legacy_fields() -> None:
    config = ImageGenerationToolConfig.model_validate({"modelId": "image-gen"})
    assert config.model_id == "image-gen"

    for legacy in (
        {"provider": "openrouter"},
        {"model": "openai/gpt-image"},
    ):
        with pytest.raises(ValidationError):
            ImageGenerationToolConfig.model_validate(legacy)


def test_config_requires_image_generation_capability_for_model_id() -> None:
    with pytest.raises(ValueError, match="does not support image_generation"):
        Config.model_validate(
            {
                "models": {
                    "main": {
                        "displayName": "Main",
                        "provider": "openrouter",
                        "model": "openai/gpt-4o-mini",
                        "capabilities": {"text": True},
                    },
                    "not-image": {
                        "displayName": "Not image",
                        "provider": "openrouter",
                        "model": "openai/gpt-4o-mini",
                        "capabilities": {"text": True},
                    },
                },
                "tools": {"imageGeneration": {"modelId": "not-image"}},
            }
        )
