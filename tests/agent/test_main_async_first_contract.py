from __future__ import annotations

import inspect
from pathlib import Path

from nanobot.agent.tools.subagent import _SUBAGENT_PARAMETERS, SubagentTool


def test_main_tool_contract_dispatches_workers_asynchronously() -> None:
    contract = Path("nanobot/templates/agent/tool_contract.md").read_text(encoding="utf-8")

    assert "Every Worker dispatch is asynchronous" in contract
    assert "end the current Main turn" in contract
    assert "new Main turn" in contract


def test_subagent_run_has_no_wait_parameter() -> None:
    assert "wait" not in _SUBAGENT_PARAMETERS["properties"]
    assert "wait" not in inspect.signature(SubagentTool.execute).parameters


def test_subagent_description_is_async_only() -> None:
    tool = SubagentTool(manager=None)  # type: ignore[arg-type]

    assert "Every run is asynchronous" in tool.description
    assert "returns immediately with a task identifier" in tool.description
    assert "do not poll" in tool.description


def test_subagent_prompt_defines_semantic_milestones() -> None:
    prompt = Path("nanobot/templates/agent/subagent_system.md").read_text(encoding="utf-8")

    assert "report_progress" in prompt
    assert "meaningful stages" in prompt
    assert "no more than three milestones" in prompt
    assert "final response remains separate" in prompt
