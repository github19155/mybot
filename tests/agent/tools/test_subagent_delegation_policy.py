"""Tests for nonblocking subagent delegation guidance."""

from importlib.resources import files as pkg_files
from unittest.mock import MagicMock

from nanobot.agent.tools.subagent import SubagentTool


def test_tool_contract_prefers_background_subagents_for_long_work():
    content = (
        pkg_files("nanobot") / "templates" / "agent" / "tool_contract.md"
    ).read_text(encoding="utf-8")

    assert "ordinary delegated worker execution should start in the background" in content
    assert "wait=false" in content
    assert "filesystem, shell, web, browser, code, build, test, and multi-step work" in content
    assert "Background results arrive automatically" in content
    assert "Do not repeatedly poll `status` or sleep-and-check" in content
    assert "wait=true" in content
    assert "trivial, near-instant child checks" in content


def test_subagent_tool_description_exposes_async_only_policy():
    description = SubagentTool(MagicMock()).description

    assert "Every run is asynchronous" in description
    assert "returns immediately" in description
    assert "task identifier" in description
    assert "Background results are delivered automatically" in description
    assert "do not poll or sleep-and-check" in description
    assert "Children cannot create children" in description
