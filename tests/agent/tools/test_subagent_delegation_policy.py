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


def test_subagent_tool_description_exposes_nonblocking_policy():
    description = SubagentTool(MagicMock()).description

    assert "Main is async-first" in description
    assert "wait=false" in description
    assert "filesystem, shell, web, browser" in description
    assert "code, build, test, and other multi-step work" in description
    assert "background results are delivered automatically" in description
    assert "do not repeatedly poll status or sleep-and-check" in description
    assert "wait=true only for trivial near-instant child work" in description
