"""Dream control-plane invariants for the native single-path implementation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nanobot.agent.dream import (
    DREAM_CONSOLIDATION,
    DREAM_GOVERNANCE,
    DreamRunContext,
    DreamTriggerController,
    build_dream_tools,
    parse_dream_result,
)
from nanobot.agent.memory import MemoryStore
from nanobot.agent.model_management import ModelManagement
from nanobot.agent.permissions import PermissionManager
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


@pytest.mark.asyncio
async def test_dream_routing_never_falls_back_to_main_implicitly() -> None:
    management = ModelManagement(Config())
    management.fleet_recommend = AsyncMock(
        return_value={"status": "error", "message": "no candidates"},
    )

    with pytest.raises(RuntimeError, match="no eligible model route"):
        await management.resolve_dream_runtime(DREAM_CONSOLIDATION)


def test_fleet_has_no_dream_specific_scoring_and_dream_is_lowest_priority() -> None:
    assert ModelFleetManager._weights(DREAM_CONSOLIDATION) == ModelFleetManager._weights("general")
    assert ModelAdmissionController._rank("dream") > ModelAdmissionController._rank("background")
