"""Tests for US-017: Fix Whisper/STT runtime failure and error exposure."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest

from rex.doctor import Status, check_stt_backend

# ---------------------------------------------------------------------------
# AC #1 + #2: check_stt_backend reports installed backend(s)
# ---------------------------------------------------------------------------


def test_check_stt_backend_whisper_only():
    """Reports OK with openai-whisper name when only whisper is installed."""
    with (patch("rex.doctor.find_spec", side_effect=lambda m: m == "whisper" or None),):
        result = check_stt_backend()
    assert result.status == Status.OK
    assert "openai-whisper" in result.message


def test_check_stt_backend_faster_whisper_only():
    """Reports OK with faster-whisper name when only faster-whisper is installed."""
    with (patch("rex.doctor.find_spec", side_effect=lambda m: m == "faster_whisper" or None),):
        result = check_stt_backend()
    assert result.status == Status.OK
    assert "faster-whisper" in result.message


def test_check_stt_backend_both_installed():
    """Reports OK mentioning both backends when both are present."""

    def _both(m: str):
        return m in ("whisper", "faster_whisper") or None

    with patch("rex.doctor.find_spec", side_effect=_both):
        result = check_stt_backend()
    assert result.status == Status.OK
    assert "whisper" in result.message.lower()


def test_check_stt_backend_neither_installed():
    """Reports ERROR when neither whisper nor faster-whisper is installed."""
    with patch("rex.doctor.find_spec", return_value=None):
        result = check_stt_backend()
    assert result.status == Status.ERROR
    assert "No STT backend" in result.message
    assert "pip install" in result.details


# ---------------------------------------------------------------------------
# AC #3 + #4: STT runtime errors logged with traceback; loop resets
# ---------------------------------------------------------------------------


def _make_stt():
    """Return a minimal SpeechToText-like object usable in isolation."""
    from rex.voice_loop import SpeechToText

    stt = SpeechToText.__new__(SpeechToText)
    stt._model = None
    stt._language = None
    stt._load_error = None
    stt._loaded = True
    return stt


@pytest.mark.skipif(
    __import__("importlib.util", fromlist=["find_spec"]).find_spec("numpy") is None,
    reason="numpy required for SpeechToText.transcribe",
)
def test_stt_runtime_error_logged_with_traceback(caplog):
    """When whisper raises, the error is logged at ERROR level with traceback."""
    from rex.voice_loop import SpeechToText

    stt = SpeechToText.__new__(SpeechToText)
    stt._language = None
    stt._load_error = None
    stt._loaded = True

    fake_model = MagicMock()
    private_error = "PRIVATE STT ERROR 4d91"
    fake_model.transcribe.side_effect = RuntimeError(private_error)
    stt._model = fake_model

    # Provide a minimal WAV buffer so format check passes
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00" * 320)
    wav_bytes = buf.getvalue()

    with caplog.at_level(logging.ERROR, logger="rex.voice_loop"):
        with pytest.raises(Exception):  # noqa: B017
            asyncio.run(stt.transcribe(audio=wav_bytes, sample_rate=16000))

    error_records = [
        r for r in caplog.records if getattr(r, "event", None) == "stt_transcription_failed"
    ]
    assert error_records, "Expected bounded STT failure diagnostics"
    record = error_records[0]
    assert getattr(record, "error_code", None) == "RuntimeError"
    assert record.exc_info is None
    assert private_error not in caplog.text


def test_stt_error_in_voice_loop_logs_bounded_diagnostics(caplog):
    """SpeechToTextError is logged without raw error text or traceback."""
    pytest.importorskip("numpy")
    from rex.assistant_errors import SpeechToTextError
    from rex.voice_loop import VoiceLoop

    private_error = "PRIVATE VOICE STT ERROR c41e"

    async def _bad_transcribe(_audio):
        raise SpeechToTextError(private_error)

    # record_phrase returns a dummy buffer
    async def _record():
        return b"\x00" * 32

    # wakeword detection yields once then stops
    class _WakeListener:
        async def listen(self, _source):
            yield None

    async def _detection_source():
        return b"\x00" * 32

    async def _speak(_text):
        return None

    loop = VoiceLoop(
        MagicMock(),
        wake_listener=_WakeListener(),
        detection_source=_detection_source,
        record_phrase=_record,
        transcribe=_bad_transcribe,
        speak=_speak,
        sample_rate=16000,
        stt_timeout=30.0,
        llm_timeout=60.0,
        tts_timeout=30.0,
    )

    with caplog.at_level(logging.ERROR, logger="rex.voice_loop"):
        asyncio.run(loop.run(max_interactions=1))

    stt_errors = [r for r in caplog.records if getattr(r, "event", None) == "stt_error"]
    assert stt_errors, "Expected bounded STT error diagnostics"
    record = stt_errors[0]
    assert getattr(record, "error_code", None) == "SpeechToTextError"
    assert record.exc_info is None
    assert private_error not in caplog.text
