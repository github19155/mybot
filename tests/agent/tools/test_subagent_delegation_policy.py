"""Tests for nonblocking subagent delegation guidance."""

from importlib.resources import files as pkg_files
from unittest.mock import MagicMock

from nanobot.agent.tools.subagent import SubagentTool


def test_tool_contract_prefers_background_subagents_for_long_work():
    content = (
        pkg_files("nanobot") / "templates" / "agent" / "tool_contract.md"
    ).read_text(encoding="utf-8")

    assert "more than about 10 seconds" in content
    assert "installs or dependency downloads" in content
    assert "builds" in content
    assert "broad test suites" in content
    assert "wait=false" in content
    assert "Do not repeatedly" in content
    assert "poll `status`" in content
    assert "wait=true" in content


def test_subagent_tool_description_exposes_nonblocking_policy():
    description = SubagentTool(MagicMock()).description

    assert "wait=false" in description
    assert "installs/downloads" in description
    assert "broad test suites" in description
    assert "more than about 10 seconds" in description
    assert "do not repeatedly poll status" in description
    assert "wait=true" in description
