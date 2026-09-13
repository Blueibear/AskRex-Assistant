"""Tests for US-009: STT-to-LLM-to-TTS stage.

Acceptance criteria:
- STT result is passed to Assistant.generate_reply() (not raw LanguageModel.generate())
- LLM response is passed to the TTS engine and audio playback begins
- If TTS fails, bounded content-free diagnostics are logged and the pipeline resets (no hang)
- End-to-end test covers STT transcript -> LLM -> TTS with mocks
- Typecheck passes
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

np = pytest.importorskip("numpy")

from rex.assistant_errors import TextToSpeechError  # noqa: E402
from rex.voice_loop import VoiceLoop  # noqa: E402


def _make_voice_loop(
    transcribe_result: str = "what time is it",
    speak_raises: Exception | None = None,
    llm_response: str = "It is noon",
    sample_rate: int = 16000,
):
    """Build a minimal VoiceLoop with mocked callables for US-009 tests."""
    audio = np.ones(sample_rate, dtype=np.float32)

    async def _record_phrase():
        return audio

    async def _detection_source():
        return np.ones(4, dtype=np.float32)

    async def _transcribe(_audio):
        return transcribe_result

    spoken_texts: list[str | None] = []

    async def _speak(text):
        spoken_texts.append(text)
        if speak_raises is not None:
            raise speak_raises

    assistant = MagicMock()
    assistant.generate_reply = AsyncMock(return_value=llm_response)
    # No stream_reply so the non-streaming path is exercised
    del assistant.stream_reply

    class _OnceListener:
        def __init__(self):
            self._fired = False

        async def listen(self, source):
            if not self._fired:
                self._fired = True
                yield await source()

        def stop(self):
            pass

    loop = VoiceLoop(
        assistant,
        wake_listener=_OnceListener(),
        detection_source=_detection_source,
        record_phrase=_record_phrase,
        transcribe=_transcribe,
        speak=_speak,
        sample_rate=sample_rate,
    )
    return loop, assistant, spoken_texts


def test_stt_result_passed_to_generate_reply():
    """AC 1: STT transcript is passed to Assistant.generate_reply(), not raw LLM."""
    loop, assistant, _ = _make_voice_loop(transcribe_result="turn on the lights")

    asyncio.run(loop.run(max_interactions=1))

    assistant.generate_reply.assert_awaited_once()
    call_args = assistant.generate_reply.call_args
    assert (
        call_args[0][0] == "turn on the lights"
    ), "generate_reply must receive the STT transcript as its first argument"


def test_llm_response_passed_to_tts():
    """AC 2: LLM response is passed to the TTS speak callable."""
    loop, _assistant, spoken_texts = _make_voice_loop(llm_response="It is noon")

    asyncio.run(loop.run(max_interactions=1))

    assert spoken_texts, "speak() must be called with the LLM response"
    assert spoken_texts[0] is not None
    assert (
        "It is noon" in spoken_texts[0]
    ), f"Expected LLM response in spoken text, got: {spoken_texts[0]!r}"


def test_tts_failure_logs_bounded_diagnostics_and_resets(caplog):
    """AC 3: TTS failures log bounded diagnostics, never response/error content."""
    private_response = "PRIVATE LLM RESPONSE 7f2c"
    private_error = "PRIVATE TTS ERROR 9a31"
    loop, _assistant, _ = _make_voice_loop(
        llm_response=private_response,
        speak_raises=TextToSpeechError(private_error),
    )

    with caplog.at_level(logging.ERROR, logger="rex.voice_loop"):
        asyncio.run(loop.run(max_interactions=1))

    records = [r for r in caplog.records if getattr(r, "event", None) == "tts_error"]
    assert records, "Expected tts_error log event on TTS failure"
    record = records[0]
    assert getattr(record, "error_code", None) == "TextToSpeechError"
    response_chars = getattr(record, "response_chars", None)
    assert isinstance(response_chars, int) and 0 < response_chars <= 100_000
    assert getattr(record, "llm_response", None) is None
    assert record.exc_info is None
    assert private_response not in caplog.text
    assert private_error not in caplog.text


def test_pipeline_continues_after_tts_failure():
    """AC 3: Pipeline does not hang after TTS failure (loop terminates cleanly)."""
    loop, _assistant, _ = _make_voice_loop(
        speak_raises=TextToSpeechError("tts not available"),
    )

    # Should complete without hanging; asyncio.run would time out if it hung
    asyncio.run(loop.run(max_interactions=1))


def test_end_to_end_stt_llm_tts(caplog):
    """AC 4: Full happy-path: STT transcript -> generate_reply -> speak."""
    loop, assistant, spoken_texts = _make_voice_loop(
        transcribe_result="hello rex",
        llm_response="Hi there!",
    )

    with caplog.at_level(logging.DEBUG, logger="rex.voice_loop"):
        asyncio.run(loop.run(max_interactions=1))

    # STT stage ran
    events = [getattr(r, "event", None) for r in caplog.records]
    assert "stt_handoff" in events, "Expected stt_handoff log event"

    # LLM stage ran
    assistant.generate_reply.assert_awaited_once_with("hello rex", voice_mode=True)

    # TTS stage ran
    assert spoken_texts, "speak() must be called in happy path"
    assert "Hi there!" in spoken_texts[0]
