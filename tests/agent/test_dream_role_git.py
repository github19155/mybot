"""Regression coverage for Dream specialist state in the memory Git store."""

from nanobot.agent.memory import MemoryStore
from nanobot.agent.subagent_role_storage import write_dream_role
from nanobot.utils.gitstore import GitStore


def _role_payload(name: str) -> dict[str, object]:
    return {
        "name": name,
        "description": "Handle recurring release triage.",
        "system_prompt": "Check release readiness and report evidence.",
        "tools": ["read_file"],
        "status": "active",
        "version": 1,
        "created_by": "dream",
    }


def test_dream_role_state_is_versioned_in_new_memory_store(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.write_soul("# Soul\n")
    store.write_user("# User\n")
    store.write_memory("# Memory\n")
    store.git.init()

    write_dream_role(tmp_path, "release-triage", _role_payload("release-triage"))

    diff = store.dream_content_diff()
    assert "agents/roles.json" in diff
    assert store.git.auto_commit(
        MemoryStore.build_dream_commit_message("dream: role evolution", diff)
    ) is not None
    assert store.dream_content_diff() == ""


def test_existing_memory_git_store_can_start_tracking_role_state(tmp_path) -> None:
    # Simulate a workspace initialized before agents/roles.json became tracked.
    legacy = GitStore(
        tmp_path,
        tracked_files=[
            "SOUL.md",
            "USER.md",
            "memory/MEMORY.md",
            "memory/.dream_cursor",
        ],
    )
    legacy.init()

    store = MemoryStore(tmp_path)
    assert store.git.init() is False
    write_dream_role(tmp_path, "release-triage", _role_payload("release-triage"))

    diff = store.dream_content_diff()
    assert "agents/roles.json" in diff
    assert store.git.auto_commit(
        MemoryStore.build_dream_commit_message("dream: role evolution", diff)
    ) is not None
    assert store.dream_content_diff() == ""
