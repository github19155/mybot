"""Persistence helpers for subagent role usage telemetry."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nanobot.config.schema import Config


ROLE_USAGE_FILE = Path("agents") / "role_usage.json"
ROLE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_USAGE_LOCK = threading.Lock()


def normalize_role_name(name: object) -> str:
    if not isinstance(name, str):
        raise ValueError("role name must be a string")
    normalized = name.strip().lower()
    if not ROLE_NAME_PATTERN.fullmatch(normalized):
        raise ValueError("role name must match [a-z][a-z0-9_-]{0,63}")
    return normalized


def workspace_from_config(config: "Config | None") -> Path | None:
    if config is None:
        return None
    return Path(config.workspace_path).expanduser().resolve()


def usage_state_path(workspace: Path) -> Path:
    return workspace.expanduser().resolve() / ROLE_USAGE_FILE


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def role_usage(workspace: Path, role: str) -> dict[str, Any]:
    name = normalize_role_name(role)
    payload = read_json_object(usage_state_path(workspace))
    value = payload.get(name)
    return dict(value) if isinstance(value, dict) else {}


def record_role_use(workspace: Path, role: str) -> None:
    """Record an accepted role launch for lifecycle/value analysis."""
    name = normalize_role_name(role)
    path = usage_state_path(workspace)
    with _USAGE_LOCK:
        payload = read_json_object(path)
        current = payload.get(name)
        row = dict(current) if isinstance(current, dict) else {}
        runs = row.get("runs", 0)
        row["runs"] = (runs if isinstance(runs, int) and not isinstance(runs, bool) else 0) + 1
        row["last_used"] = datetime.now(UTC).isoformat()
        payload[name] = row
        atomic_write_json(path, payload)


def delete_role_usage(workspace: Path, role: str) -> None:
    name = normalize_role_name(role)
    path = usage_state_path(workspace)
    with _USAGE_LOCK:
        payload = read_json_object(path)
        if name not in payload:
            return
        payload.pop(name, None)
        atomic_write_json(path, payload)
