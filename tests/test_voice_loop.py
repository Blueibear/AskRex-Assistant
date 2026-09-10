import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import rex.voice_loop as _rvl
from rex.assistant_errors import (
    AudioDeviceError,
    AudioFormatError,
    SpeechToTextError,
    TextToSpeechError,
)
from rex.voice_loop import AsyncMicrophone, TextToSpeech, VoiceLoop

np = pytest.importorskip("numpy")


class DummyListener:
    def __init__(self):
        self._triggered = False

    async def listen(self, source):
        if self._triggered:
            return
        self._triggered = True
        yield await source()

    def stop(self):
        pass


class DummyAssistant:
    def __init__(self):
        self.calls = []

    async def generate_reply(self, transcript, *, voice_mode: bool = False):
        self.calls.append(transcript)
        return "ok"


class StreamingAssistant(DummyAssistant):
    def __init__(self, tokens):
        super().__init__()
        self.tokens = list(tokens)
        self.stream_calls = []

    async def stream_reply(self, transcript, *, voice_mode: bool = False):
        self.stream_calls.append(transcript)
        for token in self.tokens:
            yield token


async def _constant_frame():
    return np.ones(4, dtype=np.float32)


async def _record_phrase():
    return np.ones(4, dtype=np.float32)


async def _transcribe(_: np.ndarray) -> str:
    return "hello world"


async def _transcribe_time(_: np.ndarray) -> str:
    return "what time is it"


async def _transcribe_filler(_: np.ndarray) -> str:
    return (
        "Okay. Nope music. Good annably. OK. Next screen. Okay. Good. Okay. "
        "Nowey. Thank you. Yes. Thank you. Thank you very much."
    )


async def _transcribe_weak_fragment(_: np.ndarray) -> str:
    return "What?"


async def _speak(_: str) -> None:
    pass


async def _ack():
    pass


@pytest.mark.unit
def test_prepare_audio_for_stt_boosts_quiet_audio():
    audio = np.ones(16000, dtype=np.float32) * 0.02

    prepared = _rvl._prepare_audio_for_stt(audio)

    assert not isinstance(prepared, bytes)
    assert float(np.max(np.abs(prepared))) > 0.1
    assert float(np.max(np.abs(prepared))) <= 1.0


@pytest.mark.unit
def test_async_microphone_uses_overlapping_detection_frames():
    chunks = [
        np.array([1.0, 2.0], dtype=np.float32),
        np.array([3.0, 4.0], dtype=np.float32),
        np.array([5.0, 6.0], dtype=np.float32),
    ]

    def recorder(_: float):
        return chunks.pop(0)

    mic = AsyncMicrophone(
        sample_rate=4,
        detection_seconds=1.0,
        detection_hop_seconds=0.5,
        capture_seconds=1.0,
        recorder=recorder,
    )

    first = asyncio.run(mic.detection_frame())
    second = asyncio.run(mic.detection_frame())
    mic.reset_detection_buffer(reason="test")
    third = asyncio.run(mic.detection_frame())

    assert first.tolist() == [0.0, 0.0, 1.0, 2.0]
    assert second.tolist() == [1.0, 2.0, 3.0, 4.0]
    assert third.tolist() == [0.0, 0.0, 5.0, 6.0]


@pytest.mark.unit
def test_async_microphone_primes_overlapping_detection_buffer():
    chunks = [
        np.array([1.0, 2.0], dtype=np.float32),
        np.array([3.0, 4.0], dtype=np.float32),
        np.array([5.0, 6.0], dtype=np.float32),
    ]
    requested_durations: list[float] = []

    def recorder(duration: float):
        requested_durations.append(duration)
        return chunks.pop(0)

    mic = AsyncMicrophone(
        sample_rate=4,
        detection_seconds=1.0,
        detection_hop_seconds=0.5,
        capture_seconds=1.0,
        recorder=recorder,
    )

    asyncio.run(mic.prime_detection_buffer(reason="test"))
    frame = asyncio.run(mic.detection_frame())

    assert requested_durations == [0.5, 0.5, 0.5]
    assert frame.tolist() == [3.0, 4.0, 5.0, 6.0]


@pytest.mark.unit
def test_async_microphone_adaptive_phrase_capture_extends_until_silence(monkeypatch):
    monkeypatch.setattr(_rvl.settings, "command_min_capture_seconds", 0.5, raising=False)
    monkeypatch.setattr(_rvl.settings, "command_max_capture_seconds", 2.0, raising=False)
    monkeypatch.setattr(_rvl.settings, "command_end_silence_seconds", 0.5, raising=False)
    monkeypatch.setattr(_rvl.settings, "command_vad_rms_threshold", 0.006, raising=False)

    chunks = [
        np.array([0.02], dtype=np.float32),
        np.array([0.02], dtype=np.float32),
        np.array([0.0], dtype=np.float32),
        np.array([0.0], dtype=np.float32),
    ]
    requested_durations: list[float] = []

    def recorder(duration: float):
        requested_durations.append(duration)
        return chunks.pop(0)

    mic = AsyncMicrophone(
        sample_rate=4,
        detection_seconds=1.0,
        capture_seconds=0.5,
        recorder=recorder,
    )

    audio = asyncio.run(mic.record_phrase())

    assert audio.tolist() == pytest.approx([0.02, 0.02, 0.0, 0.0])
    assert len(requested_durations) == 4


@pytest.mark.unit
def test_voice_loop_processes_interaction():
    assistant = DummyAssistant()
    listener = DummyListener()
    spoken = []

    async def speak(text: str) -> None:
        spoken.append(text)

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=_transcribe,
        speak=speak,
        acknowledge=_ack,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert assistant.calls == ["hello world"]
    assert spoken == ["ok."]  # Voice loop adds period for TTS


@pytest.mark.unit
def test_voice_loop_diagnostics_do_not_log_transcript_or_response_content(caplog):
    transcript_marker = "private command marker zeta"
    response_marker = "private response marker omega"

    class PrivateAssistant(DummyAssistant):
        async def generate_reply(self, transcript, *, voice_mode: bool = False):
            self.calls.append(transcript)
            return response_marker

    assistant = PrivateAssistant()
    listener = DummyListener()

    async def transcribe_private(_: np.ndarray) -> str:
        return transcript_marker

    async def fail_speak(_: str) -> None:
        raise TextToSpeechError("bounded synthesis failure")

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=transcribe_private,
        speak=fail_speak,
        acknowledge=_ack,
    )

    with caplog.at_level("INFO"):
        asyncio.run(loop.run(max_interactions=1))

    assert assistant.calls == [transcript_marker]
    serialized_logs = caplog.text + repr([record.__dict__ for record in caplog.records])
    assert transcript_marker not in serialized_logs
    assert response_marker not in serialized_logs


@pytest.mark.unit
@pytest.mark.parametrize("failure_stage", ["stt", "tts"])
def test_voice_loop_error_diagnostics_do_not_log_exception_content(caplog, failure_stage):
    private_marker = "private exception marker sigma"
    assistant = DummyAssistant()
    listener = DummyListener()

    async def transcribe(_: np.ndarray) -> str:
        if failure_stage == "stt":
            raise SpeechToTextError(private_marker)
        return "hello rex"

    async def speak(_: str) -> None:
        if failure_stage == "tts":
            raise TextToSpeechError(private_marker)

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=transcribe,
        speak=speak,
        acknowledge=_ack,
    )

    with caplog.at_level("INFO"):
        asyncio.run(loop.run(max_interactions=1))

    serialized_logs = caplog.text + repr([record.__dict__ for record in caplog.records])
    assert private_marker not in serialized_logs


@pytest.mark.unit
def test_wake_priming_diagnostic_does_not_log_exception_content(caplog):
    private_marker = "private priming path marker tau"

    class DetectionSource:
        async def prime_detection_buffer(self, *, reason: str) -> None:
            raise RuntimeError(f"{private_marker}: {reason}")

        async def read(self) -> np.ndarray:
            return await _constant_frame()

    source = DetectionSource()
    loop = VoiceLoop(
        DummyAssistant(),
        wake_listener=DummyListener(),
        detection_source=source.read,
        record_phrase=_record_phrase,
        transcribe=_transcribe,
        speak=_speak,
        acknowledge=_ack,
    )

    with caplog.at_level("INFO"):
        asyncio.run(loop._prime_wake_detection(reason="privacy-test"))

    serialized_logs = caplog.text + repr([record.__dict__ for record in caplog.records])
    assert private_marker not in serialized_logs


@pytest.mark.unit
def test_voice_loop_streams_tokens_into_sentence_buffer():
    assistant = StreamingAssistant(["Hello", " world. ", "How are", " you?"])
    listener = DummyListener()
    spoken = []

    async def speak_streaming(sentences):
        async for sentence in sentences:
            spoken.append(sentence)

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=_transcribe,
        speak=_speak,
        speak_streaming=speak_streaming,
        acknowledge=_ack,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert assistant.stream_calls == ["hello world"]
    assert assistant.calls == []
    assert spoken == ["Hello world.", "How are you?"]


@pytest.mark.unit
def test_voice_loop_resets_wake_listener_after_interaction():
    assistant = DummyAssistant()
    listener = DummyListener()
    reset_reasons: list[str] = []

    def reset_listener(*, reason="manual"):
        reset_reasons.append(reason)

    listener.reset = reset_listener  # type: ignore[attr-defined]

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=_transcribe,
        speak=_speak,
        acknowledge=None,
        post_interaction_cooldown=0,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert "post_interaction" in reset_reasons


@pytest.mark.unit
def test_voice_loop_emits_wake_listening_when_armed_and_rearmed():
    assistant = DummyAssistant()
    listener = DummyListener()
    states: list[str] = []

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=_transcribe,
        speak=_speak,
        acknowledge=None,
        post_interaction_cooldown=0,
        state_callback=states.append,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert states[0] == "wake_listening"
    assert "listening" in states
    assert states[-1] == "wake_listening"


@pytest.mark.unit
def test_voice_loop_primes_detection_before_reporting_wake_listening():
    assistant = DummyAssistant()
    listener = DummyListener()
    events: list[tuple[str, str]] = []

    class SourceOwner:
        async def prime_detection_buffer(self, *, reason: str = "manual") -> None:
            events.append(("prime", reason))

        async def frame(self):
            events.append(("source", "frame"))
            return np.ones(4, dtype=np.float32)

    source_owner = SourceOwner()

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=source_owner.frame,
        record_phrase=_record_phrase,
        transcribe=_transcribe,
        speak=_speak,
        acknowledge=None,
        post_interaction_cooldown=0,
        state_callback=lambda state: events.append(("state", state)),
    )

    asyncio.run(loop.run(max_interactions=1))

    assert events.index(("prime", "voice_loop_start")) < events.index(("state", "wake_listening"))
    assert events.index(("prime", "post_interaction_reset")) < len(events) - 1
    assert events[-1] == ("state", "wake_listening")


@pytest.mark.unit
def test_voice_loop_handles_repeated_interactions_in_one_session():
    assistant = DummyAssistant()
    spoken: list[str] = []
    reset_reasons: list[str] = []

    class TwoWakeListener:
        async def listen(self, source):
            yield await source()
            yield await source()

        def reset(self, *, reason="manual"):
            reset_reasons.append(reason)

    async def speak(text: str) -> None:
        spoken.append(text)

    loop = VoiceLoop(
        assistant,
        wake_listener=TwoWakeListener(),
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=_transcribe,
        speak=speak,
        acknowledge=None,
        post_interaction_cooldown=0,
    )

    asyncio.run(loop.run(max_interactions=2))

    assert assistant.calls == ["hello world", "hello world"]
    assert spoken == ["ok.", "ok."]
    assert reset_reasons == ["post_interaction", "post_interaction"]


@pytest.mark.unit
def test_voice_loop_handles_transcription_error():
    assistant = DummyAssistant()
    listener = DummyListener()
    spoken = []

    async def failing_transcribe(_: np.ndarray) -> str:
        raise SpeechToTextError("boom")

    async def speak(text: str) -> None:
        spoken.append(text)

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=failing_transcribe,
        speak=speak,
        acknowledge=None,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert assistant.calls == []
    assert spoken == []


@pytest.mark.unit
def test_voice_loop_ignores_likely_stt_hallucination():
    assistant = DummyAssistant()
    listener = DummyListener()
    spoken = []

    async def speak(text: str) -> None:
        spoken.append(text)

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=_transcribe_filler,
        speak=speak,
        acknowledge=None,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert assistant.calls == []
    assert spoken == []


@pytest.mark.unit
def test_voice_loop_allows_actionable_transcript():
    assistant = DummyAssistant()
    listener = DummyListener()
    spoken = []

    async def speak(text: str) -> None:
        spoken.append(text)

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=_transcribe_time,
        speak=speak,
        acknowledge=None,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert assistant.calls == ["what time is it"]
    assert spoken == ["ok."]


@pytest.mark.unit
def test_voice_loop_asks_retry_for_weak_transcript_fragment():
    assistant = DummyAssistant()
    listener = DummyListener()
    spoken = []

    async def speak(text: str) -> None:
        spoken.append(text)

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=_transcribe_weak_fragment,
        speak=speak,
        acknowledge=None,
        post_interaction_cooldown=0,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert assistant.calls == []
    assert spoken == ["I only caught part of that. Please repeat the question."]


@pytest.mark.unit
def test_voice_loop_listens_for_followup_after_weak_transcript_fragment():
    assistant = DummyAssistant()
    listener = DummyListener()
    spoken = []
    states: list[str] = []
    transcripts = ["What?", "what time is it"]

    async def transcribe(_: np.ndarray) -> str:
        return transcripts.pop(0)

    async def speak(text: str) -> None:
        spoken.append(text)

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=transcribe,
        speak=speak,
        acknowledge=None,
        post_interaction_cooldown=0,
        state_callback=states.append,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert assistant.calls == ["what time is it"]
    assert spoken == ["I only caught part of that. Please repeat the question.", "ok."]
    assert "followup_listening" in states


@pytest.mark.unit
def test_voice_loop_asks_followup_for_suspicious_need_transcript():
    assistant = DummyAssistant()
    listener = DummyListener()
    spoken = []
    states: list[str] = []
    transcripts = ["neutral need a knife", "I need a chocolate cake recipe"]

    async def transcribe(_: np.ndarray) -> str:
        return transcripts.pop(0)

    async def speak(text: str) -> None:
        spoken.append(text)

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=transcribe,
        speak=speak,
        acknowledge=None,
        post_interaction_cooldown=0,
        state_callback=states.append,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert assistant.calls == ["I need a chocolate cake recipe"]
    assert spoken == ["I may have misheard that. What did you need?", "ok."]
    assert "followup_listening" in states


@pytest.mark.unit
def test_voice_loop_allows_normal_knife_request():
    assert not _rvl._is_suspicious_voice_transcript("I need a knife")


@pytest.mark.unit
def test_voice_loop_flags_likely_kate_recipe_mishearing():
    assert _rvl._is_suspicious_voice_transcript("I need a person named Kate")


@pytest.mark.unit
def test_voice_loop_listens_for_followup_after_assistant_clarification():
    class ClarifyingAssistant:
        def __init__(self):
            self.calls = []

        async def generate_reply(self, transcript, *, voice_mode: bool = False):
            self.calls.append(transcript)
            if transcript == "I need":
                return "What do you need?"
            return "Here is a chocolate cake recipe"

    assistant = ClarifyingAssistant()
    listener = DummyListener()
    spoken = []
    states: list[str] = []
    transcripts = ["I need", "a chocolate cake recipe"]

    async def transcribe(_: np.ndarray) -> str:
        return transcripts.pop(0)

    async def speak(text: str) -> None:
        spoken.append(text)

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=transcribe,
        speak=speak,
        acknowledge=None,
        post_interaction_cooldown=0,
        state_callback=states.append,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert assistant.calls == ["I need", "I need a chocolate cake recipe"]
    assert spoken == ["What do you need?", "Here is a chocolate cake recipe."]
    assert "followup_listening" in states


@pytest.mark.unit
def test_voice_loop_prepends_wake_frame_preroll_before_stt():
    assistant = DummyAssistant()
    captured = []

    class OneWakeListener:
        async def listen(self, source):
            yield await source()

        def reset(self, *, reason="manual"):
            pass

    async def detection_source():
        return np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)

    async def record_phrase():
        return np.array([10.0, 11.0, 12.0], dtype=np.float32)

    async def transcribe(audio: np.ndarray) -> str:
        captured.append(audio.tolist())
        return "hello world"

    loop = VoiceLoop(
        assistant,
        wake_listener=OneWakeListener(),
        detection_source=detection_source,
        record_phrase=record_phrase,
        transcribe=transcribe,
        speak=_speak,
        acknowledge=None,
        sample_rate=4,
        post_interaction_cooldown=0,
        post_wake_preroll_seconds=0.5,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert captured == [[3.0, 4.0, 10.0, 11.0, 12.0]]
    assert assistant.calls == ["hello world"]


@pytest.mark.unit
def test_edge_tts_pcm_trim_removes_leading_and_trailing_silence():
    tts = TextToSpeech.__new__(TextToSpeech)
    pcm = np.zeros((1000, 1), dtype=np.int16)
    pcm[400:600, 0] = 1000

    trimmed = tts._trim_pcm_silence(pcm, 1000, padding_ms=0)

    assert trimmed.shape == (200, 1)
    assert trimmed[0, 0] == 1000
    assert trimmed[-1, 0] == 1000


@pytest.mark.unit
def test_voice_loop_handles_audio_format_error(caplog):
    assistant = DummyAssistant()
    listener = DummyListener()

    async def failing_transcribe(_: np.ndarray) -> str:
        raise AudioFormatError("Expected WAV, got ID3")

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=failing_transcribe,
        speak=_speak,
        acknowledge=None,
    )

    with caplog.at_level("ERROR"):
        asyncio.run(loop.run(max_interactions=1))

    assert assistant.calls == []
    assert "Expected WAV, got ID3" not in caplog.text
    assert any(
        getattr(record, "error_code", None) == "AudioFormatError" for record in caplog.records
    )


@pytest.mark.unit
def test_voice_loop_identify_speaker_receives_audio_frame():
    assistant = DummyAssistant()
    listener = DummyListener()
    captured = []

    def identify(audio: np.ndarray) -> str | None:
        captured.append(audio.tolist())
        return "alice"

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=_transcribe,
        speak=_speak,
        acknowledge=None,
        identify_speaker=identify,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert captured == [[1.0, 1.0, 1.0, 1.0]]


@pytest.mark.unit
def test_voice_loop_identify_speaker_without_args_still_supported():
    assistant = DummyAssistant()
    listener = DummyListener()
    calls = []

    def identify() -> str | None:
        calls.append("called")
        return "alice"

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=_constant_frame,
        record_phrase=_record_phrase,
        transcribe=_transcribe,
        speak=_speak,
        acknowledge=None,
        identify_speaker=identify,
    )

    asyncio.run(loop.run(max_interactions=1))

    assert calls == ["called"]


def test_voice_loop_propagates_audio_errors():
    assistant = DummyAssistant()
    listener = DummyListener()

    async def broken_source():
        raise AudioDeviceError("no mic")

    loop = VoiceLoop(
        assistant,
        wake_listener=listener,
        detection_source=broken_source,
        record_phrase=_record_phrase,
        transcribe=_transcribe,
        speak=_speak,
        acknowledge=None,
    )

    # The loop handles the error internally and retries until max_interactions reached.
    asyncio.run(loop.run(max_interactions=1))

    assert assistant.calls == []


def test_build_voice_loop_raises_for_missing_configured_input_device(monkeypatch):
    class DummySoundDevice:
        @staticmethod
        def query_devices():
            return [
                {"name": "Mic 0", "max_input_channels": 1},
                {"name": "Mic 1", "max_input_channels": 1},
            ]

    monkeypatch.setattr(_rvl, "sd", DummySoundDevice())
    monkeypatch.setattr(_rvl.settings, "audio_input_device", 9)

    with pytest.raises(AudioDeviceError) as excinfo:
        _rvl.build_voice_loop(object())

    assert str(excinfo.value) == "Input device 9 not found. Available: 0: Mic 0, 1: Mic 1"


# ---------------------------------------------------------------------------
# Temp file cleanup tests (US-196)
# ---------------------------------------------------------------------------


def _make_tts(monkeypatch, tmp_path):
    """Build a minimal TextToSpeech with a fake XTTS engine and temp files in tmp_path."""
    _orig_ntf = tempfile.NamedTemporaryFile

    def _patched_ntf(*args, **kwargs):
        kwargs["dir"] = str(tmp_path)
        return _orig_ntf(*args, **kwargs)

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _patched_ntf)
    # Model a successful local playback path without requiring audio hardware.
    monkeypatch.setattr(_rvl, "sa", MagicMock())

    tts = TextToSpeech.__new__(TextToSpeech)
    tts._language = "en"
    tts._default_speaker = None
    tts._tts_speed = 1.0
    tts._provider = "xtts"
    tts._xtts_init_error = None

    fake_engine = MagicMock()

    def _write_dummy_wav(text, speaker_wav, language, file_path, speed):
        Path(file_path).write_bytes(b"\x00" * 44)

    fake_engine.tts_to_file.side_effect = _write_dummy_wav
    tts._tts = fake_engine
    return tts


def test_no_leftover_wav_files_after_synthesize_chunk(monkeypatch, tmp_path):
    """Temp .wav files must be cleaned up after a successful synthesis cycle."""
    tts = _make_tts(monkeypatch, tmp_path)
    sf_mock = MagicMock()

    asyncio.run(tts._synthesize_and_play_chunk("hello world", None, sf_mock))

    leftover = list(tmp_path.glob("*.wav"))
    assert leftover == [], f"Leftover .wav files after success: {leftover}"


def test_temp_wav_cleaned_up_even_on_synthesis_error(monkeypatch, tmp_path):
    """Temp .wav files must be cleaned up even when the TTS engine raises."""
    _orig_ntf = tempfile.NamedTemporaryFile

    def _patched_ntf(*args, **kwargs):
        kwargs["dir"] = str(tmp_path)
        return _orig_ntf(*args, **kwargs)

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _patched_ntf)
    monkeypatch.setattr(_rvl, "sa", None)

    tts = TextToSpeech.__new__(TextToSpeech)
    tts._language = "en"
    tts._default_speaker = None
    tts._tts_speed = 1.0
    tts._provider = "xtts"
    tts._xtts_init_error = None

    fake_engine = MagicMock()
    fake_engine.tts_to_file.side_effect = RuntimeError("synthesis exploded")
    tts._tts = fake_engine

    sf_mock = MagicMock()

    with pytest.raises(RuntimeError):
        asyncio.run(tts._synthesize_and_play_chunk("hello", None, sf_mock))

    leftover = list(tmp_path.glob("*.wav"))
    assert leftover == [], f"Leftover .wav files after error: {leftover}"
