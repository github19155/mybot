"""Regression coverage for Dream specialist evidence and evolution history."""

from __future__ import annotations

import json

import pytest

from nanobot.agent.subagent_role_storage import candidate_entries
from nanobot.agent.tools.dream_roles import DreamRoleTool
from nanobot.config.schema import Config


def _config(tmp_path) -> Config:
    return Config(agents={"defaults": {"workspace": str(tmp_path)}})


@pytest.mark.asyncio
async def test_same_occurrence_cannot_satisfy_repeated_evidence_threshold(tmp_path) -> None:
    tool = DreamRoleTool(tmp_path, config=_config(tmp_path))

    first = json.loads(await tool.execute(
        action="observe",
        role="release-triage",
        responsibility="release readiness triage",
        evidence="checked release readiness and blockers",
        occurrence="task:release-alpha",
    ))
    reworded_same_task = json.loads(await tool.execute(
        action="observe",
        role="release-triage",
        responsibility="release readiness triage",
        evidence="reviewed the same alpha release for readiness gaps",
        occurrence="task:release-alpha",
    ))
    second_task = json.loads(await tool.execute(
        action="observe",
        role="release-triage",
        responsibility="release readiness triage",
        evidence="checked beta release readiness and blockers",
        occurrence="task:release-beta",
    ))

    assert first["evidence_count"] == 1
    assert reworded_same_task["evidence_count"] == 1
    assert second_task["evidence_count"] == 2
    assert second_task["occurrences"] == ["task:release-alpha", "task:release-beta"]
    assert candidate_entries(tmp_path)["release-triage"]["evidence_count"] == 2


@pytest.mark.asyncio
async def test_specialist_evolution_history_appends_across_updates(tmp_path) -> None:
    tool = DreamRoleTool(tmp_path, config=_config(tmp_path))
    for occurrence in ("task:one", "task:two"):
        await tool.execute(
            action="observe",
            role="release-triage",
            responsibility="release readiness triage",
            evidence=f"release review from {occurrence}",
            occurrence=occurrence,
        )

    created = json.loads(await tool.execute(
        action="create",
        role="release-triage",
        values={
            "description": "Triage recurring releases.",
            "system_prompt": "Check release readiness.",
            "tools": ["read_file"],
            "evolution": ["v1: created from repeated release checks"],
        },
    ))
    updated = json.loads(await tool.execute(
        action="update",
        role="release-triage",
        values={
            "description": "Triage recurring releases with test evidence.",
            "system_prompt": "Check release readiness and test evidence.",
            "tools": ["read_file", "exec"],
            "evolution": ["v2: add test execution after repeated gaps"],
        },
    ))

    assert created["evolution"] == ["v1: created from repeated release checks"]
    assert updated["evolution"] == [
        "v1: created from repeated release checks",
        "v2: add test execution after repeated gaps",
    ]
    assert updated["version"] == 2
