"""Canonical model-domain configuration types.

This module defines what a configured model *is*. Consumer assignments (Main,
Dream, Subagent, image tools, transcription) remain owned by their respective
configuration domains and reference model IDs rather than redefining model
facts.

The temporary flat compatibility surface on :class:`ModelConfig` exists only
so the staged model-domain refactor can land without forcing Runtime/Fleet,
Image, Transcription, and Subagent/Dream to move in one PR. Canonical persisted
model data is nested under capabilities, generation_defaults, and pricing.
"""

from __future__ import annotations

import re
from typing import Any, cast

from pydantic import Field, field_validator, model_validator

from nanobot.config_base import Base


class ModelCapabilities(Base):
    """Facts about what a concrete model can do."""

    text: bool = True
    vision: bool = False
    tools: bool = False
    reasoning: bool = False
    image_generation: bool = False
    audio: bool = False
    video: bool = False


class ModelGenerationDefaults(Base):
    """Default inference parameters for calls to a model.

    These are defaults, not capabilities and not consumer policy. Callers may
    override them for an individual runtime without changing model identity.
    """

    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, ge=1)
    reasoning_effort: str | None = None


class ModelPricing(Base):
    """Optional per-million-token price facts for a model offering."""

    input: float | None = Field(default=None, ge=0.0)
    output: float | None = Field(default=None, ge=0.0)
    cache_read: float | None = Field(default=None, ge=0.0)
    cache_write: float | None = Field(default=None, ge=0.0)


class ModelConfig(Base):
    """Canonical definition of one configured model route.

    ``pools`` remains a model-offering fact because Fleet selects offerings by
    pool. Fleet scoring, ranking, telemetry, and selection policy do not live in
    this object.
    """

    model: str
    provider: str = "auto"
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    context_window_tokens: int = Field(default=200_000, ge=1)
    pricing: ModelPricing = Field(default_factory=ModelPricing)
    generation_defaults: ModelGenerationDefaults = Field(
        default_factory=ModelGenerationDefaults,
    )
    offering_id: str | None = None
    pools: list[str] = Field(default_factory=list)
    max_concurrent_requests: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _accept_staged_flat_fields(cls, value: object) -> object:
        """Map the pre-refactor flat shape into the canonical nested shape.

        This is an internal staging bridge, not a second model representation:
        after validation there is only one set of fields on ``ModelConfig``.
        It can be removed after all consumer branches use the canonical API.
        """
        if not isinstance(value, dict):
            return value
        data = dict(cast(dict[str, Any], value))

        capabilities_raw = data.get("capabilities")
        capabilities = (
            dict(cast(dict[str, Any], capabilities_raw))
            if isinstance(capabilities_raw, dict)
            else {}
        )
        for old, new in (
            ("supports_vision", "vision"),
            ("supportsVision", "vision"),
            ("supports_image_generation", "image_generation"),
            ("supportsImageGeneration", "image_generation"),
            ("supports_audio", "audio"),
            ("supportsAudio", "audio"),
            ("supports_video", "video"),
            ("supportsVideo", "video"),
            ("supports_tools", "tools"),
            ("supportsTools", "tools"),
            ("supports_reasoning", "reasoning"),
            ("supportsReasoning", "reasoning"),
        ):
            if old in data and new not in capabilities:
                capabilities[new] = data[old]
        if capabilities:
            data["capabilities"] = capabilities

        generation_raw = data.get("generation_defaults", data.get("generationDefaults"))
        generation = (
            dict(cast(dict[str, Any], generation_raw))
            if isinstance(generation_raw, dict)
            else {}
        )
        for old, new in (
            ("temperature", "temperature"),
            ("max_tokens", "max_tokens"),
            ("maxTokens", "max_tokens"),
            ("reasoning_effort", "reasoning_effort"),
            ("reasoningEffort", "reasoning_effort"),
        ):
            if old in data and new not in generation:
                generation[new] = data[old]
        if generation:
            data["generation_defaults"] = generation

        pricing_raw = data.get("pricing")
        pricing = (
            dict(cast(dict[str, Any], pricing_raw))
            if isinstance(pricing_raw, dict)
            else {}
        )
        for old, new in (
            ("input_cost_per_million", "input"),
            ("inputCostPerMillion", "input"),
            ("output_cost_per_million", "output"),
            ("outputCostPerMillion", "output"),
            ("cached_input_cost_per_million", "cache_read"),
            ("cachedInputCostPerMillion", "cache_read"),
            ("cache_write_cost_per_million", "cache_write"),
            ("cacheWriteCostPerMillion", "cache_write"),
        ):
            if old in data and new not in pricing:
                pricing[new] = data[old]
        if pricing:
            data["pricing"] = pricing

        if "pools" not in data:
            if "fleet_pools" in data:
                data["pools"] = data["fleet_pools"]
            elif "fleetPools" in data:
                data["pools"] = data["fleetPools"]
        return data

    @field_validator("model", "provider")
    @classmethod
    def _validate_identity_part(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model identity fields must not be blank")
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

    def to_generation_settings(self) -> Any:
        from nanobot.providers.base import GenerationSettings

        return GenerationSettings(
            temperature=self.generation_defaults.temperature,
            max_tokens=self.generation_defaults.max_tokens,
            reasoning_effort=self.generation_defaults.reasoning_effort,
        )

    # Temporary staged compatibility properties. They intentionally proxy the
    # canonical nested objects rather than storing duplicate values.
    @property
    def max_tokens(self) -> int:
        return self.generation_defaults.max_tokens

    @max_tokens.setter
    def max_tokens(self, value: int) -> None:
        self.generation_defaults.max_tokens = value

    @property
    def temperature(self) -> float:
        return self.generation_defaults.temperature

    @temperature.setter
    def temperature(self, value: float) -> None:
        self.generation_defaults.temperature = value

    @property
    def reasoning_effort(self) -> str | None:
        return self.generation_defaults.reasoning_effort

    @reasoning_effort.setter
    def reasoning_effort(self, value: str | None) -> None:
        self.generation_defaults.reasoning_effort = value

    @property
    def supports_vision(self) -> bool:
        return self.capabilities.vision

    @supports_vision.setter
    def supports_vision(self, value: bool) -> None:
        self.capabilities.vision = value

    @property
    def supports_image_generation(self) -> bool:
        return self.capabilities.image_generation

    @supports_image_generation.setter
    def supports_image_generation(self, value: bool) -> None:
        self.capabilities.image_generation = value

    @property
    def supports_audio(self) -> bool:
        return self.capabilities.audio

    @supports_audio.setter
    def supports_audio(self, value: bool) -> None:
        self.capabilities.audio = value

    @property
    def supports_video(self) -> bool:
        return self.capabilities.video

    @supports_video.setter
    def supports_video(self, value: bool) -> None:
        self.capabilities.video = value

    @property
    def supports_tools(self) -> bool:
        return self.capabilities.tools

    @supports_tools.setter
    def supports_tools(self, value: bool) -> None:
        self.capabilities.tools = value

    @property
    def supports_reasoning(self) -> bool:
        return self.capabilities.reasoning

    @supports_reasoning.setter
    def supports_reasoning(self, value: bool) -> None:
        self.capabilities.reasoning = value

    @property
    def fleet_pools(self) -> list[str]:
        return self.pools

    @fleet_pools.setter
    def fleet_pools(self, value: list[str]) -> None:
        self.pools = value

    @property
    def input_cost_per_million(self) -> float | None:
        return self.pricing.input

    @input_cost_per_million.setter
    def input_cost_per_million(self, value: float | None) -> None:
        self.pricing.input = value

    @property
    def output_cost_per_million(self) -> float | None:
        return self.pricing.output

    @output_cost_per_million.setter
    def output_cost_per_million(self, value: float | None) -> None:
        self.pricing.output = value

    @property
    def cached_input_cost_per_million(self) -> float | None:
        return self.pricing.cache_read

    @cached_input_cost_per_million.setter
    def cached_input_cost_per_million(self, value: float | None) -> None:
        self.pricing.cache_read = value


# Temporary import compatibility for staged consumer branches. There is only
# one concrete model type; this alias must disappear with the final legacy cut.
ModelPresetConfig = ModelConfig
