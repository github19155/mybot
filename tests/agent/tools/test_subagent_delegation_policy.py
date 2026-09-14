"""Tests for asynchronous Worker delegation guidance."""

from importlib.resources import files as pkg_files
from unittest.mock import MagicMock

from nanobot.agent.tools.subagent import SubagentTool


def test_tool_contract_requires_async_worker_dispatch_and_new_main_turn():
    content = (
        pkg_files("nanobot") / "templates" / "agent" / "tool_contract.md"
    ).read_text(encoding="utf-8")

    assert "Every Worker dispatch is asynchronous" in content
    assert "end the current Main turn" in content
    assert "Do not poll Worker status to wait for completion" in content
    assert "new Main turn" in content
    assert "wait=true" not in content
    assert "wait=false" not in content


def test_subagent_tool_description_exposes_async_only_policy():
    description = SubagentTool(MagicMock()).description

    assert "Every run is asynchronous" in description
    assert "returns immediately" in description
    assert "task identifier" in description
    assert "Background results are delivered automatically" in description
    assert "do not poll or sleep-and-check" in description
    assert "Children cannot create children" in description
