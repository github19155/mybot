"""Default bootstrap model_name resolution (V2: agents.defaults.model_id + Config.models)."""

from __future__ import annotations

import json
from pathlib import Path

from nanobot.config.loader import load_config  # noqa: F401  (completes package import graph first)
from nanobot.webui.ws_http import (
    _default_model_name_from_config,
    _resolve_bootstrap_model_name,
)


def _write_v2_config(path: Path, *, upstream: str = "custom/smoke-model") -> Path:
    path.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model_id": "main", "workspace": "."}},
                "models": {
                    "main": {
                        "display_name": "Smoke Model",
                        "provider": "custom",
                        "model": upstream,
                        "capabilities": {"text": True},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_default_model_fallback_resolves_configured_model(tmp_path: Path) -> None:
    config_path = _write_v2_config(tmp_path / "config.json")
    assert _default_model_name_from_config(config_path) == "custom/smoke-model"


def test_bootstrap_model_runtime_wins_then_config_fallback(tmp_path: Path) -> None:
    config_path = _write_v2_config(tmp_path / "config.json")
    assert _resolve_bootstrap_model_name(lambda: "openai/gpt-4.1", config_path) == "openai/gpt-4.1"
    assert _resolve_bootstrap_model_name(lambda: "   ", config_path) == "custom/smoke-model"
    assert _resolve_bootstrap_model_name(None, config_path) == "custom/smoke-model"


def test_default_model_fallback_degrades_gracefully_without_resolve_preset(tmp_path: Path) -> None:
    legacy = {
        "agents": {
            "defaults": {"provider": "custom", "model": "custom/smoke-model", "workspace": "."}
        },
        "providers": {"custom": {"apiKey": "x"}},
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    assert _default_model_name_from_config(path) is None
