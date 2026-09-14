"""Registry for speech-to-text provider adapters.

Provider-specific HTTP adapters live in ``nanobot.providers.transcription``.
Canonical provider and model identity come from ``Config.models``; this module
only maps concrete provider IDs to transcription adapter implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol


class TranscriptionProviderAdapter(Protocol):
    """Runtime protocol implemented by provider-specific transcription adapters."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        language: str | None = None,
        model: str | None = None,
    ) -> None: ...

    async def transcribe(self, file_path: str | Path) -> str: ...


@dataclass(frozen=True)
class TranscriptionProviderSpec:
    name: str
    adapter: str
    aliases: tuple[str, ...] = ()

    def load_adapter(self) -> type[TranscriptionProviderAdapter]:
        module_name, _, class_name = self.adapter.partition(":")
        if not module_name or not class_name:
            raise RuntimeError(f"Invalid transcription adapter path: {self.adapter}")
        adapter = getattr(import_module(module_name), class_name)
        return adapter


TRANSCRIPTION_PROVIDERS: tuple[TranscriptionProviderSpec, ...] = (
    TranscriptionProviderSpec(
        name="groq",
        adapter="nanobot.providers.transcription:GroqTranscriptionProvider",
    ),
    TranscriptionProviderSpec(
        name="openai",
        adapter="nanobot.providers.transcription:OpenAITranscriptionProvider",
    ),
    TranscriptionProviderSpec(
        name="openrouter",
        adapter="nanobot.providers.transcription:OpenRouterTranscriptionProvider",
    ),
    TranscriptionProviderSpec(
        name="xiaomi_mimo",
        adapter="nanobot.providers.transcription:XiaomiMiMoTranscriptionProvider",
        aliases=("mimo", "xiaomi"),
    ),
    TranscriptionProviderSpec(
        name="stepfun",
        adapter="nanobot.providers.transcription:StepFunTranscriptionProvider",
    ),
    TranscriptionProviderSpec(
        name="assemblyai",
        adapter="nanobot.providers.transcription:AssemblyAITranscriptionProvider",
    ),
    TranscriptionProviderSpec(
        name="siliconflow",
        adapter="nanobot.providers.transcription:OpenAITranscriptionProvider",
        aliases=("silicon",),
    ),
)

_BY_NAME = {spec.name: spec for spec in TRANSCRIPTION_PROVIDERS}
_BY_ALIAS = {alias: spec for spec in TRANSCRIPTION_PROVIDERS for alias in spec.aliases}


def transcription_provider_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in TRANSCRIPTION_PROVIDERS)


def get_transcription_provider(name: str) -> TranscriptionProviderSpec | None:
    return _BY_NAME.get(name)


def resolve_transcription_provider(value: Any) -> TranscriptionProviderSpec | None:
    """Resolve registry aliases for adapter-management callers only.

    Canonical transcription routing intentionally does not use aliases: it
    looks up ``ModelConfig.provider`` with ``get_transcription_provider``.
    """
    if not isinstance(value, str):
        return None
    name = value.strip().lower()
    return _BY_NAME.get(name) or _BY_ALIAS.get(name)
