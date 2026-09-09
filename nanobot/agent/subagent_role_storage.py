"""Persistence helpers for Dream-managed specialist roles and telemetry."""

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


# Dream role state is separate from reusable skills. Generic Dream file tools
# cannot write these paths; the restricted dream_roles capability owns them.
DREAM_ROLE_STATE_FILE = Path("agents") / "roles.json"
DREAM_CANDIDATES_FILE = Path("agents") / "role_candidates.json"
DREAM_USAGE_FILE = Path("agents") / "role_usage.json"
ROLE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ROLE_LOCK = threading.Lock()
_USAGE_LOCK = threading.Lock()
_CANDIDATE_LOCK = threading.Lock()


def normalize_role_name(name: object) -> str:
    """Normalize and validate a role identifier."""
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


def role_state_path(workspace: Path) -> Path:
    return workspace.expanduser().resolve() / DREAM_ROLE_STATE_FILE


def candidate_state_path(workspace: Path) -> Path:
    return workspace.expanduser().resolve() / DREAM_CANDIDATES_FILE


def usage_state_path(workspace: Path) -> Path:
    return workspace.expanduser().resolve() / DREAM_USAGE_FILE


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


def dream_role_entries_for_workspace(workspace: Path) -> dict[str, dict[str, Any]]:
    raw = read_json_object(role_state_path(workspace))
    result: dict[str, dict[str, Any]] = {}
    for raw_name, value in raw.items():
        if not isinstance(raw_name, str) or not isinstance(value, dict):
            continue
        try:
            name = normalize_role_name(raw_name)
        except ValueError:
            continue
        payload = dict(value)
        declared_name = payload.get("name")
        if declared_name is not None:
            try:
                if normalize_role_name(declared_name) != name:
                    continue
            except ValueError:
                continue
        result[name] = payload
    return result


def dream_role_entries(config: "Config | None") -> dict[str, dict[str, Any]]:
    workspace = workspace_from_config(config)
    return dream_role_entries_for_workspace(workspace) if workspace is not None else {}


def write_dream_role(workspace: Path, name: str, payload: dict[str, Any]) -> None:
    normalized = normalize_role_name(name)
    path = role_state_path(workspace)
    with _ROLE_LOCK:
        roles = read_json_object(path)
        roles[normalized] = dict(payload)
        atomic_write_json(path, roles)


def delete_dream_role(workspace: Path, name: str) -> bool:
    normalized = normalize_role_name(name)
    path = role_state_path(workspace)
    with _ROLE_LOCK:
        roles = read_json_object(path)
        if normalized not in roles:
            return False
        roles.pop(normalized, None)
        atomic_write_json(path, roles)
        return True


def role_usage(workspace: Path, role: str) -> dict[str, Any]:
    """Return persisted lightweight usage metadata for one role."""
    name = normalize_role_name(role)
    payload = read_json_object(usage_state_path(workspace))
    value = payload.get(name)
    return dict(value) if isinstance(value, dict) else {}


def record_role_use(workspace: Path, role: str) -> None:
    """Record an accepted role launch for Dream's hot/cold reasoning."""
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


def candidate_entries(workspace: Path) -> dict[str, dict[str, Any]]:
    raw = read_json_object(candidate_state_path(workspace))
    return {
        name: dict(value)
        for name, value in raw.items()
        if isinstance(name, str) and isinstance(value, dict)
    }


def observe_candidate(
    workspace: Path,
    name: str,
    *,
    responsibility: str,
    evidence: str,
) -> dict[str, Any]:
    """Persist one distinct evidence summary for a possible specialist."""
    normalized = normalize_role_name(name)
    responsibility = responsibility.strip()
    evidence = evidence.strip()
    if not responsibility or not evidence:
        raise ValueError("candidate observation requires responsibility and evidence")
    path = candidate_state_path(workspace)
    now = datetime.now(UTC).isoformat()
    with _CANDIDATE_LOCK:
        payload = read_json_object(path)
        current = payload.get(normalized)
        row = dict(current) if isinstance(current, dict) else {}
        prior_evidence = [
            str(item).strip()
            for item in row.get("evidence", [])
            if isinstance(item, str) and item.strip()
        ]
        is_new = evidence.casefold() not in {item.casefold() for item in prior_evidence}
        if is_new:
            prior_evidence.append(evidence)
        prior_evidence = prior_evidence[-5:]
        count = row.get("evidence_count", 0)
        if not isinstance(count, int) or isinstance(count, bool):
            count = 0
        row.update({
            "name": normalized,
            "responsibility": responsibility,
            "evidence_count": count + (1 if is_new else 0),
            "evidence": prior_evidence,
            "first_seen": row.get("first_seen") or now,
            "last_seen": now,
        })
        payload[normalized] = row
        atomic_write_json(path, payload)
        return dict(row)


def remove_candidate(workspace: Path, name: str) -> None:
    normalized = normalize_role_name(name)
    path = candidate_state_path(workspace)
    with _CANDIDATE_LOCK:
        payload = read_json_object(path)
        if normalized not in payload:
            return
        payload.pop(normalized, None)
        atomic_write_json(path, payload)
