"""Tests for the current MemoryStore storage and history-journal contract."""

import json
from pathlib import Path

import pytest

from nanobot.agent.memory import _HISTORY_ENTRY_HARD_CAP, MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


class TestMemoryStoreBasicIO:
    def test_read_memory_returns_empty_when_missing(self, store):
        assert store.read_memory() == ""

    def test_write_and_read_memory(self, store):
        store.write_memory("hello")
        assert store.read_memory() == "hello"

    def test_read_soul_returns_empty_when_missing(self, store):
        assert store.read_soul() == ""

    def test_write_and_read_soul(self, store):
        store.write_soul("soul content")
        assert store.read_soul() == "soul content"

    def test_read_user_returns_empty_when_missing(self, store):
        assert store.read_user() == ""

    def test_write_and_read_user(self, store):
        store.write_user("user content")
        assert store.read_user() == "user content"

    def test_get_memory_context_returns_empty_when_missing(self, store):
        assert store.get_memory_context() == ""

    def test_get_memory_context_returns_formatted_content(self, store):
        store.write_memory("important fact")
        ctx = store.get_memory_context()
        assert "Long-term Memory" in ctx
        assert "important fact" in ctx


class TestHistoryJournal:
    def test_append_history_allocates_persistent_cursors(self, store):
        assert store.append_history("event 1") == 1
        assert store.append_history("event 2") == 2
        assert store.append_history("event 3") == 3

        stored = MemoryStore(store.workspace)
        assert stored.append_history("event 4") == 4

    def test_append_history_persists_session_key(self, store):
        cursor = store.append_history("event 1", session_key="telegram:chat-1")
        data = json.loads(store.read_file(store.history_file))
        assert data == {
            "cursor": cursor,
            "timestamp": data["timestamp"],
            "content": "event 1",
            "session_key": "telegram:chat-1",
        }

    def test_append_history_strips_thinking_content(self, store):
        cursor = store.append_history("<think>reasoning</think>final answer")
        data = json.loads(store.read_file(store.history_file))
        assert data["cursor"] == cursor
        assert data["content"] == "final answer"

    def test_append_history_drops_pure_leak_content(self, store):
        cursor = store.append_history("<think>nothing user-facing</think>")
        data = json.loads(store.read_file(store.history_file))
        assert data["cursor"] == cursor
        assert data["content"] == ""

    def test_append_history_drops_malformed_leak_prefix(self, store):
        cursor = store.append_history("<channel|>")
        data = json.loads(store.read_file(store.history_file))
        assert data["cursor"] == cursor
        assert data["content"] == ""

    def test_read_unprocessed_history_uses_cursor_boundary(self, store):
        store.append_history("event 1")
        store.append_history("event 2")
        store.append_history("event 3")
        entries = store.read_unprocessed_history(since_cursor=1)
        assert [entry["cursor"] for entry in entries] == [2, 3]

    def test_read_unprocessed_skips_invalid_entries(self, store):
        store.history_file.write_text(
            '{"timestamp": "2026-04-01 10:00", "content": "no cursor"}\n'
            '{"cursor": 1, "timestamp": "2026-04-01 10:00", "content": "valid"}\n'
            '{"cursor": 2, "timestamp": "2026-04-01 10:01"}\n'
            '{"cursor": 3, "content": "missing timestamp"}\n'
            '{"cursor": 4, "timestamp": "2026-04-01 10:03", "content": 123}\n'
            '{"cursor": 5, "timestamp": "2026-04-01 10:04", "content": "bad session", "session_key": 42}\n'
            '{"cursor": 6, "timestamp": "2026-04-01 10:05", "content": "also valid", "session_key": "telegram:chat-1"}\n',
            encoding="utf-8",
        )
        entries = store.read_unprocessed_history(since_cursor=0)
        assert [entry["cursor"] for entry in entries] == [1, 6]
        assert [entry["content"] for entry in entries] == ["valid", "also valid"]

    def test_next_cursor_falls_back_when_tail_has_no_cursor(self, store):
        store.history_file.write_text(
            '{"timestamp": "2026-04-01 10:01", "content": "no cursor"}\n',
            encoding="utf-8",
        )
        store._cursor_file.unlink(missing_ok=True)
        assert store.append_history("new event") == 1

    def test_append_history_allocates_unique_cursors_under_concurrent_writes(self, store):
        import threading

        writers = 16
        start = threading.Barrier(writers)
        cursors: list[int] = []
        lock = threading.Lock()

        def worker(index):
            start.wait()
            cursor = store.append_history(f"event {index}")
            with lock:
                cursors.append(cursor)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(writers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sorted(cursors) == list(range(1, writers + 1))
        persisted = store.read_unprocessed_history(since_cursor=0)
        assert sorted(entry["cursor"] for entry in persisted) == list(range(1, writers + 1))

    def test_compact_history_drops_oldest_processed_entries(self, tmp_path):
        store = MemoryStore(tmp_path, max_history_entries=2)
        for index in range(1, 6):
            store.append_history(f"event {index}")
        store.set_last_dream_cursor(5)

        store.compact_history()

        entries = store.read_unprocessed_history(since_cursor=0)
        assert [entry["cursor"] for entry in entries] == [4, 5]

    def test_compact_history_preserves_pending_consumer_entries(self, tmp_path):
        store = MemoryStore(tmp_path, max_history_entries=50)
        for index in range(1, 101):
            store.append_history(f"event {index}")
        store.set_last_dream_cursor(20)

        store.compact_history()

        entries = store.read_unprocessed_history(since_cursor=0)
        assert [entry["cursor"] for entry in entries] == list(range(21, 101))

    def test_write_entries_uses_atomic_write(self, tmp_path):
        store = MemoryStore(tmp_path)
        store.append_history("event 1")
        store.append_history("event 2")
        entries = store.read_unprocessed_history(since_cursor=0)
        temp = store.history_file.with_suffix(".jsonl.tmp")

        store._write_entries(entries)

        assert not temp.exists()
        assert store.history_file.exists()

    def test_write_entries_cleans_up_tmp_on_exception(self, tmp_path, monkeypatch):
        store = MemoryStore(tmp_path)
        store.append_history("event 1")
        entries = store.read_unprocessed_history(since_cursor=0)
        temp = store.history_file.with_suffix(".jsonl.tmp")

        def failing_replace(*args, **kwargs):
            raise RuntimeError("Simulated failure")

        monkeypatch.setattr("os.replace", failing_replace)
        with pytest.raises(RuntimeError):
            store._write_entries(entries)

        assert not temp.exists()
        assert store.history_file.exists()


class TestAppendHistoryHardCap:
    def test_oversized_entry_is_truncated(self, store):
        huge = "x" * (_HISTORY_ENTRY_HARD_CAP + 10_000)
        store.append_history(huge)
        entry = store.read_unprocessed_history(since_cursor=0)[0]
        assert len(entry["content"]) <= _HISTORY_ENTRY_HARD_CAP + 50

    def test_oversize_warning_is_emitted_once(self, store, monkeypatch):
        records: list[str] = []
        monkeypatch.setattr(
            "nanobot.agent.memory.logger.warning",
            lambda message, *args: records.append(message.format(*args)),
        )
        huge = "x" * (_HISTORY_ENTRY_HARD_CAP + 1)
        store.append_history(huge)
        store.append_history(huge)
        store.append_history(huge)

        warnings = [record for record in records if "exceeds" in record and "chars" in record]
        assert len(warnings) == 1

    def test_custom_max_chars_overrides_default(self, store):
        store.append_history("a" * 500, max_chars=100)
        entry = store.read_unprocessed_history(since_cursor=0)[0]
        assert len(entry["content"]) <= 150

    def test_normal_sized_entries_are_unchanged(self, store):
        message = "normal short entry"
        store.append_history(message)
        entry = store.read_unprocessed_history(since_cursor=0)[0]
        assert entry["content"] == message


def test_history_skips_non_dict_jsonl_lines(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path)
    memory.history_file.write_text(
        "\n".join([
            "null",
            "[1, 2]",
            "true",
            json.dumps({
                "cursor": 1,
                "timestamp": "2026-01-01T00:00:00",
                "content": "kept",
                "session_key": "cli:t",
            }),
            "",
        ]),
        encoding="utf-8",
    )
    entries = memory.read_unprocessed_history(since_cursor=0)
    assert entries == [{
        "cursor": 1,
        "timestamp": "2026-01-01T00:00:00",
        "content": "kept",
        "session_key": "cli:t",
    }]
    assert memory.append_history("next", session_key="cli:t") == 2


def test_raw_archive_handles_none_timestamp_and_missing_role(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path)
    messages = [
        {"content": "message with none timestamp", "timestamp": None, "role": "user"},
        {"content": "message with int timestamp", "timestamp": 1720000000, "role": "assistant"},
        {"content": "message with missing role", "timestamp": "2026-07-28T12:00:00"},
    ]
    memory.raw_archive(messages, session_key="cli:test")
    raw_history = memory.history_file.read_text(encoding="utf-8")
    assert "[?] USER: message with none timestamp" in raw_history
    assert "[1720000000] ASSISTANT: message with int timestamp" in raw_history
    assert "[2026-07-28T12:00] UNKNOWN: message with missing role" in raw_history
