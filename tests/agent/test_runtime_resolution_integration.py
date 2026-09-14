"""Cross-layer runtime resolution contracts for Phase 3B acceptance."""

import asyncio
from dataclasses import replace
from functools import partial
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.model_management import ModelManagement
from nanobot.agent.model_presets import configured_model_presets
from nanobot.agent.model_runtime import ModelRuntimeResolver
from nanobot.agent.permissions import PermissionManager
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.subagent_roles import resolve_role
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.providers.factory import build_provider_snapshot, provider_signature
from nanobot.utils.llm_runtime import runtime_from_provider_snapshot


@pytest.fixture
def runtime_config(tmp_path):
    return Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "workspace": str(tmp_path),
                    "model": "openai/gpt-4.1",
                    "provider": "openai",
                    "max_tokens": 1234,
                    "temperature": 0.2,
                    "reasoning_effort": "low",
                    "context_window_tokens": 64_000,
                    "supports_vision": True,
                }
            },
            "model_fleet": {"enabled": False},
            "providers": {
                "openai": {"api_key": "sk-runtime-test-openai"},
                "deepseek": {"api_key": "sk-runtime-test-deepseek"},
            },
            "model_presets": {
                "worker": {
                    "model": "deepseek/deepseek-chat",
                    "provider": "deepseek",
                    "max_tokens": 2048,
                    "temperature": 0.4,
                    "reasoning_effort": "medium",
                    "context_window_tokens": 48_000,
                    "supports_vision": True,
                },
                "backup": {
                    "model": "openai/gpt-4o-mini",
                    "provider": "openai",
                    "context_window_tokens": 8_000,
                },
            },
        }
    )


def _resolver(config):
    loader = MagicMock(wraps=partial(build_provider_snapshot, config))
    resolver = ModelRuntimeResolver(
        runtime_from_provider_snapshot(build_provider_snapshot(config)),
        model_presets=configured_model_presets(config),
        preset_catalog_loader=partial(configured_model_presets, config),
        provider_snapshot_loader=loader,
    )
    return resolver, loader


def test_from_config_reuses_supplied_snapshot_without_rebuilding(runtime_config):
    snapshot = replace(
        build_provider_snapshot(runtime_config, preset_name="worker"),
        system_prompt_prefix="captured worker prompt",
    )
    loader = MagicMock(wraps=partial(build_provider_snapshot, runtime_config))

    loop = AgentLoop.from_config(
        runtime_config,
        tool_registry=ToolRegistry(),
        provider_snapshot=snapshot,
        provider_snapshot_loader=loader,
    )

    runtime = loop.llm_runtime()
    assert runtime == runtime_from_provider_snapshot(snapshot)
    assert runtime.provider is snapshot.provider
    assert runtime.model == "deepseek/deepseek-chat"
    assert runtime.generation == snapshot.generation
    assert runtime.context_window_tokens == 48_000
    assert runtime.snapshot_signature == snapshot.signature
    assert runtime.model_preset == "worker"
    assert runtime.system_prompt_prefix == "captured worker prompt"
    assert runtime.supports_vision is True
    loader.assert_not_called()


@pytest.mark.parametrize(
    ("selection", "expected_preset", "expected_vision"),
    [
        ({"model": "deepseek/deepseek-chat"}, None, False),
        ({"model_preset": "worker"}, "worker", True),
    ],
)
def test_override_and_default_selection_share_cross_provider_resolution(
    runtime_config,
    selection,
    expected_preset,
    expected_vision,
):
    resolver, _loader = _resolver(runtime_config)
    parent = resolver.runtime
    override = resolver.resolve_override(
        model=selection.get("model"),
        model_preset=selection.get("model_preset"),
    )

    assert resolver.runtime is parent
    if "model" in selection:
        selected = resolver.select_model(selection["model"])
    else:
        selected = resolver.select_preset(selection["model_preset"])
    main = AgentLoop.from_config(
        runtime_config,
        tool_registry=ToolRegistry(),
        **selection,
    ).llm_runtime()

    assert resolver.runtime is selected
    assert parent.provider.provider_name == "openai"
    assert parent.model == "openai/gpt-4.1"
    for runtime in (override, selected, main):
        assert runtime is not None
        assert runtime.provider.provider_name == "deepseek"
        assert runtime.model == "deepseek/deepseek-chat"
        assert runtime.generation == selected.generation
        assert runtime.context_window_tokens == selected.context_window_tokens
        assert runtime.snapshot_signature == selected.snapshot_signature
        assert runtime.model_preset == expected_preset
        assert runtime.supports_vision is expected_vision

    if "model" in selection:
        assert selected.generation == parent.generation
        assert selected.context_window_tokens == parent.context_window_tokens


@pytest.mark.asyncio
@pytest.mark.parametrize("dream_first", [False, True])
async def test_main_and_dream_share_one_resolved_preset_runtime(runtime_config, dream_first):
    runtime_config.agents.defaults.dream.model_override = "worker"
    original = runtime_config.model_dump()
    resolver, loader = _resolver(runtime_config)
    management = ModelManagement(runtime_config, runtime_resolver=resolver)

    if dream_first:
        dream = await management.resolve_dream_runtime("consolidation")
        main = resolver.select_preset("worker")
    else:
        main = resolver.select_preset("worker")
        dream = await management.resolve_dream_runtime("consolidation")

    assert main is dream
    assert main.provider.provider_name == "deepseek"
    assert main.context_window_tokens == 48_000
    assert main.snapshot_signature == provider_signature(runtime_config, preset_name="worker")
    assert resolver.resolve_preset("worker") is main
    assert await management.resolve_dream_runtime("consolidation") is main
    assert resolver.runtime is main
    assert loader.call_count == 1
    assert runtime_config.model_dump() == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role_selection", "selection", "expected_model", "expected_provider"),
    [
        (
            {"model": "deepseek/deepseek-chat"},
            {"model_preset": "backup"},
            "openai/gpt-4o-mini",
            "openai",
        ),
        (
            {"model_preset": "worker"},
            {"model": "openai/gpt-4o-mini"},
            "openai/gpt-4o-mini",
            "openai",
        ),
        (
            {"model_preset": "worker"},
            {},
            "deepseek/deepseek-chat",
            "deepseek",
        ),
        ({}, {}, "openai/gpt-4.1", "openai"),
    ],
)
async def test_subagent_freezes_one_role_and_runtime_per_task(
    runtime_config,
    monkeypatch,
    tmp_path,
    role_selection,
    selection,
    expected_model,
    expected_provider,
):
    runtime_config.subagent_roles["coder"] = type(
        runtime_config.subagent_roles["coder"]
    ).model_validate(
        {
            **role_selection,
            "thinking": "high",
            "context": "fork",
            "timeout_seconds": 73,
        }
    )
    resolver, _loader = _resolver(runtime_config)
    parent = resolver.runtime
    management = ModelManagement(runtime_config, runtime_resolver=resolver)
    role_spy = MagicMock(wraps=resolve_role)
    monkeypatch.setattr("nanobot.agent.subagent.resolve_role", role_spy)
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        model_management=management,
        runtime_resolver=resolver,
        permission_manager=PermissionManager(runtime_config),
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    captured_call = {}

    async def fake_run(*args, **kwargs):
        captured_call["args"] = args
        captured_call["kwargs"] = kwargs
        entered.set()
        await release.wait()
        return "done"

    manager._run_subagent = AsyncMock(side_effect=fake_run)
    dispatch = await manager.spawn(
        "check runtime",
        runtime=parent,
        role="coder",
        **selection,
    )
    task = next(iter(manager._running_tasks.values()))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert "id:" in dispatch
        assert role_spy.call_count == 1
        args = captured_call["args"]
        kwargs = captured_call["kwargs"]
        status = args[4]
        captured_runtime = args[5]
        role_snapshot = kwargs["role_definition"]

        assert captured_runtime.model == expected_model
        assert captured_runtime.provider.provider_name == expected_provider
        assert captured_runtime.generation.reasoning_effort == "high"
        assert status.thinking == "high"
        assert status.context == "fork"
        assert status.timeout_seconds == 73
        assert status.role_snapshot is role_snapshot
        assert role_snapshot.thinking == "high"
        assert role_snapshot.context == "fork"
        assert role_snapshot.timeout_seconds == 73
        assert resolver.runtime is parent
        assert parent.generation.reasoning_effort == "low"

        runtime_config.subagent_roles["coder"].thinking = "low"
        runtime_config.subagent_roles["coder"].timeout_seconds = 99
        switched = resolver.select_model("deepseek/deepseek-reasoner")
        assert switched.model == "deepseek/deepseek-reasoner"
        assert args[5] is captured_runtime
        assert captured_runtime.model == expected_model
        assert captured_runtime.generation.reasoning_effort == "high"
        assert status.timeout_seconds == 73
        assert role_snapshot.thinking == "high"
    finally:
        release.set()
        await task

    assert role_spy.call_count == 1


def test_invalidation_refreshes_new_admissions_without_rewriting_captured_runtime(
    runtime_config,
):
    resolver, loader = _resolver(runtime_config)
    captured = resolver.admit()
    cached_preset = resolver.resolve_preset("worker")

    runtime_config.agents.defaults.model = "openai/gpt-4o-mini"
    runtime_config.agents.defaults.context_window_tokens = 96_000
    runtime_config.model_presets["worker"].context_window_tokens = 56_000
    loader.reset_mock()
    resolver.invalidate()

    assert resolver.runtime is captured
    loader.assert_not_called()
    fresh = resolver.admit()
    refreshed_preset = resolver.resolve_preset("worker")

    assert fresh.model == "openai/gpt-4o-mini"
    assert fresh.context_window_tokens == 96_000
    assert fresh.provider is not captured.provider
    assert refreshed_preset.context_window_tokens == 56_000
    assert refreshed_preset is not cached_preset
    assert captured.model == "openai/gpt-4.1"
    assert captured.context_window_tokens == 64_000
    assert cached_preset.context_window_tokens == 48_000
    assert resolver.admit() is fresh
    assert loader.call_count == 2
