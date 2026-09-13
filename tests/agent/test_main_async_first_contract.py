from __future__ import annotations

import inspect
from pathlib import Path

from nanobot.agent.tools.subagent import SubagentTool, _SUBAGENT_PARAMETERS


def test_main_tool_contract_is_async_first() -> None:
    contract = Path("nanobot/templates/agent/tool_contract.md").read_text(encoding="utf-8")

    assert "Main is async-first" in contract
    assert "wait=false" in contract
    assert "reply to the user immediately" in contract
    assert "Do not use `wait=true` merely because the eventual response needs the child result" in contract


def test_subagent_wait_defaults_to_background() -> None:
    wait_schema = _SUBAGENT_PARAMETERS["properties"]["wait"]

    assert wait_schema["default"] is False
    assert "async-first" in wait_schema["description"]
    assert inspect.signature(SubagentTool.execute).parameters["wait"].default is False


def test_subagent_description_reserves_wait_for_near_instant_work() -> None:
    tool = SubagentTool(manager=None)  # type: ignore[arg-type]

    assert "Main is async-first" in tool.description
    assert "normal delegated work should run with wait=false" in tool.description
    assert "wait=true only for trivial near-instant child work" in tool.description
