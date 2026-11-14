from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from rex.assistant_errors import AudioDeviceError
from rex.voice_loop import VoiceLoop


class _OnceListener:
    async def listen(self, _source):
        yield b"wake"

    def mark_listening_started(self, *, reason: str = "test") -> None:
        return None

    def reset(self, *, reason: str = "test") -> None:
        return None


def _assistant(reply: str = "Hello.") -> MagicMock:
    assistant = MagicMock()
    assistant.generate_reply = AsyncMock(return_value=reply)
    del assistant.stream_reply
    return assistant


def _base_callbacks():
    async def detection_source():
        return b"wake"

    async def transcribe(_audio):
        return "hello rex"

    return detection_source, transcribe


def test_microphone_failure_emits_structured_actionable_diagnostic(caplog) -> None:
    detection_source, transcribe = _base_callbacks()
    diagnostics: list[dict[str, object]] = []

    async def record_phrase():
        raise AudioDeviceError("device busy")

    async def speak(_text: str) -> None:
        return None

    loop = VoiceLoop(
        _assistant(),
        wake_listener=_OnceListener(),
        detection_source=detection_source,
        record_phrase=record_phrase,
        transcribe=transcribe,
        speak=speak,
        diagnostic_callback=diagnostics.append,
        post_interaction_cooldown=0,
    )

    with caplog.at_level(logging.ERROR, logger="rex.voice_loop"):
        asyncio.run(loop.run(max_interactions=1))

    assert diagnostics
    diagnostic = diagnostics[-1]
    assert diagnostic["event"] == "audio_device_error"
    assert diagnostic["code"] == "microphone_unavailable"
    assert diagnostic["device_kind"] == "microphone"
    assert "Voice settings" in str(diagnostic["user_message"])
    assert "permission" in str(diagnostic["user_message"]).lower()
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "audio_device_error"
    )
    assert record.device_kind == "microphone"
    assert record.user_message == diagnostic["user_message"]


def test_speaker_failure_emits_structured_actionable_diagnostic(caplog) -> None:
    detection_source, transcribe = _base_callbacks()
    diagnostics: list[dict[str, object]] = []

    async def record_phrase():
        return b"audio"

    async def speak(_text: str) -> None:
        raise AudioDeviceError("output device busy")

    loop = VoiceLoop(
        _assistant(),
        wake_listener=_OnceListener(),
        detection_source=detection_source,
        record_phrase=record_phrase,
        transcribe=transcribe,
        speak=speak,
        diagnostic_callback=diagnostics.append,
        post_interaction_cooldown=0,
    )

    with caplog.at_level(logging.ERROR, logger="rex.voice_loop"):
        asyncio.run(loop.run(max_interactions=1))

    assert diagnostics
    diagnostic = diagnostics[-1]
    assert diagnostic["event"] == "audio_device_error"
    assert diagnostic["code"] == "speaker_unavailable"
    assert diagnostic["device_kind"] == "speaker"
    assert "Voice settings" in str(diagnostic["user_message"])
    assert "output" in str(diagnostic["user_message"]).lower()
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "audio_device_error"
    )
    assert record.device_kind == "speaker"
    assert record.user_message == diagnostic["user_message"]


def test_voice_bridge_uses_canonical_nested_microphone_selection() -> None:
    from types import SimpleNamespace

    import rex_voice_bridge

    settings = SimpleNamespace(
        audio=SimpleNamespace(input_device=14),
        input_device_index=99,
    )

    assert rex_voice_bridge._configured_input_device_index(settings) == 14


def test_voice_bridge_preserves_actionable_audio_diagnostic(monkeypatch) -> None:
    import rex_voice_bridge
    from rex.audio_config import build_audio_device_diagnostic

    events: list[dict[str, object]] = []
    monkeypatch.setattr(rex_voice_bridge, "emit", events.append)

    diagnostic = build_audio_device_diagnostic("microphone", "device busy")
    rex_voice_bridge.emit_audio_diagnostic(diagnostic)

    error_event = next(event for event in events if event.get("type") == "error")
    assert error_event["code"] == "microphone_unavailable"
    assert error_event["device_kind"] == "microphone"
    assert error_event["error"] == diagnostic["user_message"]
    assert "Voice settings" in str(error_event["error"])


def test_tts_does_not_hide_speaker_device_failure(monkeypatch) -> None:
    import pytest

    import rex.voice_loop as voice_loop_module
    from rex.voice.tts import TextToSpeech

    monkeypatch.setattr(voice_loop_module.settings, "tts_provider", "windows")
    tts = TextToSpeech(language="en")

    async def fail_output(_text: str, *, request_started_at: float):
        raise AudioDeviceError("speaker init failed")

    monkeypatch.setattr(tts, "_speak_windows", fail_output)

    with pytest.raises(AudioDeviceError, match="speaker init failed"):
        asyncio.run(tts.speak("Hello"))
