"""Canonical model-domain types and lookup helpers.

A model registry is a mapping from stable ``model_id`` keys to ``ModelConfig``
values. The key is machine identity, ``display_name`` is mutable human-facing
text, and ``model`` is the upstream model string sent to the provider API.
Consumer domains own only references to model IDs and their own usage policy.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from nanobot.config_base import Base

ModelCapability = Literal["text", "vision", "image_generation", "transcription"]
MODEL_CAPABILITIES: tuple[ModelCapability, ...] = (
    "text",
    "vision",
    "image_generation",
    "transcription",
)
MODEL_ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}")


class _StrictModelDomainBase(Base):
    """Model-domain DTO base that rejects unknown fields."""

    model_config = ConfigDict(**Base.model_config, extra="forbid")


def validate_model_id(model_id: str) -> str:
    """Validate and return one canonical model registry ID.

    IDs are stable lowercase slugs. Display names and upstream provider model
    strings are intentionally not accepted as substitutes for this identity.
    """
    if not isinstance(model_id, str) or MODEL_ID_PATTERN.fullmatch(model_id) is None:
        raise ValueError("model_id must match [a-z][a-z0-9_-]{0,63}")
    return model_id


class ModelCapabilities(_StrictModelDomainBase):
    """Capabilities with real Nanobot consumers; all capabilities are opt-in."""

    text: bool = False
    vision: bool = False
    image_generation: bool = False
    transcription: bool = False


class ModelGenerationDefaults(_StrictModelDomainBase):
    """Default inference parameters for calls to this model, not capabilities."""

    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, ge=1)
    reasoning_effort: str | None = None


class ModelPricing(_StrictModelDomainBase):
    """Per-million-token price facts currently consumed by Model Fleet."""

    input: float | None = Field(default=None, ge=0.0)
    output: float | None = Field(default=None, ge=0.0)
    cache_read: float | None = Field(default=None, ge=0.0)


class ModelConfig(_StrictModelDomainBase):
    """Canonical definition of one configured provider/model route.

    ``provider`` is always a concrete configured Provider ID. Auto-detection may
    be used by a future creation-input parser, but ``"auto"`` is never valid
    canonical model state.
    """

    display_name: str
    provider: str
    model: str
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    context_window_tokens: int = Field(default=200_000, ge=1)
    pricing: ModelPricing = Field(default_factory=ModelPricing)
    generation_defaults: ModelGenerationDefaults = Field(
        default_factory=ModelGenerationDefaults,
    )
    offering_id: str | None = None
    pools: list[str] = Field(default_factory=list)
    max_concurrent_requests: int | None = Field(default=None, ge=1)

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("display_name must not be blank")
        return normalized

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider must not be blank")
        if normalized.casefold() == "auto":
            raise ValueError("canonical ModelConfig.provider must be a concrete provider ID")
        return normalized

    @field_validator("model")
    @classmethod
    def _validate_upstream_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model must not be blank")
        return normalized

    @field_validator("offering_id")
    @classmethod
    def _validate_offering_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", normalized):
            raise ValueError("offering_id contains unsupported characters")
        return normalized

    @field_validator("pools")
    @classmethod
    def _normalize_pools(cls, value: list[str]) -> list[str]:
        pools = [item.strip().lower() for item in value if item.strip()]
        return list(dict.fromkeys(pools))


def get_model(models: Mapping[str, ModelConfig], model_id: str) -> ModelConfig:
    """Resolve one canonical model ID from a registry or raise ``KeyError``."""
    validate_model_id(model_id)
    try:
        return models[model_id]
    except KeyError:
        raise KeyError(f"model_id {model_id!r} not found") from None


def require_model_capability(
    models: Mapping[str, ModelConfig],
    model_id: str,
    capability: ModelCapability,
) -> ModelConfig:
    """Resolve a model and require one declared capability."""
    if capability not in MODEL_CAPABILITIES:
        raise ValueError(f"unknown model capability {capability!r}")
    model = get_model(models, model_id)
    if not getattr(model.capabilities, capability):
        raise ValueError(f"model_id {model_id!r} does not support {capability}")
    return model
