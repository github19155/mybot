"""Acceptance tests for the nonblocking subagent control design."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.permissions import PermissionManager
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.bus.queue import MessageBus
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config, ModelPresetConfig
from nanobot.providers.base import GenerationSettings, LLMProvider
from nanobot.utils.llm_runtime import LLMRuntime


def _runtime() -> LLMRuntime:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "parent/model"
    provider.generation = GenerationSettings(temperature=0.2, max_tokens=1024)
    return LLMRuntime.capture(provider, "parent/model", context_window_tokens=32_000)


def _manager(tmp_path, **kwargs) -> SubagentManager:
    return SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        permission_manager=PermissionManager(Config()),
        **kwargs,
    )


def test_config_accepts_custom_role_and_full_role_defaults() -> None:
    config = Config(
        modelPresets={
            "coding": ModelPresetConfig(model="provider/coder"),
        },
        subagentRoles={
            "backend-coder": {
                "description": "Backend implementation",
                "systemPrompt": "Implement and test backend changes.",
                "tools": ["read_file", "write_file", "exec"],
                "modelPreset": "coding",
                "thinking": "high",
                "temperature": 0.1,
                "timeoutSeconds": 1800,
                "context": "fork",
            },
        },
    )

    role = config.subagent_roles["backend-coder"]
    assert role.system_prompt == "Implement and test backend changes."
    assert role.tools == ["read_file", "write_file", "exec"]
    assert role.model_preset == "coding"
    assert role.thinking == "high"
    assert role.timeout_seconds == 1800
    assert role.context == "fork"


def test_role_model_and_preset_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="model.*model_preset|model_preset.*model"):
        Config(
            subagentRoles={
                "custom": {
                    "description": "Custom role",
                    "systemPrompt": "Do the work.",
                    "model": "provider/model",
                    "modelPreset": "coding",
                },
            },
        )


def test_role_store_supports_custom_crud_and_builtin_disable_reset() -> None:
    from nanobot.agent.subagent_roles import SubagentRoleStore

    config = Config()
    store = SubagentRoleStore(config)
    created = store.create(
        "release-manager",
        {
            "description": "Prepare a release",
            "systemPrompt": "Check release readiness and report blockers.",
            "tools": ["read_file", "exec"],
        },
    )
    assert created["name"] == "release-manager"
    assert store.get("release-manager")["system_prompt"].startswith("Check")

    disabled = store.delete("coder")
    assert disabled["disabled"] is True
    assert store.get("coder")["disabled"] is True
    assert store.reset("coder")["disabled"] is False

    assert store.delete("release-manager")["deleted"] is True
    with pytest.raises(ValueError, match="Unknown subagent role"):
        store.get("release-manager")

    with pytest.raises(ValueError, match="Unknown or forbidden"):
        store.update("coder", {"tools": ["subagent"]})
    assert "subagent" not in (config.subagent_roles.get("coder").tools if config.subagent_roles.get("coder") else [])


def test_role_store_uses_atomic_file_backing(tmp_path) -> None:
    from nanobot.agent.subagent_roles import SubagentRoleStore

    path = tmp_path / "config.json"
    save_config(Config(), path)
    config = load_config(path)
    store = SubagentRoleStore(config)
    store.create(
        "incident-lead",
        {"description": "Lead an incident", "systemPrompt": "Investigate the incident."},
    )
    store.delete("coder")

    reloaded = load_config(path)
    assert "incident-lead" in reloaded.subagent_roles
    assert reloaded.subagent_roles["coder"].disabled is True


@pytest.mark.asyncio
async def test_session_budget_queues_instead_of_rejecting(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager.max_concurrent_per_session = 1
    entered = asyncio.Event()
    release = asyncio.Event()

    async def run(spec):
        entered.set()
        await release.wait()
        return SimpleNamespace(
            stop_reason="completed",
            final_content="done",
            error=None,
            tool_events=[],
        )

    manager.runner.run = AsyncMock(side_effect=run)
    runtime = _runtime()

    first = await manager.spawn("first", runtime=runtime, session_key="session-1")
    await asyncio.wait_for(entered.wait(), timeout=1)
    second = await manager.spawn("second", runtime=runtime, session_key="session-1")

    assert isinstance(first, str) and "id:" in first
    assert isinstance(second, str) and "queued" in second.lower()
    queued = [status for status in manager._task_statuses.values() if status.task_description == "second"]
    assert len(queued) == 1
    assert queued[0].phase == "queued"

    release.set()
    await manager.close()


@pytest.mark.asyncio
async def test_run_snapshots_thinking_and_fork_context(tmp_path) -> None:
    manager = _manager(tmp_path)
    seen = {}

    async def run(spec):
        seen["messages"] = spec.initial_messages
        seen["thinking"] = spec.runtime.generation.reasoning_effort
        return SimpleNamespace(
            stop_reason="completed",
            final_content="done",
            error=None,
            tool_events=[],
        )

    manager.runner.run = AsyncMock(side_effect=run)
    await manager.spawn(
        "summarize",
        runtime=_runtime(),
        session_key="session-1",
        thinking="high",
        context="fork",
        fork_history=[
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer", "reasoning_content": "private"},
        ],
    )
    await asyncio.gather(*manager._running_tasks.values(), return_exceptions=True)

    assert seen["thinking"] == "high"
    assert seen["messages"][-2]["content"] == "Earlier answer"
    assert "reasoning_content" not in seen["messages"][-2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role_selection", "run_model", "run_preset", "expected_model", "expected_preset", "calls"),
    [
        ({"model": "provider/role"}, "provider/run", None, "provider/run", None, 1),
        ({"modelPreset": "fast"}, None, None, None, "fast", 1),
        ({}, None, None, None, None, 0),
    ],
)
async def test_subagent_runtime_precedence_uses_one_frozen_role(
    tmp_path,
    monkeypatch,
    role_selection,
    run_model,
    run_preset,
    expected_model,
    expected_preset,
    calls,
) -> None:
    from nanobot.agent.model_management import ModelManagement
    from nanobot.agent.subagent_roles import resolve_role as real_resolve_role

    config = Config(
        modelPresets={"fast": {"model": "provider/fast"}},
        subagentRoles={"coder": role_selection},
    )
    parent = _runtime()
    resolver = MagicMock()
    resolver.runtime = parent
    resolver.resolve_selection.return_value = parent
    management = ModelManagement(config, runtime_resolver=resolver)
    role_resolver = MagicMock(side_effect=real_resolve_role)
    monkeypatch.setattr("nanobot.agent.subagent.resolve_role", role_resolver)
    manager = _manager(
        tmp_path,
        model_management=management,
        runtime_resolver=resolver,
    )
    manager._announce_result = AsyncMock()
    manager.runner.run = AsyncMock(return_value=SimpleNamespace(
        stop_reason="completed", final_content="done", error=None, tool_events=[],
    ))

    dispatch = await manager.spawn(
        "runtime precedence",
        runtime=parent,
        role="coder",
        model=run_model,
        model_preset=run_preset,
    )
    tasks = list(manager._running_tasks.values())
    await asyncio.gather(*tasks, return_exceptions=True)

    assert "id:" in dispatch
    assert role_resolver.call_count == 1
    assert resolver.resolve_selection.call_count == calls
    if calls:
        resolver.resolve_selection.assert_called_once_with(
            parent,
            model=expected_model,
            model_preset=expected_preset,
        )
    await manager.close()


@pytest.mark.asyncio
async def test_global_budget_is_sixteen_and_queues_the_seventeenth(tmp_path) -> None:
    manager = _manager(tmp_path, max_concurrent_subagents=16)
    manager._announce_result = AsyncMock()
    entered = 0
    entered_event = asyncio.Event()
    release = asyncio.Event()

    async def run(spec):
        nonlocal entered
        entered += 1
        if entered == 16:
            entered_event.set()
        await release.wait()
        return SimpleNamespace(
            stop_reason="completed", final_content="done", error=None, tool_events=[],
        )

    manager.runner.run = AsyncMock(side_effect=run)
    for index in range(17):
        await manager.spawn(
            f"task-{index}",
            runtime=_runtime(),
            session_key=f"session-{index}",
        )

    await asyncio.wait_for(entered_event.wait(), timeout=1)
    assert manager.get_running_count() == 16
    assert sum(status.state == "queued" for status in manager._task_statuses.values()) == 1

    release.set()
    await manager.close()


@pytest.mark.asyncio
async def test_stop_running_task_marks_stopped_and_terminates_only_its_exec_owner(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager._announce_result = AsyncMock()
    entered = asyncio.Event()

    async def run(spec):
        entered.set()
        await asyncio.Event().wait()

    manager.runner.run = AsyncMock(side_effect=run)
    manager._exec_session_manager.terminate_by_owner = AsyncMock(return_value=0)
    await manager.spawn("long task", runtime=_runtime(), session_key="session-1")
    await asyncio.wait_for(entered.wait(), timeout=1)
    task_id = next(iter(manager._running_tasks))

    assert await manager.stop(task_id) is True
    await asyncio.sleep(0)

    status = next(item for item in manager._finished if item.task_id == task_id)
    assert status.state == "stopped"
    manager._exec_session_manager.terminate_by_owner.assert_awaited_once_with(
        f"session-1:subagent:{task_id}"
    )
    assert manager._announce_result.await_count == 1
    assert manager._announce_result.await_args.args[5] == "stopped"


@pytest.mark.asyncio
async def test_stop_does_not_relabel_completed_task_during_notification(tmp_path) -> None:
    manager = _manager(tmp_path)
    notification_started = asyncio.Event()
    release_notification = asyncio.Event()

    async def announce(*_args):
        notification_started.set()
        await release_notification.wait()

    manager._announce_result = announce
    manager.runner.run = AsyncMock(return_value=SimpleNamespace(
        stop_reason="completed", final_content="done", error=None, tool_events=[],
    ))
    await manager.spawn("done task", runtime=_runtime(), session_key="session-1")
    task_id = next(iter(manager._running_tasks))
    await asyncio.wait_for(notification_started.wait(), timeout=1)

    assert await manager.stop(task_id) is False
    release_notification.set()
    await asyncio.gather(*manager._running_tasks.values(), return_exceptions=True)
    await asyncio.sleep(0)
    status = next(item for item in manager._finished if item.task_id == task_id)
    assert status.state == "completed"


@pytest.mark.asyncio
async def test_unified_subagent_tool_runs_in_background_by_default() -> None:
    from nanobot.agent.tools.subagent import SubagentTool

    manager = type("Manager", (), {})()
    manager.spawn = AsyncMock(return_value="queued (id: task-1)")
    tool = SubagentTool(manager)
    token = bind_request_context(RequestContext(
        channel="test",
        chat_id="chat-1",
        session_key="session-1",
        runtime=_runtime(),
        allowed_tools=frozenset({"read_file", "subagent"}),
    ))
    try:
        result = await tool.execute(action="run", task="inspect the project")
    finally:
        reset_request_context(token)

    assert result == "queued (id: task-1)"
    manager.spawn.assert_awaited_once()
    assert manager.spawn.await_args.kwargs["session_key"] == "session-1"
    assert manager.spawn.await_args.kwargs["allowed_tools"] == {"read_file", "subagent"}
