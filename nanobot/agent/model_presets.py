"""Helpers for runtime model preset selection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from nanobot.config.schema import Config, ModelPresetConfig

PresetCatalogLoader = Callable[[], Mapping[str, ModelPresetConfig]]


def default_selection_signature(
    signature: tuple[object, ...] | None,
    model_preset: str | None = None,
) -> tuple[object, ...] | None:
    return (model_preset, *signature[:2]) if signature else None


def configured_model_presets(config: Config) -> dict[str, ModelPresetConfig]:
    return {**config.model_presets, "default": config.resolve_default_preset()}


def load_model_preset_catalog(
    config_path: Path | None = None,
) -> dict[str, ModelPresetConfig]:
    """Load the current preset catalog from the configured file."""
    from nanobot.config.loader import load_config, resolve_config_env_vars

    return configured_model_presets(
        resolve_config_env_vars(
            load_config(config_path),
            config_path=config_path,
        ),
    )


def normalize_preset_name(name: str | None, presets: dict[str, ModelPresetConfig]) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("model_preset must be a non-empty string")
    name = name.strip()
    if name in presets:
        return name
    matches = [candidate for candidate in presets if candidate.casefold() == name.casefold()]
    if len(matches) == 1:
        return matches[0]
    raise KeyError(f"model_preset {name!r} not found. Available: {', '.join(presets) or '(none)'}")
