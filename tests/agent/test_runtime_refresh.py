import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.queue import MessageBus
from nanobot.bus.runtime_events import RuntimeModelChanged
from nanobot.config.loader import save_config
from nanobot.config.schema import Config
from nanobot.providers.base import GenerationSettings
from nanobot.providers.factory import ProviderSnapshot, load_provider_snapshot
from nanobot.webui.settings_api import update_agent_settings


def _provider(default_model: str) -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = default_model
    provider.generation = GenerationSettings(temperature=0.1, max_tokens=1024)
    return provider


def _snapshot(
    model: str,
    *,
    preset: str | None = None,
    signature: tuple[object, ...] | None = None,
) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider=_provider(model),
        model=model,
        context_window_tokens=32_768,
        signature=signature or (model, "auto", "v1"),
        generation=GenerationSettings(temperature=0.7, max_tokens=4096, reasoning_effort="high"),
        model_preset=preset,
        system_prompt_prefix=f"prompt:{model}",
        supports_vision=True,
    )


def test_main_startup_preserves_the_complete_provider_snapshot(tmp_path: Path) -> None:
    initial = _snapshot("vision-model", preset="vision")
    loader = MagicMock()

    loop = AgentLoop(
        bus=MessageBus(),
        provider=initial.provider,
        provider_snapshot=initial,
        provider_snapshot_loader=loader,
        workspace=tmp_path,
    )

    runtime = loop.runtime_resolver.runtime
    assert runtime.provider is initial.provider
    assert runtime.model == initial.model
    assert runtime.generation == initial.generation
    assert runtime.context_window_tokens == initial.context_window_tokens
    assert runtime.snapshot_signature == initial.signature
    assert runtime.model_preset == initial.model_preset
    assert runtime.system_prompt_prefix == initial.system_prompt_prefix
    assert runtime.supports_vision is True
    loader.assert_not_called()


@pytest.mark.asyncio
async def test_invalidation_refreshes_on_next_main_admission_and_publishes(tmp_path: Path) -> None:
    initial = _snapshot("model-a", signature=("model-a", "auto", "v1"))
    refreshed = _snapshot("model-b", signature=("model-b", "auto", "v2"))
    loader = MagicMock(return_value=refreshed)
    published: list[RuntimeModelChanged] = []
    loop = AgentLoop(
        bus=MessageBus(),
        provider=initial.provider,
        provider_snapshot=initial,
        provider_snapshot_loader=loader,
        workspace=tmp_path,
    )
    loop.runtime_events.subscribe(published.append, RuntimeModelChanged)

    loop.invalidate_runtime_config()
    assert published == []
    assert loader.call_count == 0

    runtime = loop.llm_runtime()
    await asyncio.sleep(0)

    assert runtime.model == "model-b"
    loader.assert_called_once_with(include_fallbacks=True)
    assert [(event.model, event.model_preset) for event in published] == [("model-b", None)]


def test_invalidated_main_runtime_surfaces_config_errors(tmp_path: Path) -> None:
    initial = _snapshot("model-a")

    def fail_refresh(**_kwargs) -> ProviderSnapshot:
        raise ValueError("invalid config")

    loop = AgentLoop(
        bus=MessageBus(),
        provider=initial.provider,
        provider_snapshot=initial,
        provider_snapshot_loader=fail_refresh,
        workspace=tmp_path,
    )
    loop.invalidate_runtime_config()

    with pytest.raises(ValueError, match="invalid config"):
        loop.llm_runtime()


def test_settings_context_window_refreshes_runtime_state(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    config.agents.defaults.model = "openai/gpt-4o"
    config.agents.defaults.provider = "openai"
    config.agents.defaults.context_window_tokens = 65_536
    config.providers.openai.api_key = "sk-test"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    def loader(**kwargs) -> ProviderSnapshot:
        return load_provider_snapshot(config_path, **kwargs)

    loop = AgentLoop.from_config(
        config,
        tool_registry=ToolRegistry(),
        provider_snapshot_loader=loader,
    )

    payload = update_agent_settings({"context_window_tokens": ["262144"]})
    loop.invalidate_runtime_config()
    loop.llm_runtime()

    assert payload["requires_restart"] is False
    assert loop.context_window_tokens == 262_144
