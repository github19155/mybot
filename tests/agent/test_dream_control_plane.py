"""Dream control-plane invariants for the native single-path implementation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.dream import (
    DREAM_CONSOLIDATION,
    DREAM_GOVERNANCE,
    DreamRunContext,
    DreamTriggerController,
    build_dream_tools,
    parse_dream_result,
)
from nanobot.agent.dream_runtime import resolve_dream_runtime
from nanobot.agent.dream_worker import run_dream_worker
from nanobot.agent.memory import MemoryStore
from nanobot.agent.permissions import PermissionManager
from nanobot.bus.events import InboundMessage
from nanobot.command.dream_commands import cmd_dream
from nanobot.command.router import CommandContext
from nanobot.config.schema import Config, DreamConfig
from nanobot.model_fleet import ModelAdmissionController, ModelFleetManager


def _config(**overrides: object) -> DreamConfig:
    return DreamConfig.model_validate({
        "cooldown_minutes": 30,
        "idle_minutes": 10,
        "pressure_entries": 3,
        "max_runs_per_day": 8,
        **overrides,
    })


def test_dream_config_is_single_bounded_policy_source() -> None:
    with pytest.raises(ValueError):
        DreamConfig(cooldown_minutes=0)
    with pytest.raises(ValueError):
        DreamConfig(max_runs_per_day=999)
    with pytest.raises(ValueError):
        DreamConfig(retention_days=999)

    config = DreamConfig(pools={DREAM_CONSOLIDATION: " DREAM-CHEAP "})
    assert config.pool_for(DREAM_CONSOLIDATION) == "dream-cheap"
    assert not hasattr(config, "cron")
    assert not hasattr(config, "interval_h")
    assert not hasattr(config, "model_override")
    assert not hasattr(config, "fallback_preset")


def test_trigger_controller_pressure_idle_and_cooldown(tmp_path: Path) -> None:
    controller = DreamTriggerController(tmp_path, _config(), PermissionManager(Config()))
    now = 2_000_000_000

    active = controller.decide(
        pending_entries=5,
        last_activity_ms=now - 60_000,
        now_ms=now,
    )
    assert active.run is False
    assert active.reason == "foreground_active"

    ready = controller.decide(
        pending_entries=5,
        last_activity_ms=now - 20 * 60_000,
        now_ms=now,
    )
    assert ready.run is True
    assert ready.reason == "pressure"
    controller.admit(ready, now_ms=now)

    cooled = controller.decide(
        pending_entries=5,
        last_activity_ms=now - 21 * 60_000,
        now_ms=now + 60_000,
    )
    assert cooled.run is False
    assert cooled.reason == "cooldown"


def test_manual_request_selects_workload_and_is_consumed(tmp_path: Path) -> None:
    controller = DreamTriggerController(tmp_path, _config(), PermissionManager(Config()))
    controller.request(DREAM_GOVERNANCE)

    decision = controller.decide(
        pending_entries=1,
        last_activity_ms=999_999_999,
        now_ms=1_000_000_000,
    )
    assert decision.run is True
    assert decision.reason == "manual"
    assert decision.workload == DREAM_GOVERNANCE

    controller.admit(decision, now_ms=1_000_000_000)
    assert controller.request_path.exists() is False


def test_runtime_dream_tools_are_read_only(tmp_path: Path) -> None:
    tools = build_dream_tools(tmp_path, PermissionManager(Config()))

    assert tools.tool_names == ["read_file"]
    tool, _params, error = tools.prepare_call(
        "write_file",
        {"path": "USER.md", "content": "x"},
    )
    assert tool is None
    assert error is not None


def test_prepare_is_trigger_gated_and_does_not_advance_cursor(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    for index in range(1, 4):
        store.append_history(f"item {index}")

    controller = DreamTriggerController(tmp_path, _config(), PermissionManager(Config()))
    controller.request(DREAM_CONSOLIDATION)
    batch = controller.prepare(store)

    assert batch is not None
    assert batch.run.source_revision == 3
    assert "This run is read-only" in batch.prompt
    assert "source_revision: 3" in batch.prompt
    assert store.get_last_dream_cursor() == 0


def test_parse_dream_result_maps_proposal_capability_to_approval_policy() -> None:
    run = DreamRunContext(
        run_id="run-approval",
        workload=DREAM_CONSOLIDATION,
        source_revision=1,
        started_at_ms=1,
    )
    result = parse_dream_result(json.dumps({
        "summary": "proposal check",
        "proposals": [
            {"kind": "specialist_candidate", "summary": "candidate", "impact_level": "low"},
            {"kind": "memory_write", "summary": "note", "impact_level": "low"},
        ],
    }), run=run, permissions=PermissionManager(Config()))

    assert result["proposals"][0]["requires_user_approval"] is True
    assert result["proposals"][0]["state"] == "awaiting_user_approval"
    assert result["proposals"][1]["requires_user_approval"] is False
    assert result["proposals"][1]["state"] == "pending"


def test_parse_dream_result_routes_high_impact_to_user_approval() -> None:
    run = DreamRunContext(
        run_id="run-1",
        workload=DREAM_CONSOLIDATION,
        source_revision=9,
        started_at_ms=1,
    )
    result = parse_dream_result(json.dumps({
        "summary": "Repeated CI work may justify specialization.",
        "findings": [{
            "kind": "pattern",
            "summary": "CI repair recurs",
            "evidence_refs": ["history:7", "history:9"],
            "confidence": 0.9,
        }],
        "proposals": [{
            "kind": "specialist_candidate",
            "summary": "Consider a CI maintainer specialist",
            "rationale": "Repeated stable workflow",
            "evidence_refs": ["history:7", "history:9"],
            "confidence": 0.8,
            "impact_level": "high",
            "reversible": False,
            "proposed_action": {"name": "ci-maintainer"},
        }],
    }), run=run, permissions=PermissionManager(Config()))

    proposal = result["proposals"][0]
    assert proposal["state"] == "awaiting_user_approval"
    assert proposal["requires_user_approval"] is True
    assert proposal["kind"] == "specialist_candidate"
    assert result["metadata"] == {
        "side_effects": "none",
        "operational_authority": "main",
        "high_impact_authority": "user",
    }


async def _cancel_worker_sleep(_seconds: float) -> None:
    raise asyncio.CancelledError


def _worker_agent(tmp_path: Path, config: Config, memory: MemoryStore) -> SimpleNamespace:
    runtime = SimpleNamespace(
        model_id="dream-model",
        provider=SimpleNamespace(provider_name="test"),
    )
    resolver = SimpleNamespace(
        runtime=SimpleNamespace(model_id="main"),
        resolve_selection=MagicMock(return_value=runtime),
    )
    process_direct = AsyncMock(
        return_value=SimpleNamespace(
            content=json.dumps(
                {
                    "summary": "Validated Dream result.",
                    "findings": [],
                    "proposals": [],
                }
            ),
            metadata={"_stop_reason": "completed"},
        )
    )
    management = SimpleNamespace(
        config_snapshot=lambda: config,
        fleet_recommend=AsyncMock(return_value={
            "status": "ok",
            "recommended": {"model_id": "dream-model"},
        }),
    )
    return SimpleNamespace(
        workspace=tmp_path,
        model_management=management,
        runtime_resolver=resolver,
        permissions=PermissionManager(lambda: config),
        context=SimpleNamespace(memory=memory),
        process_direct=process_direct,
    )


@pytest.mark.asyncio
async def test_background_worker_executes_read_only_dream_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    config.agents.defaults.dream.pressure_entries = 1
    config.agents.defaults.dream.idle_minutes = 0
    memory = MemoryStore(tmp_path)
    memory.write_memory("canonical memory")
    revision = memory.append_history("background evidence")
    agent = _worker_agent(tmp_path, config, memory)
    monkeypatch.setattr(
        "nanobot.agent.dream_worker.asyncio.sleep",
        _cancel_worker_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await run_dream_worker(agent)

    assert memory.get_last_dream_cursor() == revision
    assert memory.read_memory() == "canonical memory"
    call = agent.process_direct.await_args
    assert call is not None
    assert call.kwargs["channel"] == "dream"
    assert call.kwargs["tools"].tool_names == ["read_file"]
    row = json.loads(
        (tmp_path / "memory" / "dream_results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    assert row["metadata"]["side_effects"] == "none"
    assert row["metadata"]["operational_authority"] == "main"
    assert row["metadata"]["model_id"] == "dream-model"
    assert "model_preset" not in row["metadata"]


@pytest.mark.asyncio
async def test_manual_dream_request_is_consumed_by_same_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    config.agents.defaults.dream.pressure_entries = 500
    config.agents.defaults.dream.idle_minutes = 24 * 60
    memory = MemoryStore(tmp_path)
    revision = memory.append_history("manual evidence")
    agent = _worker_agent(tmp_path, config, memory)
    msg = InboundMessage(
        channel="cli",
        sender_id="u1",
        chat_id="direct",
        content="/dream",
    )
    ctx = CommandContext(
        msg=msg,
        session=None,
        key=msg.session_key,
        raw="/dream",
        args="",
        loop=agent,
    )

    queued = await cmd_dream(ctx)
    assert "request queued" in queued.content.lower()
    assert (tmp_path / "memory" / ".dream_request.json").exists()

    monkeypatch.setattr(
        "nanobot.agent.dream_worker.asyncio.sleep",
        _cancel_worker_sleep,
    )
    with pytest.raises(asyncio.CancelledError):
        await run_dream_worker(agent)

    assert agent.process_direct.await_count == 1
    assert memory.get_last_dream_cursor() == revision
    assert not (tmp_path / "memory" / ".dream_request.json").exists()
    state = json.loads(
        (tmp_path / "memory" / ".dream_state.json").read_text(encoding="utf-8")
    )
    assert state["last_reason"] == "manual"


@pytest.mark.asyncio
async def test_dream_routing_never_falls_back_to_main_implicitly() -> None:
    resolver = MagicMock()
    resolver.runtime = object()
    recommend = AsyncMock(return_value={"status": "error", "message": "no candidates"})

    with pytest.raises(RuntimeError, match="no eligible model route"):
        await resolve_dream_runtime(
            DREAM_CONSOLIDATION,
            config=DreamConfig(),
            runtime_resolver=resolver,
            fleet_recommend=recommend,
        )
    resolver.resolve_selection.assert_not_called()


@pytest.mark.asyncio
async def test_dream_explicit_model_id_bypasses_fleet() -> None:
    resolver = MagicMock()
    resolver.runtime = object()
    resolved = object()
    resolver.resolve_selection.return_value = resolved
    recommend = AsyncMock()

    assert await resolve_dream_runtime(
        DREAM_CONSOLIDATION,
        config=DreamConfig(model_id="dream-explicit"),
        runtime_resolver=resolver,
        fleet_recommend=recommend,
    ) is resolved
    recommend.assert_not_awaited()
    resolver.resolve_selection.assert_called_once_with(
        resolver.runtime,
        model_id="dream-explicit",
    )


@pytest.mark.asyncio
async def test_dream_fleet_then_fallback_use_model_id_and_workload_pool() -> None:
    resolver = MagicMock()
    resolver.runtime = object()
    resolver.resolve_selection.side_effect = ["fleet-runtime", "fallback-runtime"]
    config = DreamConfig(
        fallback_model_id="fallback-model",
        pools={DREAM_CONSOLIDATION: "dream-cheap"},
    )
    recommend = AsyncMock(return_value={
        "status": "ok",
        "recommended": {"model_id": "fleet-model"},
    })

    assert await resolve_dream_runtime(
        DREAM_CONSOLIDATION,
        config=config,
        runtime_resolver=resolver,
        fleet_recommend=recommend,
    ) == "fleet-runtime"
    recommend.assert_awaited_once_with(
        pool="dream-cheap",
        task_type="background",
        min_context_tokens=16_000,
    )
    resolver.resolve_selection.assert_called_once_with(
        resolver.runtime,
        model_id="fleet-model",
    )

    resolver.resolve_selection.reset_mock()
    recommend = AsyncMock(return_value={"status": "error"})
    assert await resolve_dream_runtime(
        DREAM_CONSOLIDATION,
        config=config,
        runtime_resolver=resolver,
        fleet_recommend=recommend,
    ) == "fallback-runtime"
    resolver.resolve_selection.assert_called_once_with(
        resolver.runtime,
        model_id="fallback-model",
    )


def test_fleet_has_no_dream_specific_scoring_and_dream_is_lowest_priority() -> None:
    assert ModelFleetManager._weights(DREAM_CONSOLIDATION) == ModelFleetManager._weights("general")
    assert ModelAdmissionController._rank("dream") > ModelAdmissionController._rank("background")
