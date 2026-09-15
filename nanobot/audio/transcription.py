"""Application-level audio transcription service.

This module owns nanobot's transcription behavior: canonical model resolution,
upload validation, temporary-file handling, and dispatch to provider adapters.
Provider-specific HTTP details live in ``nanobot.providers.transcription``.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.audio.transcription_registry import (
    TranscriptionProviderSpec,
    get_transcription_provider,
)
from nanobot.config.loader import resolve_env_refs
from nanobot.config.paths import get_media_dir
from nanobot.config.schema import Config, ProviderConfig
from nanobot.model_domain import require_model_capability
from nanobot.providers.registry import find_by_name
from nanobot.utils.media_decode import FileSizeExceeded, save_base64_data_url

TranscriptionProviderName = str

_MAX_AUDIO_BYTES_FALLBACK = 25 * 1024 * 1024
_AUDIO_MIME_ALLOWED: frozenset[str] = frozenset({
    "audio/aac",
    "audio/flac",
    "audio/m4a",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-m4a",
    "audio/x-wav",
})


@dataclass(frozen=True)
class EffectiveTranscriptionConfig:
    enabled: bool
    model_id: str | None
    provider: TranscriptionProviderName | None
    model: str | None
    language: str | None
    api_key: str = field(repr=False)
    api_base: str
    max_duration_sec: int
    max_upload_mb: int

    @property
    def configured(self) -> bool:
        """Whether Transcription is bound to a concrete canonical model."""
        return bool(self.model_id and self.provider and self.model)

    @property
    def provider_configured(self) -> bool:
        """Whether credentials for the resolved provider are currently available."""
        return bool(self.api_key)


class TranscriptionError(Exception):
    """Stable transcription service error surfaced to callers such as WebUI."""

    def __init__(self, detail: str, **extra: Any):
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


class TranscriptionConfigError(TranscriptionError):
    """Canonical model/provider configuration cannot serve transcription."""


class TranscriptionIngressError(TranscriptionError):
    """Audio upload or transcription execution failed."""


def _provider_config(config: Config, provider: str) -> ProviderConfig | None:
    value = getattr(config.providers, provider, None)
    if isinstance(value, ProviderConfig):
        return value
    value = (config.providers.model_extra or {}).get(provider)
    return value if isinstance(value, ProviderConfig) else None


def _provider_default_api_base(provider: str) -> str | None:
    spec = find_by_name(provider)
    return spec.default_api_base if spec else None


def _resolve_transcription_api_key(
    provider: str,
    provider_cfg: ProviderConfig | None,
) -> str:
    api_key = resolve_env_refs(getattr(provider_cfg, "api_key", None) or "") if provider_cfg else ""
    if api_key:
        return api_key

    spec = find_by_name(provider)
    if provider == "siliconflow":
        env_key = os.environ.get("SILICONFLOW_API_KEY")
        if env_key:
            return env_key

    env_key = spec.env_key if spec else ""
    return os.environ.get(env_key, "") if env_key else ""


def _resolve_transcription_api_base(
    provider: str,
    provider_cfg: ProviderConfig | None,
) -> str:
    api_base = resolve_env_refs(getattr(provider_cfg, "api_base", None) or "") if provider_cfg else ""
    if api_base:
        return api_base
    return _provider_default_api_base(provider) or ""


def _extract_data_url_mime(url: str) -> str | None:
    header, _, _ = url.partition(",")
    if not header.startswith("data:") or ";base64" not in header:
        return None
    return header[5:].split(";", 1)[0].strip().lower() or None


def resolve_transcription_config(config: Config) -> EffectiveTranscriptionConfig:
    """Resolve Transcription exclusively through ``transcription.model_id``."""
    top = config.transcription
    model_id = top.model_id
    if model_id is None:
        return EffectiveTranscriptionConfig(
            enabled=top.enabled,
            model_id=None,
            provider=None,
            model=None,
            language=top.language,
            api_key="",
            api_base="",
            max_duration_sec=top.max_duration_sec,
            max_upload_mb=top.max_upload_mb,
        )

    try:
        model_cfg = require_model_capability(config.models, model_id, "transcription")
    except KeyError as exc:
        raise TranscriptionConfigError("unknown_model_id", model_id=model_id) from exc
    except ValueError as exc:
        if model_id in config.models:
            raise TranscriptionConfigError(
                "model_lacks_transcription_capability",
                model_id=model_id,
            ) from exc
        raise TranscriptionConfigError("unknown_model_id", model_id=model_id) from exc

    provider = model_cfg.provider
    provider_cfg = _provider_config(config, provider)
    return EffectiveTranscriptionConfig(
        enabled=top.enabled,
        model_id=model_id,
        provider=provider,
        model=model_cfg.model,
        language=top.language,
        api_key=_resolve_transcription_api_key(provider, provider_cfg),
        api_base=_resolve_transcription_api_base(provider, provider_cfg),
        max_duration_sec=top.max_duration_sec,
        max_upload_mb=top.max_upload_mb,
    )


def _require_runnable_transcription(
    config: EffectiveTranscriptionConfig,
) -> TranscriptionProviderSpec:
    if not config.enabled:
        raise TranscriptionConfigError("disabled")
    if not config.configured:
        raise TranscriptionConfigError("no_model_configured")

    provider = config.provider
    assert provider is not None
    spec = get_transcription_provider(provider)
    if spec is None:
        raise TranscriptionConfigError(
            "provider_no_transcription_adapter",
            provider=provider,
            model_id=config.model_id,
        )
    if not config.provider_configured:
        raise TranscriptionConfigError(
            "provider_not_configured",
            provider=provider,
            model_id=config.model_id,
        )
    return spec


async def transcribe_audio_data_url(
    data_url: Any,
    config: EffectiveTranscriptionConfig,
    *,
    duration_ms: Any = None,
) -> str:
    """Validate, persist, transcribe, and remove a WebUI audio data URL."""
    _require_runnable_transcription(config)
    if not isinstance(data_url, str) or not data_url:
        raise TranscriptionIngressError("missing_audio")
    if (
        isinstance(duration_ms, (int, float))
        and duration_ms > (config.max_duration_sec * 1000 + 1000)
    ):
        raise TranscriptionIngressError("duration")
    if _extract_data_url_mime(data_url) not in _AUDIO_MIME_ALLOWED:
        raise TranscriptionIngressError("mime")

    audio_path: str | None = None
    max_bytes = max(
        1,
        config.max_upload_mb * 1024 * 1024 if config.max_upload_mb else _MAX_AUDIO_BYTES_FALLBACK,
    )
    try:
        audio_path = save_base64_data_url(
            data_url,
            get_media_dir("webui-transcription"),
            max_bytes=max_bytes,
        )
    except FileSizeExceeded as exc:
        raise TranscriptionIngressError("size") from exc
    except Exception as exc:
        logger.warning("transcription audio decode failed: {}", exc)
    if not audio_path:
        raise TranscriptionIngressError("decode")

    try:
        text = await transcribe_audio_file(audio_path, config)
    finally:
        with suppress(OSError):
            Path(audio_path).unlink(missing_ok=True)
    if not text:
        raise TranscriptionIngressError("transcription_failed", provider=config.provider)
    return text


async def transcribe_audio_file(
    file_path: str | Path,
    config: EffectiveTranscriptionConfig,
) -> str:
    """Transcribe *file_path* using an already-resolved canonical model route."""
    spec = _require_runnable_transcription(config)
    provider_name = config.provider
    model = config.model
    assert provider_name is not None
    assert model is not None

    adapter = spec.load_adapter()(
        api_key=config.api_key,
        api_base=config.api_base or None,
        language=config.language,
        model=model,
    )
    try:
        text = await adapter.transcribe(file_path)
    except Exception as exc:
        logger.exception("{} transcription failed: {}", provider_name, exc)
        raise TranscriptionIngressError(
            "transcription_failed",
            provider=provider_name,
            model_id=config.model_id,
        ) from exc
    if not text:
        raise TranscriptionIngressError(
            "transcription_failed",
            provider=provider_name,
            model_id=config.model_id,
        )
    return text
