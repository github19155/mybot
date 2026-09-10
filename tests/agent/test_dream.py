"""Tests for Dream prompt construction and cursor management."""

import pytest

from nanobot.agent.memory import MemoryStore
from nanobot.utils.prompt_templates import render_template


@pytest.fixture
def store(tmp_path):
    memory = MemoryStore(tmp_path)
    memory.write_soul("# Soul\n- Helpful")
    memory.write_memory("# Memory\n- Project X active")
    return memory


class TestBuildDreamPrompt:
    def test_returns_none_when_no_history(self, store):
        assert store.build_dream_prompt() is None

    def test_returns_prompt_with_history(self, store):
        store.append_history("hello")

        result = store.build_dream_prompt()

        assert result is not None
        prompt, cursor = result
        assert cursor > 0
        assert "## Conversation History" in prompt
        assert "hello" in prompt

    def test_cursor_advances_only_after_runtime_commits_it(self, store):
        store.append_history("first")
        first = store.build_dream_prompt()
        assert first is not None
        _, first_cursor = first

        assert store.build_dream_prompt() is not None
        store.set_last_dream_cursor(first_cursor)
        assert store.build_dream_prompt() is None

        store.append_history("second")
        second = store.build_dream_prompt()
        assert second is not None
        _, second_cursor = second
        assert second_cursor > first_cursor

    def test_prompt_declares_authority_and_read_only_boundary(self, store):
        store.append_history("test")

        result = store.build_dream_prompt()

        assert result is not None
        prompt, _ = result
        assert "Main owns operational/runtime control" in prompt
        assert "User is the highest governance authority" in prompt
        assert "Read-only analysis only" in prompt
        assert "structured findings and proposals" in prompt

    def test_prompt_does_not_duplicate_current_memory_file_contents(self, store):
        store.append_history("hello")

        result = store.build_dream_prompt()

        assert result is not None
        prompt, _ = result
        assert "## Current Memory Files" not in prompt
        assert "Project X active" not in prompt
        assert "Helpful" not in prompt

    def test_workspace_dream_prompt_overrides_default(self, store):
        store.dream_prompt_file.parent.mkdir(parents=True)
        store.dream_prompt_file.write_text("Custom Dream prompt.", encoding="utf-8")
        store.append_history("keep this fact")

        result = store.build_dream_prompt()

        assert result is not None
        prompt, _ = result
        assert prompt.startswith("Custom Dream prompt.")
        assert "background cognition and system-governance analysis layer" not in prompt
        assert "## Conversation History" in prompt
        assert "keep this fact" in prompt

    def test_workspace_dream_prompt_override_is_capped(self, store):
        store.dream_prompt_file.parent.mkdir(parents=True)
        store.dream_prompt_file.write_text("x" * 40_000, encoding="utf-8")
        store.append_history("keep this fact")

        result = store.build_dream_prompt()

        assert result is not None
        prompt, _ = result
        assert "x" * 40_000 not in prompt
        assert "... (truncated)" in prompt
        assert "## Conversation History" in prompt

    def test_empty_workspace_dream_prompt_uses_default(self, store):
        store.dream_prompt_file.parent.mkdir(parents=True)
        store.dream_prompt_file.write_text("  \n", encoding="utf-8")
        store.append_history("test")

        result = store.build_dream_prompt()

        assert result is not None
        prompt, _ = result
        assert "background cognition and system-governance analysis layer" in prompt
        assert "User is the highest governance authority" in prompt

    def test_truncates_long_entries_at_1000_chars(self, store):
        long_content = "x" * 2000
        store.append_history(long_content)

        result = store.build_dream_prompt()

        assert result is not None
        prompt, _ = result
        assert long_content not in prompt
        assert "x" * 1000 in prompt
        assert "x" * 1001 not in prompt

    def test_batches_oldest_unprocessed_entries_first(self, store):
        for index in range(25):
            store.append_history(f"entry-{index + 1:02d}")

        result = store.build_dream_prompt(max_entries=20)

        assert result is not None
        prompt, cursor = result
        assert cursor == 20
        assert "entry-01" in prompt
        assert "entry-20" in prompt
        assert "entry-21" not in prompt

        store.set_last_dream_cursor(cursor)
        next_result = store.build_dream_prompt(max_entries=20)
        assert next_result is not None
        next_prompt, next_cursor = next_result
        assert next_cursor == 25
        assert "entry-21" in next_prompt
        assert "entry-25" in next_prompt

    def test_skips_malformed_history_entries(self, store):
        store.history_file.write_text(
            '{"cursor": 1, "timestamp": "2026-04-01 10:00"}\n'
            '{"cursor": 2, "timestamp": "2026-04-01 10:01", "content": "usable memory"}\n',
            encoding="utf-8",
        )

        result = store.build_dream_prompt()

        assert result is not None
        prompt, cursor = result
        assert cursor == 2
        assert "usable memory" in prompt


def test_default_dream_prompt_defines_governed_proposal_contract():
    prompt = render_template("agent/dream.md", strip=True)

    assert "## Evidence rules" in prompt
    assert "Ground every non-trivial finding or proposal" in prompt
    assert "specialist_candidate" in prompt
    assert "Dream may autonomously think and propose" in prompt
    assert "may not autonomously decide what becomes canonical truth or action" in prompt
    assert "User is the highest governance authority" in prompt
    assert "Read-only analysis only" in prompt
