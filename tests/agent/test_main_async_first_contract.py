from __future__ import annotations

import inspect
from pathlib import Path

from nanobot.agent.tools.subagent import _SUBAGENT_PARAMETERS, SubagentTool


def test_main_prompt_contract_is_pure_async_orchestration() -> None:
    contract = Path("nanobot/templates/agent/tool_contract.md").read_text(encoding="utf-8")

    assert "# Main Orchestration Contract" in contract
    assert "Every Worker dispatch is asynchronous" in contract
    assert "reply to the user immediately" in contract
    assert "new Main turn" in contract
    assert "no special per-turn orchestration-call budget" in contract
    assert "wait=true" not in contract
    assert "wait=false" not in contract


def test_legacy_subagent_wait_defaults_to_background_until_runtime_integration() -> None:
    """Runtime removal of wait belongs to the tool-registration integration change."""
    wait_schema = _SUBAGENT_PARAMETERS["properties"]["wait"]

    assert wait_schema["default"] is False
    assert inspect.signature(SubagentTool.execute).parameters["wait"].default is False


def test_subagent_description_still_exposes_legacy_wait_until_runtime_integration() -> None:
    """Prompt/docs must not depend on this legacy runtime field while it still exists."""
    tool = SubagentTool(manager=None)  # type: ignore[arg-type]

    assert "wait" in tool.description


def test_subagent_prompt_defines_semantic_milestones() -> None:
    prompt = Path("nanobot/templates/agent/subagent_system.md").read_text(encoding="utf-8")

    assert "report_progress" in prompt
    assert "meaningful stages" in prompt
    assert "no more than three milestones" in prompt
    assert "final response remains separate" in prompt
