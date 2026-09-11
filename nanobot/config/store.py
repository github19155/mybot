"""Path-scoped configuration persistence."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from filelock import FileLock

from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config

_T = TypeVar("_T")


class ConfigStore:
    """Path-scoped config access with process-safe read-modify-write operations."""

    def __init__(self, config_path: Path) -> None:
        self.path = config_path.expanduser().resolve(strict=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")
        self._file_lock = FileLock(str(lock_path))

    def load(self) -> Config:
        """Load config from this store's explicit path."""
        with self._lock:
            return load_config(self.path)

    def update(self, mutation: Callable[[Config], _T]) -> _T:
        """Apply and atomically persist one path-scoped read-modify-write operation."""
        with self._lock, self._file_lock:
            config = load_config(self.path)
            result = mutation(config)
            save_config(config, self.path)
            return result

    def run_serialized(self, operation: Callable[[Path], _T]) -> _T:
        """Run a path-aware read-modify-write operation under the config-file lock."""
        with self._lock, self._file_lock:
            return operation(self.path)
