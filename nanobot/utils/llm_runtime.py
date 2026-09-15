"""Immutable execution settings for one LLM turn."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from nanobot.providers.base import GenerationSettings, LLMProvider

if TYPE_CHECKING:
    from nanobot.providers.factory import ProviderSnapshot


@dataclass(frozen=True, slots=True)
class LLMRuntime:
    """One captured canonical model/provider configuration for an execution."""

    provider: LLMProvider
    model_id: str | None
    model: str
    generation: GenerationSettings
    context_window_tokens: int
    system_prompt_prefix: str | None = None
    snapshot_signature: tuple[object, ...] | None = None
    supports_vision: bool = False

    @classmethod
    def capture(
        cls,
        provider: LLMProvider,
        model: str,
        *,
        context_window_tokens: int,
        model_id: str | None = None,
        supports_vision: bool = False,
        system_prompt_prefix: str | None = None,
        snapshot_signature: tuple[object, ...] | None = None,
    ) -> LLMRuntime:
        """Capture provider defaults without retaining mutable generation state."""
        defaults = GenerationSettings()
        generation = getattr(provider, "generation", defaults)
        return cls(
            provider=provider,
            model_id=model_id,
            model=model,
            generation=GenerationSettings(
                temperature=getattr(generation, "temperature", defaults.temperature),
                max_tokens=getattr(generation, "max_tokens", defaults.max_tokens),
                reasoning_effort=getattr(
                    generation,
                    "reasoning_effort",
                    defaults.reasoning_effort,
                ),
            ),
            context_window_tokens=context_window_tokens,
            supports_vision=supports_vision,
            system_prompt_prefix=system_prompt_prefix,
            snapshot_signature=snapshot_signature,
        )

    def with_generation_overrides(
        self,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMRuntime:
        generation = self.generation
        return replace(
            self,
            generation=GenerationSettings(
                temperature=generation.temperature if temperature is None else temperature,
                max_tokens=generation.max_tokens if max_tokens is None else max_tokens,
                reasoning_effort=(
                    generation.reasoning_effort
                    if reasoning_effort is None
                    else reasoning_effort
                ),
            ),
        )


def runtime_from_provider_snapshot(snapshot: ProviderSnapshot) -> LLMRuntime:
    """Convert a provider factory snapshot into the canonical runtime value."""
    if snapshot.generation is not None:
        return LLMRuntime(
            provider=snapshot.provider,
            model_id=snapshot.model_id,
            model=snapshot.model,
            generation=snapshot.generation,
            context_window_tokens=snapshot.context_window_tokens,
            supports_vision=snapshot.supports_vision,
            system_prompt_prefix=snapshot.system_prompt_prefix,
            snapshot_signature=snapshot.signature,
        )
    return LLMRuntime.capture(
        snapshot.provider,
        snapshot.model,
        model_id=snapshot.model_id,
        context_window_tokens=snapshot.context_window_tokens,
        supports_vision=snapshot.supports_vision,
        system_prompt_prefix=snapshot.system_prompt_prefix,
        snapshot_signature=snapshot.signature,
    )
