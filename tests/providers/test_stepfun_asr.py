"""StepFun ASR adapter and canonical transcription routing regressions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nanobot.audio.transcription import resolve_transcription_config
from nanobot.audio.transcription_registry import get_transcription_provider
from nanobot.config.schema import Config
from nanobot.providers.transcription import StepFunTranscriptionProvider


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "voice.ogg"
    path.write_bytes(b"OggS\x00fake-audio-bytes")
    return path


def _make_stream(status: int, lines: list[str]) -> MagicMock:
    class Response:
        status_code = status
        reason_phrase = "OK" if status == 200 else "Error"

        async def __aenter__(self) -> "Response":
            return self

        async def __aexit__(self, *exc: object) -> None:
            pass

        async def aiter_lines(self) -> Any:
            for line in lines:
                yield line

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                response = httpx.Response(
                    self.status_code,
                    request=httpx.Request("POST", "https://example.test"),
                )
                raise httpx.HTTPStatusError("bad status", request=response.request, response=response)

    mock = MagicMock()
    mock.return_value = Response()
    return mock


def _make_sequence(entries: list[int | list[str]]) -> MagicMock:
    calls = [0]

    class Response:
        def __init__(self, entry: int | list[str]) -> None:
            self.entry = entry
            self.status_code = entry if isinstance(entry, int) else 200
            self.reason_phrase = "Error" if isinstance(entry, int) else "OK"

        async def __aenter__(self) -> "Response":
            return self

        async def __aexit__(self, *exc: object) -> None:
            pass

        async def aiter_lines(self) -> Any:
            if isinstance(self.entry, list):
                for line in self.entry:
                    yield line

        def raise_for_status(self) -> None:
            if isinstance(self.entry, int):
                response = httpx.Response(
                    self.entry,
                    request=httpx.Request("POST", "https://example.test"),
                )
                raise httpx.HTTPStatusError("bad status", request=response.request, response=response)

    def next_response(*args: object, **kwargs: object) -> Response:
        index = min(calls[0], len(entries) - 1)
        calls[0] += 1
        return Response(entries[index])

    return MagicMock(side_effect=next_response)


def test_stepfun_url_and_model_configuration() -> None:
    default = StepFunTranscriptionProvider(api_key="sk-test")
    assert default.api_url == "https://api.stepfun.com/v1/audio/asr/sse"
    assert default.model == "stepaudio-2.5-asr"
    custom = StepFunTranscriptionProvider(
        api_key="sk-test",
        api_base="https://api.stepfun.com/step_plan/v1",
        model="stepaudio-2-asr-pro",
    )
    assert custom.api_url == "https://api.stepfun.com/step_plan/v1/audio/asr/sse"
    assert custom.model == "stepaudio-2-asr-pro"


@pytest.mark.asyncio
async def test_missing_key_and_file_short_circuit(audio_file: Path) -> None:
    stream = MagicMock()
    with patch.dict("os.environ", {}, clear=True), patch("httpx.AsyncClient.stream", stream):
        assert await StepFunTranscriptionProvider(api_key=None).transcribe(audio_file) == ""
    stream.assert_not_called()

    stream.reset_mock()
    with patch("httpx.AsyncClient.stream", stream):
        assert await StepFunTranscriptionProvider(api_key="k").transcribe("/missing.wav") == ""
    stream.assert_not_called()


@pytest.mark.asyncio
async def test_sse_done_event_returns_text(audio_file: Path) -> None:
    lines = [
        f"data: {json.dumps({'type': 'transcript.text.delta', 'text': '你'})}",
        f"data: {json.dumps({'type': 'transcript.text.done', 'text': '你好世界'})}",
    ]
    with patch("httpx.AsyncClient.stream", _make_stream(200, lines)):
        result = await StepFunTranscriptionProvider(api_key="k").transcribe(audio_file)
    assert result == "你好世界"


@pytest.mark.asyncio
async def test_sse_error_event_returns_empty(audio_file: Path) -> None:
    lines = [f"data: {json.dumps({'type': 'error', 'message': 'bad audio'})}"]
    with patch("httpx.AsyncClient.stream", _make_stream(200, lines)):
        assert await StepFunTranscriptionProvider(api_key="k").transcribe(audio_file) == ""


@pytest.mark.asyncio
async def test_stepfun_retries_transient_status_then_succeeds(audio_file: Path) -> None:
    success = [f"data: {json.dumps({'type': 'transcript.text.done', 'text': 'ok'})}"]
    stream = _make_sequence([503, success])
    with patch("httpx.AsyncClient.stream", stream), patch("asyncio.sleep", AsyncMock()):
        assert await StepFunTranscriptionProvider(api_key="k").transcribe(audio_file) == "ok"
    assert stream.call_count == 2


@pytest.mark.asyncio
async def test_stepfun_gives_up_after_retry_budget(audio_file: Path) -> None:
    stream = _make_sequence([503, 503, 503, 503])
    sleep = AsyncMock()
    with patch("httpx.AsyncClient.stream", stream), patch("asyncio.sleep", sleep):
        assert await StepFunTranscriptionProvider(api_key="k").transcribe(audio_file) == ""
    assert stream.call_count == 4
    assert sleep.await_count == 3


@pytest.mark.asyncio
async def test_stepfun_does_not_retry_401(audio_file: Path) -> None:
    stream = _make_stream(401, [])
    sleep = AsyncMock()
    with patch("httpx.AsyncClient.stream", stream), patch("asyncio.sleep", sleep):
        assert await StepFunTranscriptionProvider(api_key="k").transcribe(audio_file) == ""
    assert stream.call_count == 1
    sleep.assert_not_awaited()


def test_stepfun_registry_has_adapter_only() -> None:
    spec = get_transcription_provider("stepfun")
    assert spec is not None
    assert spec.adapter == "nanobot.providers.transcription:StepFunTranscriptionProvider"
    assert not hasattr(spec, "default_model")


def test_stepfun_resolves_through_canonical_model_id() -> None:
    config = Config.model_validate({
        "models": {
            "main": {
                "displayName": "Main",
                "provider": "anthropic",
                "model": "claude-opus-4-5",
                "capabilities": {"text": True},
            },
            "speech": {
                "displayName": "StepFun ASR",
                "provider": "stepfun",
                "model": "stepaudio-2.5-asr",
                "capabilities": {"transcription": True},
            },
        },
        "agents": {"defaults": {"modelId": "main"}},
        "transcription": {"modelId": "speech", "language": "zh"},
        "providers": {
            "stepfun": {
                "apiKey": "step-test",
                "apiBase": "https://api.stepfun.com/step_plan/v1",
            }
        },
    })
    resolved = resolve_transcription_config(config)
    assert resolved.model_id == "speech"
    assert resolved.provider == "stepfun"
    assert resolved.model == "stepaudio-2.5-asr"
    assert resolved.language == "zh"
    assert resolved.api_key == "step-test"
    assert resolved.api_base == "https://api.stepfun.com/step_plan/v1"
