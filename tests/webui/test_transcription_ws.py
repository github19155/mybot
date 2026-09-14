"""Tests for WebUI transcription envelopes carried over the gateway socket."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from nanobot.config.loader import save_config
from nanobot.config.schema import Config
from nanobot.webui.transcription_ws import webui_transcription_event


def _audio_data_url(payload: bytes = b"voice", mime: str = "audio/webm") -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def _config(
    *,
    provider: str = "groq",
    model: str = "whisper-large-v3",
    api_key: str | None = "gsk-test",
    capability: bool = True,
    enabled: bool = True,
    model_id: str | None = "speech",
) -> Config:
    providers: dict[str, object] = {}
    models: dict[str, object] = {
        "main": {
            "displayName": "Main",
            "provider": "anthropic",
            "model": "claude-opus-4-5",
            "capabilities": {"text": True},
        },
    }
    transcription: dict[str, object] = {"enabled": enabled}
    if model_id is not None:
        provider_config: dict[str, object] = {}
        if api_key is not None:
            provider_config["apiKey"] = api_key
        providers[provider] = provider_config
        models[model_id] = {
            "displayName": "Speech",
            "provider": provider,
            "model": model,
            "capabilities": {"transcription": capability},
        }
        transcription["modelId"] = model_id
    return Config.model_validate({
        "models": models,
        "agents": {"defaults": {"modelId": "main"}},
        "transcription": transcription,
        "providers": providers,
    })


async def _event(
    config: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    config_path = tmp_path / "config.json"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    return await webui_transcription_event(
        payload or {"request_id": "voice-1", "data_url": _audio_data_url()}
    )


@pytest.mark.asyncio
async def test_webui_rejects_no_model_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event, payload = await _event(_config(model_id=None), tmp_path, monkeypatch)
    assert event == "transcription_error"
    assert payload == {"request_id": "voice-1", "detail": "no_model_configured"}


@pytest.mark.asyncio
async def test_webui_rejects_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event, payload = await _event(_config(enabled=False), tmp_path, monkeypatch)
    assert event == "transcription_error"
    assert payload == {"request_id": "voice-1", "detail": "disabled"}


@pytest.mark.asyncio
async def test_webui_rejects_unconfigured_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    event, payload = await _event(_config(api_key=None), tmp_path, monkeypatch)
    assert event == "transcription_error"
    assert payload == {
        "request_id": "voice-1",
        "detail": "provider_not_configured",
        "provider": "groq",
        "model_id": "speech",
    }


@pytest.mark.asyncio
async def test_webui_rejects_provider_without_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event, payload = await _event(
        _config(provider="cpa", model="openai/whisper-x", api_key="cpa-key"),
        tmp_path,
        monkeypatch,
    )
    assert event == "transcription_error"
    assert payload == {
        "request_id": "voice-1",
        "detail": "provider_no_transcription_adapter",
        "provider": "cpa",
        "model_id": "speech",
    }


@pytest.mark.asyncio
async def test_webui_uses_explicit_gateway_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_path = tmp_path / "default.json"
    gateway_path = tmp_path / "gateway.json"
    save_config(_config(api_key="gsk-global"), default_path)
    save_config(_config(api_key=None), gateway_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", default_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    event, payload = await webui_transcription_event(
        {"request_id": "voice-explicit", "data_url": _audio_data_url()},
        config_path=gateway_path,
    )
    assert event == "transcription_error"
    assert payload["detail"] == "provider_not_configured"
    assert payload["provider"] == "groq"


@pytest.mark.asyncio
async def test_webui_rejects_lacking_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event, payload = await _event(_config(capability=False), tmp_path, monkeypatch)
    assert event == "transcription_error"
    assert payload == {
        "request_id": "voice-1",
        "detail": "model_lacks_transcription_capability",
        "model_id": "speech",
    }


@pytest.mark.asyncio
async def test_webui_rejects_unsupported_mime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event, payload = await _event(
        _config(),
        tmp_path,
        monkeypatch,
        payload={"request_id": "voice-1", "data_url": _audio_data_url(mime="text/plain")},
    )
    assert event == "transcription_error"
    assert payload["detail"] == "mime"


@pytest.mark.asyncio
async def test_webui_rejects_oversized_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    config.transcription.max_upload_mb = 1
    monkeypatch.setattr("nanobot.audio.transcription.get_media_dir", lambda _channel=None: tmp_path)
    event, payload = await _event(
        config,
        tmp_path,
        monkeypatch,
        payload={
            "request_id": "voice-1",
            "data_url": _audio_data_url(payload=b"x" * (1024 * 1024 + 1)),
        },
    )
    assert event == "transcription_error"
    assert payload["detail"] == "size"


@pytest.mark.asyncio
async def test_webui_rejects_excessive_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    config.transcription.max_duration_sec = 10
    event, payload = await _event(
        config,
        tmp_path,
        monkeypatch,
        payload={
            "request_id": "voice-1",
            "data_url": _audio_data_url(),
            "duration_ms": 11_001,
        },
    )
    assert event == "transcription_error"
    assert payload["detail"] == "duration"


@pytest.mark.asyncio
async def test_webui_returns_text_and_removes_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    monkeypatch.setattr("nanobot.audio.transcription.get_media_dir", lambda _channel=None: media_dir)
    captured_paths: list[Path] = []

    async def fake_transcribe_audio_file(path: str | Path, _resolved: Any) -> str:
        file_path = Path(path)
        assert file_path.exists()
        captured_paths.append(file_path)
        return "hello voice"

    monkeypatch.setattr(
        "nanobot.audio.transcription.transcribe_audio_file",
        fake_transcribe_audio_file,
    )
    event, payload = await _event(
        _config(),
        tmp_path,
        monkeypatch,
        payload={
            "request_id": "voice-1",
            "data_url": _audio_data_url(payload=b"webm voice", mime="audio/webm;codecs=opus"),
            "duration_ms": 1200,
        },
    )
    assert event == "transcription_result"
    assert payload == {"request_id": "voice-1", "text": "hello voice"}
    assert captured_paths
    assert not captured_paths[0].exists()


@pytest.mark.asyncio
async def test_webui_failure_still_removes_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    monkeypatch.setattr("nanobot.audio.transcription.get_media_dir", lambda _channel=None: media_dir)
    captured_paths: list[Path] = []

    async def fake_transcribe_audio_file(path: str | Path, _resolved: Any) -> str:
        captured_paths.append(Path(path))
        return ""

    monkeypatch.setattr(
        "nanobot.audio.transcription.transcribe_audio_file",
        fake_transcribe_audio_file,
    )
    event, payload = await _event(_config(), tmp_path, monkeypatch)
    assert event == "transcription_error"
    assert payload["detail"] == "transcription_failed"
    assert captured_paths
    assert not captured_paths[0].exists()
