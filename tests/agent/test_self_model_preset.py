from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.runtime_control import AgentRuntimeControl
from nanobot.agent.tools.self import MyTool
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ModelPresetConfig
from nanobot.providers.factory import ProviderSnapshot
from nanobot.session.model_selection import model_preset_from_metadata


def _provider(default_model: str, max_tokens: int = 123) -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = default_model
    provider.generation = SimpleNamespace(
        max_tokens=max_tokens, temperature=0.1, reasoning_effort=None
    )
    return provider


def _make_loop(tmp_path, presets=None, active_preset=None):
    provider = _provider("base-model")
    configured = presets or {}

    def load_snapshot(*, preset_name=None, preset=None, **_kwargs):
        selected = preset or configured[preset_name]
        selected_provider = _provider(selected.model, max_tokens=selected.max_tokens or 123)
        selected_provider.generation = SimpleNamespace(
            max_tokens=selected.max_tokens or 123,
            temperature=selected.temperature if selected.temperature is not None else 0.1,
            reasoning_effort=selected.reasoning_effort,
        )
        return ProviderSnapshot(
            provider=selected_provider,
            model=selected.model,
            context_window_tokens=selected.context_window_tokens,
            signature=(preset_name, selected.model),
            generation=selected_provider.generation,
            model_preset=preset_name,
        )

    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1000,
        model_presets=configured,
        model_preset=active_preset,
        provider_snapshot_loader=load_snapshot,
    )


def _my_tool(loop: AgentLoop) -> MyTool:
    return MyTool(
        runtime_control=AgentRuntimeControl(loop),
        modify_allowed=True,
    )


def test_model_preset_getter_none_when_not_set(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    assert loop.model_preset is None


def test_model_preset_setter_updates_state(tmp_path) -> None:
    presets = {
        "fast": ModelPresetConfig(
            model="openai/gpt-4.1",
            provider="openai",
            max_tokens=4096,
            context_window_tokens=32_768,
            temperature=0.5,
            reasoning_effort="low",
        )
    }
    loop = _make_loop(tmp_path, presets=presets)
    loop.model_preset = "fast"

    assert loop.model_preset == "fast"
    assert loop.model == "openai/gpt-4.1"
    assert loop.context_window_tokens == 32_768
    runtime = loop.llm_runtime()
    assert runtime.generation.temperature == 0.5
    assert runtime.generation.max_tokens == 4096
    assert runtime.generation.reasoning_effort == "low"
    assert not hasattr(loop.subagents, "model")
    assert not hasattr(loop.consolidator, "model")
    assert not hasattr(loop.consolidator, "context_window_tokens")
    assert loop.llm_runtime().model == "openai/gpt-4.1"
    assert loop.llm_runtime().context_window_tokens == 32_768
    assert not hasattr(loop.consolidator, "max_completion_tokens")
    assert loop.llm_runtime().generation.max_tokens == 4096


def test_model_preset_setter_calls_runtime_model_publisher(tmp_path) -> None:
    published: list[tuple[str, str | None]] = []
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider("base-model", max_tokens=123),
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1000,
        model_presets={"fast": ModelPresetConfig(model="openai/gpt-4.1")},
        provider_snapshot_loader=lambda *, preset_name=None, **_kwargs: ProviderSnapshot(
            provider=_provider("openai/gpt-4.1"),
            model="openai/gpt-4.1",
            context_window_tokens=200_000,
            signature=(preset_name, "openai/gpt-4.1"),
            model_preset=preset_name,
        ),
        runtime_model_publisher=lambda model, preset: published.append((model, preset)),
    )

    loop.set_model_preset("fast")

    assert published == [("openai/gpt-4.1", "fast")]


def test_model_preset_setter_replaces_provider_from_snapshot(tmp_path) -> None:
    old_provider = _provider("base-model", max_tokens=123)
    new_provider = _provider("anthropic/claude-opus-4-5", max_tokens=2048)
    preset = ModelPresetConfig(
        model="anthropic/claude-opus-4-5",
        provider="anthropic",
        max_tokens=2048,
        context_window_tokens=200_000,
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=old_provider,
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1000,
        model_presets={"deep": preset},
        provider_snapshot_loader=lambda *, preset_name=None, **_kwargs: ProviderSnapshot(
            provider=new_provider,
            model=preset.model,
            context_window_tokens=preset.context_window_tokens,
            signature=(preset_name, preset.model),
            model_preset=preset_name,
        ),
    )

    loop.set_model_preset("deep")

    assert loop.provider is new_provider
    assert not hasattr(loop.runner, "provider")
    assert not hasattr(loop.subagents, "provider")
    assert not hasattr(loop.subagents.runner, "provider")
    assert not hasattr(loop.consolidator, "provider")
    assert loop.model == "anthropic/claude-opus-4-5"
    assert loop.context_window_tokens == 200_000
    assert not hasattr(loop.consolidator, "max_completion_tokens")
    assert loop.llm_runtime().generation.max_tokens == 2048


def test_model_preset_setter_failure_leaves_old_state(tmp_path) -> None:
    preset = ModelPresetConfig(model="openai/gpt-4.1", max_tokens=4096)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider("base-model", max_tokens=123),
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1000,
        model_presets={"fast": preset},
        provider_snapshot_loader=lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("provider unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        loop.set_model_preset("fast")

    assert loop.model_preset is None
    assert loop.model == "base-model"
    assert not hasattr(loop.subagents, "model")
    assert not hasattr(loop.consolidator, "model")
    assert loop.context_window_tokens == 1000
    assert not hasattr(loop.consolidator, "max_completion_tokens")
    assert loop.llm_runtime().generation.max_tokens == 123


def test_model_preset_setter_raises_on_unknown(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    with pytest.raises(KeyError, match="model_preset 'missing' not found"):
        loop.model_preset = "missing"


def test_model_preset_setter_raises_on_empty_string(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    with pytest.raises(ValueError, match="model_preset must be a non-empty string"):
        loop.model_preset = ""


def test_self_tool_inspect_shows_model_preset(tmp_path) -> None:
    presets = {
        "fast": ModelPresetConfig(model="openai/gpt-4.1"),
    }
    loop = _make_loop(tmp_path, presets=presets, active_preset="fast")
    tool = _my_tool(loop)
    output = tool._inspect_all()
    assert "model_preset: 'fast'" in output


def test_self_tool_set_model_preset_via_modify(tmp_path) -> None:
    presets = {
        "fast": ModelPresetConfig(model="openai/gpt-4.1"),
    }
    loop = _make_loop(tmp_path, presets=presets)
    tool = _my_tool(loop)
    result = tool._modify("model_preset", "fast")
    assert "Error" not in result
    assert loop.model_preset == "fast"
    assert loop.model == "openai/gpt-4.1"


def test_self_tool_set_model_preset_switches_back_to_default(tmp_path) -> None:
    presets = {
        "default": ModelPresetConfig(model="base-model", context_window_tokens=1000),
        "fast": ModelPresetConfig(model="openai/gpt-4.1", context_window_tokens=32_768),
    }
    loop = _make_loop(tmp_path, presets=presets, active_preset="fast")
    tool = _my_tool(loop)

    result = tool._modify("model_preset", "default")

    assert "Error" not in result
    assert "model is now 'base-model'" in result
    assert loop.model_preset == "default"
    assert loop.model == "base-model"
    assert loop.context_window_tokens == 1000


def test_self_tool_set_model_preset_unknown_lists_available(tmp_path) -> None:
    presets = {
        "default": ModelPresetConfig(model="base-model"),
        "fast": ModelPresetConfig(model="openai/gpt-4.1"),
    }
    loop = _make_loop(tmp_path, presets=presets)
    tool = _my_tool(loop)

    result = tool._modify("model_preset", "missing")

    assert result == "Error: model_preset 'missing' not found. Available: default, fast."
    assert loop.model_preset is None
    assert loop.model == "base-model"


def test_self_tool_sets_model_preset_for_current_session(tmp_path) -> None:
    presets = {
        "default": ModelPresetConfig(model="base-model"),
        "fast": ModelPresetConfig(model="openai/gpt-4.1"),
    }
    loop = _make_loop(tmp_path, presets=presets)
    tool = _my_tool(loop)

    with request_context(RequestContext(
        channel="cli",
        chat_id="one",
        session_key="cli:one",
        metadata={"source": "self-tool"},
    )):
        result = tool._modify("model_preset", "fast")

    assert "for the next turn" in result
    assert model_preset_from_metadata(
        loop.sessions.get_or_create("cli:one").metadata
    ) == "fast"
    assert loop.model_preset is None
    assert loop.model == "base-model"


def test_self_tool_reports_session_preset_provider_configuration_error(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    loop.set_session_model_preset = MagicMock(
        side_effect=ValueError("No API key configured for provider 'openai'.")
    )
    tool = _my_tool(loop)

    with request_context(RequestContext(
        channel="cli",
        chat_id="one",
        session_key="cli:one",
    )):
        result = tool._modify("model_preset", "broken")

    assert result == "Error: No API key configured for provider 'openai'."


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("model", "other-model"),
        ("context_window_tokens", 8_192),
    ],
)
def test_self_tool_rejects_instance_runtime_changes_in_session(
    tmp_path,
    key: str,
    value: object,
) -> None:
    loop = _make_loop(tmp_path)
    tool = _my_tool(loop)
    session = loop.sessions.get_or_create("cli:one")

    with request_context(RequestContext(
        channel="cli",
        chat_id="one",
        session_key=session.key,
        runtime=loop.runtime_for_session(session),
    )):
        result = tool._modify(key, value)

    other_runtime = loop.runtime_for_session(loop.sessions.get_or_create("cli:two"))
    assert "instance-wide and disabled" in result
    assert "model_preset" in result
    assert other_runtime.model == "base-model"
    assert other_runtime.context_window_tokens == 1000


def test_self_tool_set_model_clears_active_preset(tmp_path) -> None:
    presets = {
        "fast": ModelPresetConfig(model="openai/gpt-4.1"),
    }
    loop = _make_loop(tmp_path, presets=presets, active_preset="fast")
    tool = _my_tool(loop)
    result = tool._modify("model", "anthropic/claude-opus-4-5")
    assert "Error" not in result
    assert loop.model_preset is None
    assert loop.model == "anthropic/claude-opus-4-5"


def test_from_config_injects_default_preset(tmp_path) -> None:
    from unittest.mock import patch

    from nanobot.config.schema import Config
    config = Config.model_validate({
        "agents": {"defaults": {"model": "openai/gpt-4.1", "workspace": str(tmp_path)}},
    })
    fake_provider = _provider("openai/gpt-4.1")
    with patch("nanobot.providers.factory.make_provider", return_value=fake_provider):
        loop = AgentLoop.from_config(config, tool_registry=ToolRegistry())
    assert loop.model == "openai/gpt-4.1"
    assert loop.model_preset is None
    assert "default" in loop.model_presets
    assert loop.model_presets["default"].model == "openai/gpt-4.1"

