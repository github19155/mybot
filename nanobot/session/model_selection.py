"""Session-scoped canonical model selection metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

SESSION_MODEL_ID_METADATA_KEY = "_nanobot_model_id"


def model_id_from_metadata(metadata: object) -> str | None:
    """Read the canonical session model ID from persisted metadata."""
    if not isinstance(metadata, Mapping):
        return None
    typed_metadata = cast(Mapping[object, object], metadata)
    if SESSION_MODEL_ID_METADATA_KEY not in typed_metadata:
        return None
    value = typed_metadata[SESSION_MODEL_ID_METADATA_KEY]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("session model_id must be a non-empty string")
    return value.strip()
