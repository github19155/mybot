from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.runtime_control import AgentRuntimeControl
from nanobot.agent.tools.self import MyTool
from nanobot.bus.queue import MessageBus
from nanobot.model_domain import ModelConfig, ModelGenerationDefaults
from nanobot.providers.factory import ProviderSnapshot
from nanobot.session.model_selection import model_id_from_metadata


def _provider(default_model: str) -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = default_model
    provider.generation = SimpleNamespace(
        max_tokens=123,
        temperature=0.1,
        reasoning_effort=None,
    )
    return provider


def _model(
    upstream: str,
    *,
    provider: str = "openai",
    context_window_tokens: int = 32_768,
    max_tokens: int = 4096,
) -> ModelConfig:
    return ModelConfig(
        display_name=upstream,
        provider=provider,
        model=upstream,
        context_window_tokens=context_window_tokens,
        generation_defaults=ModelGenerationDefaults(
            temperature=0.5,
            max_tokens=max_tokens,
            reasoning_effort="low",
        ),
    )


def _make_loop(tmp_path, *, active_model_id: str | None = None) -> AgentLoop:
    models = {
        "base": _model("base-model", context_window_tokens=8_192, max_tokens=1024),
        "fast": _model("openai/gpt-5.6", context_window_tokens=32_768),
    }

    def load_snapshot(*, model_id=None, **_kwargs):
        selected_id = model_id or "base"
        selected = models[selected_id]
        provider = _provider(selected.model)
        generation = selected.generation_defaults
        return ProviderSnapshot(
            model_id=selected_id,
            provider=provider,
            model=selected.model,
            context_window_tokens=selected.context_window_tokens,
            signature=(selected_id, selected.provider, selected.model),
            generation=SimpleNamespace(
                temperature=generation.temperature,
                max_tokens=generation.max_tokens,
                reasoning_effort=generation.reasoning_effort,
            ),
            supports_vision=selected.capabilities.vision,
        )

    initial = load_snapshot(model_id="base")
    return AgentLoop(
        bus=MessageBus(),
        provider=initial.provider,
        provider_snapshot=initial,
        workspace=tmp_path,
        models=models,
        model_id=active_model_id,
        provider_snapshot_loader=load_snapshot,
    )


def _my_tool(loop: AgentLoop) -> MyTool:
    return MyTool(runtime_control=AgentRuntimeControl(loop), modify_allowed=True)


def test_model_id_switch_updates_canonical_runtime(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    runtime = loop.set_model_id("fast")

    assert loop.model_id == "fast"
    assert runtime.model_id == "fast"
    assert loop.model == "openai/gpt-5.6"
    assert loop.context_window_tokens == 32_768
    assert runtime.generation.max_tokens == 4096


def test_model_id_switch_rejects_raw_upstream_model(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    with pytest.raises(ValueError, match="model_id must match"):
        loop.set_model_id("openai/gpt-5.6")

    assert loop.model_id == "base"
    assert loop.model == "base-model"


def test_self_tool_sets_model_id_for_current_session(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    tool = _my_tool(loop)

    with request_context(RequestContext(
        channel="cli",
        chat_id="one",
        session_key="cli:one",
        metadata={"source": "self-tool"},
    )):
        result = tool._modify("model_id", "fast")

    assert "Error" not in result
    assert "for the next turn" in result
    assert model_id_from_metadata(
        loop.sessions.get_or_create("cli:one").metadata
    ) == "fast"
    assert loop.model_id == "base"
    assert loop.model == "base-model"


def test_self_tool_rejects_raw_model_write(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    tool = _my_tool(loop)

    result = tool._modify("model", "openai/gpt-5.6")

    assert "read-only" in result or "cannot be modified" in result
    assert loop.model_id == "base"


def test_self_tool_reports_unknown_model_id(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    tool = _my_tool(loop)

    result = tool._modify("model_id", "missing")

    assert result.startswith("Error:")
    assert "missing" in result
    assert loop.model_id == "base"
