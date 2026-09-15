"""Canonical transcription routing plus adapter safety regressions."""
from __future__ import annotations

import base64
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from nanobot.audio.transcription import (
    TranscriptionConfigError,
    TranscriptionIngressError,
    resolve_transcription_config,
    transcribe_audio_file,
)
from nanobot.audio.transcription_registry import (
    get_transcription_provider,
    transcription_provider_names,
)
from nanobot.config.schema import Config, TranscriptionConfig
from nanobot.providers.transcription import (
    AssemblyAITranscriptionProvider,
    GroqTranscriptionProvider,
    OpenAITranscriptionProvider,
    OpenRouterTranscriptionProvider,
    XiaomiMiMoTranscriptionProvider,
    _audio_format,
    _resolve_chat_completions_url,
    _resolve_transcription_url,
)


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "voice.ogg"
    path.write_bytes(b"OggS\x00fake-audio-bytes")
    return path


def _response(status: int, payload: dict[str, object] | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://example.test/audio/transcriptions")
    return httpx.Response(status_code=status, json=payload or {}, request=request)


def _config(
    *,
    provider: str = "groq",
    model: str = "whisper-large-v3",
    api_key: str | None = "stt-key",
    api_base: str | None = None,
    language: str | None = None,
    enabled: bool = True,
    capability: bool = True,
) -> Config:
    provider_payload: dict[str, object] = {}
    if api_key is not None:
        provider_payload["apiKey"] = api_key
    if api_base is not None:
        provider_payload["apiBase"] = api_base
    return Config.model_validate({
        "models": {
            "main": {
                "displayName": "Main",
                "provider": "anthropic",
                "model": "claude-opus-4-5",
                "capabilities": {"text": True},
            },
            "speech": {
                "displayName": "Speech",
                "provider": provider,
                "model": model,
                "capabilities": {"transcription": capability},
            },
        },
        "agents": {"defaults": {"modelId": "main"}},
        "transcription": {"enabled": enabled, "modelId": "speech", "language": language},
        "providers": {provider: provider_payload},
    })


def test_resolves_model_id_to_provider_and_upstream_model() -> None:
    resolved = resolve_transcription_config(_config(
        provider="openrouter",
        model="nvidia/parakeet-tdt-0.6b-v3",
        api_key="sk-or-test",
        api_base="https://openrouter.ai/api/v1",
        language="en",
    ))
    assert resolved.model_id == "speech"
    assert resolved.provider == "openrouter"
    assert resolved.model == "nvidia/parakeet-tdt-0.6b-v3"
    assert resolved.language == "en"
    assert resolved.api_key == "sk-or-test"
    assert resolved.api_base == "https://openrouter.ai/api/v1"
    assert resolved.configured is True
    assert resolved.provider_configured is True


def test_no_model_id_has_no_groq_or_registry_fallback() -> None:
    config = Config()
    config.providers.groq.api_key = "must-not-be-used"
    resolved = resolve_transcription_config(config)
    assert resolved.model_id is None
    assert resolved.provider is None
    assert resolved.model is None
    assert resolved.api_key == ""
    assert resolved.api_base == ""
    assert resolved.configured is False


def test_legacy_channel_fields_are_not_read() -> None:
    config = Config()
    config.channels.__dict__["transcription_provider"] = "openai"
    config.channels.__dict__["transcription_language"] = "en"
    config.providers.openai.api_key = "sk-test"
    resolved = resolve_transcription_config(config)
    assert resolved.provider is None
    assert resolved.language is None


def test_unknown_model_id_is_explicit() -> None:
    config = Config()
    config.transcription.model_id = "missing"
    with pytest.raises(TranscriptionConfigError) as exc_info:
        resolve_transcription_config(config)
    assert exc_info.value.detail == "unknown_model_id"
    assert exc_info.value.extra == {"model_id": "missing"}


def test_transcription_capability_is_required() -> None:
    with pytest.raises(TranscriptionConfigError) as exc_info:
        resolve_transcription_config(_config(capability=False))
    assert exc_info.value.detail == "model_lacks_transcription_capability"


def test_provider_identity_comes_from_model_not_model_prefix() -> None:
    resolved = resolve_transcription_config(_config(
        provider="cpa",
        model="openai/whisper-x",
        api_key="cpa-key",
        api_base="https://cpa.example/v1",
    ))
    assert resolved.provider == "cpa"
    assert resolved.model == "openai/whisper-x"


def test_provider_default_api_base_does_not_create_model_identity() -> None:
    resolved = resolve_transcription_config(_config(
        provider="siliconflow",
        model="vendor/custom-asr",
        api_key="sf-test",
    ))
    assert resolved.model == "vendor/custom-asr"
    assert resolved.api_base == "https://api.siliconflow.cn/v1"


def test_provider_env_key_is_reused() -> None:
    config = _config(provider="siliconflow", model="TeleAI/TeleSpeechASR", api_key=None)
    with patch.dict(os.environ, {"SILICONFLOW_API_KEY": "sf-env-key"}, clear=True):
        resolved = resolve_transcription_config(config)
    assert resolved.api_key == "sf-env-key"


def test_env_refs_are_resolved() -> None:
    config = _config(api_key="${MY_GROQ_KEY}", api_base="${MY_GROQ_BASE}")
    with patch.dict(os.environ, {
        "MY_GROQ_KEY": "gsk-real",
        "MY_GROQ_BASE": "https://groq.example/v1",
    }, clear=True):
        resolved = resolve_transcription_config(config)
    assert resolved.api_key == "gsk-real"
    assert resolved.api_base == "https://groq.example/v1"


def test_missing_credentials_do_not_change_configured_semantics() -> None:
    config = _config(api_key="${MISSING_KEY}")
    with patch.dict(os.environ, {}, clear=True):
        resolved = resolve_transcription_config(config)
    assert resolved.configured is True
    assert resolved.provider_configured is False


def test_registry_only_owns_adapter_mapping() -> None:
    assert "groq" in transcription_provider_names()
    spec = get_transcription_provider("siliconflow")
    assert spec is not None
    assert spec.adapter == "nanobot.providers.transcription:OpenAITranscriptionProvider"
    assert not hasattr(spec, "default_model")
    assert get_transcription_provider("silicon") is None
    assert get_transcription_provider("mimo") is None


def test_removed_provider_and_model_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TranscriptionConfig.model_validate({"provider": "groq"})
    with pytest.raises(ValidationError):
        TranscriptionConfig.model_validate({"model": "whisper-large-v3"})


@pytest.mark.asyncio
async def test_exact_upstream_model_is_passed_to_adapter(audio_file: Path) -> None:
    captured: dict[str, object] = {}

    class StubOpenRouter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def transcribe(self, file_path: str | Path) -> str:
            captured["file_path"] = Path(file_path)
            return "ok"

    resolved = resolve_transcription_config(_config(
        provider="openrouter",
        model="openai/whisper-x",
        api_key="sk-or-test",
        api_base="https://openrouter.ai/api/v1",
        language="en",
    ))
    with patch("nanobot.providers.transcription.OpenRouterTranscriptionProvider", StubOpenRouter):
        assert await transcribe_audio_file(audio_file, resolved) == "ok"
    assert captured["model"] == "openai/whisper-x"
    assert captured["api_key"] == "sk-or-test"
    assert captured["language"] == "en"


@pytest.mark.asyncio
async def test_provider_without_adapter_is_explicit(audio_file: Path) -> None:
    resolved = resolve_transcription_config(_config(provider="cpa", model="openai/whisper-x"))
    with pytest.raises(TranscriptionConfigError) as exc_info:
        await transcribe_audio_file(audio_file, resolved)
    assert exc_info.value.detail == "provider_no_transcription_adapter"
    assert exc_info.value.extra["provider"] == "cpa"


@pytest.mark.asyncio
async def test_provider_not_configured_is_explicit(audio_file: Path) -> None:
    with patch.dict(os.environ, {}, clear=True):
        resolved = resolve_transcription_config(_config(api_key=None))
        with pytest.raises(TranscriptionConfigError) as exc_info:
            await transcribe_audio_file(audio_file, resolved)
    assert exc_info.value.detail == "provider_not_configured"


@pytest.mark.asyncio
async def test_empty_adapter_result_is_transcription_failed(audio_file: Path) -> None:
    class EmptyGroq:
        def __init__(self, **kwargs):
            pass

        async def transcribe(self, file_path: str | Path) -> str:
            return ""

    resolved = resolve_transcription_config(_config())
    with patch("nanobot.providers.transcription.GroqTranscriptionProvider", EmptyGroq):
        with pytest.raises(TranscriptionIngressError) as exc_info:
            await transcribe_audio_file(audio_file, resolved)
    assert exc_info.value.detail == "transcription_failed"


def test_resolved_repr_hides_api_key() -> None:
    resolved = resolve_transcription_config(_config(api_key="secret"))
    assert "secret" not in repr(resolved)
    assert "api_key" not in repr(resolved)


def test_disabled_is_separate_from_configured() -> None:
    resolved = resolve_transcription_config(_config(enabled=False))
    assert resolved.enabled is False
    assert resolved.configured is True


@pytest.mark.asyncio
async def test_openai_retries_on_transient_http(audio_file: Path) -> None:
    provider = OpenAITranscriptionProvider(api_key="sk-test")
    post = AsyncMock(side_effect=[_response(503), _response(200, {"text": "hello"})])
    with patch("httpx.AsyncClient.post", post), patch("asyncio.sleep", AsyncMock()):
        assert await provider.transcribe(audio_file) == "hello"
    assert post.await_count == 2


@pytest.mark.asyncio
async def test_openai_retry_backoff_and_limit(audio_file: Path) -> None:
    provider = OpenAITranscriptionProvider(api_key="sk-test")
    post = AsyncMock(return_value=_response(503))
    sleep = AsyncMock()
    with patch("httpx.AsyncClient.post", post), patch("asyncio.sleep", sleep):
        assert await provider.transcribe(audio_file) == ""
    assert post.await_count == 4
    assert [call.args[0] for call in sleep.await_args_list] == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_openai_does_not_retry_auth_error(audio_file: Path) -> None:
    provider = OpenAITranscriptionProvider(api_key="sk-test")
    post = AsyncMock(return_value=_response(401))
    with patch("httpx.AsyncClient.post", post), patch("asyncio.sleep", AsyncMock()):
        assert await provider.transcribe(audio_file) == ""
    assert post.await_count == 1


@pytest.mark.asyncio
async def test_multipart_keeps_language_model_and_mime(audio_file: Path) -> None:
    provider = GroqTranscriptionProvider(api_key="k", model="my-stt", language="ko")
    post = AsyncMock(return_value=_response(200, {"text": "ok"}))
    with patch("httpx.AsyncClient.post", post):
        assert await provider.transcribe(audio_file) == "ok"
    files = post.await_args.kwargs["files"]
    assert files["model"] == (None, "my-stt")
    assert files["language"] == (None, "ko")
    assert files["file"][2] == "audio/ogg"


@pytest.mark.asyncio
async def test_openrouter_keeps_json_audio_behavior(audio_file: Path) -> None:
    provider = OpenRouterTranscriptionProvider(api_key="k", model="openai/whisper-x", language="en")
    post = AsyncMock(return_value=_response(200, {"text": "ok"}))
    with patch("httpx.AsyncClient.post", post):
        assert await provider.transcribe(audio_file) == "ok"
    body = post.await_args.kwargs["json"]
    assert body["model"] == "openai/whisper-x"
    assert body["language"] == "en"
    assert body["input_audio"]["format"] == "ogg"
    assert base64.b64decode(body["input_audio"]["data"]) == audio_file.read_bytes()


def test_audio_format_and_url_normalization_regressions() -> None:
    assert _audio_format(Path("v.opus")) == "ogg"
    assert _audio_format(Path("v.mp4")) == "m4a"
    default = "https://api.openai.com/v1/audio/transcriptions"
    assert _resolve_transcription_url(None, default) == default
    assert _resolve_transcription_url("https://api.groq.com/openai/v1", default).endswith(
        "/audio/transcriptions"
    )
    chat_default = "https://api.xiaomimimo.com/v1/chat/completions"
    assert _resolve_chat_completions_url("https://api.xiaomimimo.com/v1", chat_default) == chat_default


def test_adapter_model_overrides_remain_supported() -> None:
    assert OpenAITranscriptionProvider(api_key="k", model="custom").model == "custom"
    assert GroqTranscriptionProvider(api_key="k", model="custom").model == "custom"
    assert OpenRouterTranscriptionProvider(api_key="k", model="custom").model == "custom"
    assert XiaomiMiMoTranscriptionProvider(api_key="k", model="custom").model == "custom"
    assert AssemblyAITranscriptionProvider(api_key="k", model="custom").model == "custom"
