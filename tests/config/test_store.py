"""Tests for path-scoped configuration persistence."""

from __future__ import annotations

import threading
from pathlib import Path

from nanobot.config.store import ConfigStore


def test_store_uses_explicit_path_and_creates_parent(tmp_path: Path) -> None:
    config_path = tmp_path / "nested" / "config.json"

    store = ConfigStore(config_path)

    assert store.path == config_path.resolve()
    assert config_path.parent.is_dir()
    assert store.load().source_path == store.path


def test_update_persists_mutation_and_returns_result(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")

    def mutate(config: object) -> str:
        config.gateway.port = 19001  # type: ignore[attr-defined]
        return "updated"

    assert store.update(mutate) == "updated"
    assert store.load().gateway.port == 19001


def test_run_serialized_blocks_another_store_for_same_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    first = ConfigStore(config_path)
    second = ConfigStore(config_path)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def hold_first(_: Path) -> None:
        first_entered.set()
        release_first.wait(timeout=2)

    def enter_second(_: Path) -> None:
        second_entered.set()

    first_thread = threading.Thread(target=lambda: first.run_serialized(hold_first))
    second_thread = threading.Thread(target=lambda: second.run_serialized(enter_second))

    first_thread.start()
    assert first_entered.wait(timeout=1)
    second_thread.start()
    assert not second_entered.wait(timeout=0.1)

    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert second_entered.is_set()
