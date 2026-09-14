"""Registry for speech-to-text provider adapters.

Provider-specific HTTP adapters live in ``nanobot.providers.transcription``.
Canonical provider and model identity come from ``Config.models``; this module
only maps concrete provider IDs to transcription adapter implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol


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
    ),
)

_BY_NAME = {spec.name: spec for spec in TRANSCRIPTION_PROVIDERS}


def transcription_provider_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in TRANSCRIPTION_PROVIDERS)


def get_transcription_provider(name: str) -> TranscriptionProviderSpec | None:
    return _BY_NAME.get(name)
