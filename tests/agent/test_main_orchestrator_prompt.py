from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import ContextBuilder
from nanobot.agent.permissions import PermissionManager
from nanobot.agent.subagent import SubagentManager
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config


def _worker_prompt(tmp_path: Path, *, role: str = "general") -> str:
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        permission_manager=PermissionManager(Config()),
    )
    return manager._build_subagent_prompt(role=role)


def test_rendered_main_prompt_is_control_plane_only(tmp_path: Path) -> None:
    prompt = ContextBuilder(tmp_path).build_system_prompt()

    assert "# Main Orchestration Contract" in prompt
    assert "Main does not perform operational execution itself" in prompt
    assert "Every Worker dispatch is asynchronous" in prompt
    assert "new Main turn" in prompt
    assert "Tool Catalog" in prompt
    assert "Main-callable tools" in prompt
    assert "Prefer a matching active Specialist" in prompt
    assert "Use a WorkAgent" in prompt
    assert "per-turn orchestration-call budget" in prompt

    assert "# Worker Execution Contract" not in prompt
    assert "## File and Coding Workflows" not in prompt
    assert "`grep` returns matches with five context lines by default" not in prompt
    assert "Join them when using `read_file`" not in prompt
    assert "wait=true" not in prompt
    assert "wait=false" not in prompt


def test_rendered_worker_prompt_gets_common_execution_contract_and_skills(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: review\ndescription: Review repository changes.\n---\n\nReview carefully.",
        encoding="utf-8",
    )

    prompt = _worker_prompt(tmp_path)

    assert prompt.count("# Worker Execution Contract") == 1
    assert "locate, inspect, edit, then verify" in prompt
    assert "run the smallest reliable tests/checks" in prompt
    assert "read the full `SKILL.md`" in prompt
    assert "review" in prompt
    assert "Join them when using `read_file`" in prompt
    assert "Runtime permission policy" not in prompt  # Main wording must not leak in.


def test_common_execution_contract_is_shared_by_general_and_specialist(tmp_path: Path) -> None:
    general = _worker_prompt(tmp_path, role="general")
    specialist = _worker_prompt(tmp_path, role="coder")

    marker = "# Worker Execution Contract"
    assert general.count(marker) == 1
    assert specialist.count(marker) == 1
    assert "## Code, Tests, and Artifacts" in general
    assert "## Code, Tests, and Artifacts" in specialist


def test_explicit_skill_payload_is_worker_only_for_main(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: review\ndescription: Review changes.\n---\n\nRun the execution checklist.",
        encoding="utf-8",
    )

    messages = ContextBuilder(tmp_path).build_messages([], "Please $review this patch.")
    user_content = str(messages[-1]["content"])

    assert "[Worker-only skill guidance" in user_content
    assert "Main must not execute it directly" in user_content
    assert "Run the execution checklist." in user_content
