"""SpeechRouter-backed mobile gateway adapter tests (S35).

Verifies ``RoutedSpeechToTextAdapter`` / ``RoutedTextToSpeechAdapter`` present
the exact same public surface the legacy adapters do, and translate provider
failures into truthful ``MobileApiError`` outcomes rather than raising raw
provider exceptions across the gateway boundary.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from rex.mobile_api.errors import MobileApiError
from rex.speech.contracts import SpeechProviderResponseError, SpeechProviderTimeoutError
from rex.speech.mobile_adapter import RoutedSpeechToTextAdapter, RoutedTextToSpeechAdapter
from rex.speech.policy import SpeechPolicy, SpeechPolicyMode
from rex.speech.providers.native import NATIVE_PROVIDER_ID
from rex.speech.router import SpeechRouter


class FakeDecoder:
    def decode(self, path: str) -> Any:
        return np.zeros(16_000, dtype=np.float32)


class FakeSTTProvider:
    def __init__(
        self, provider_id: str, *, text: str = "hi", error: Exception | None = None
    ) -> None:
        self.provider_id = provider_id
        self.is_local = True
        self._text = text
        self._error = error
        self.received: list[Any] = []

    def availability(self) -> tuple[bool, str]:
        return True, "ok"

    def decode(self, path: str) -> Any:  # pragma: no cover - decode is not routed
        raise AssertionError("provider.decode should never be called by the routed adapter")

    def transcribe(self, audio: Any) -> str:
        self.received.append(audio)
        if self._error:
            raise self._error
        return self._text


class FakeTTSProvider:
    def __init__(self, provider_id: str, *, error: Exception | None = None) -> None:
        self.provider_id = provider_id
        self.is_local = True
        self._error = error

    def availability(self) -> tuple[bool, str]:
        return True, "ok"

    def mime_type(self) -> str:
        return "audio/wav"

    def list_voice_ids(self) -> list[str]:
        return ["default-voice"]

    def resolve_voice(self, requested: str | None) -> str:
        if requested == "bad-voice":
            raise SpeechProviderResponseError("unknown voice")
        return requested or "default-voice"

    def synthesize(self, text: str, voice_id: str) -> bytes:
        if self._error:
            raise self._error
        return b"audio-bytes"


def _router(stt: dict[str, Any], tts: dict[str, Any]) -> SpeechRouter:
    return SpeechRouter(
        stt_providers=stt,
        tts_providers=tts,
        policy=SpeechPolicy(mode=SpeechPolicyMode.AUTOMATIC),
    )


class TestRoutedSpeechToTextAdapter:
    def test_native_provider_receives_decoded_numpy_audio(self) -> None:
        native = FakeSTTProvider(NATIVE_PROVIDER_ID, text="hello")
        adapter = RoutedSpeechToTextAdapter(_router({"native": native}, {}), decoder=FakeDecoder())
        audio = adapter.decode("/tmp/a.wav")
        assert adapter.transcribe(audio) == "hello"
        assert len(native.received) == 1
        assert native.received[0] is audio

    def test_non_native_provider_receives_wav_bytes(self) -> None:
        voicestudio = FakeSTTProvider("voicestudio", text="vs-text")
        adapter = RoutedSpeechToTextAdapter(
            _router({"voicestudio": voicestudio}, {}), decoder=FakeDecoder()
        )
        audio = adapter.decode("/tmp/a.wav")
        assert adapter.transcribe(audio) == "vs-text"
        assert len(voicestudio.received) == 1
        assert isinstance(voicestudio.received[0], (bytes, bytearray))
        assert voicestudio.received[0][:4] == b"RIFF"

    def test_provider_timeout_becomes_mobile_api_error(self) -> None:
        native = FakeSTTProvider(NATIVE_PROVIDER_ID, error=SpeechProviderTimeoutError("timed out"))
        adapter = RoutedSpeechToTextAdapter(_router({"native": native}, {}), decoder=FakeDecoder())
        audio = adapter.decode("/tmp/a.wav")
        with pytest.raises(MobileApiError) as exc_info:
            adapter.transcribe(audio)
        assert exc_info.value.retryable is True

    def test_availability_reflects_resolved_provider(self) -> None:
        adapter = RoutedSpeechToTextAdapter(_router({}, {}))
        available, _reason = adapter.availability()
        assert available is False
        with pytest.raises(MobileApiError):
            adapter.require_available()


class TestRoutedTextToSpeechAdapter:
    def test_synthesize_round_trip(self) -> None:
        native = FakeTTSProvider(NATIVE_PROVIDER_ID)
        adapter = RoutedTextToSpeechAdapter(_router({}, {"native": native}))
        voice_id = adapter.resolve_voice(None)
        assert adapter.synthesize("hello", voice_id) == b"audio-bytes"
        assert adapter.mime_type() == "audio/wav"

    def test_unknown_voice_becomes_bad_request(self) -> None:
        native = FakeTTSProvider(NATIVE_PROVIDER_ID)
        adapter = RoutedTextToSpeechAdapter(_router({}, {"native": native}))
        with pytest.raises(MobileApiError) as exc_info:
            adapter.resolve_voice("bad-voice")
        assert exc_info.value.http_status == 400

    def test_no_provider_available_is_truthful_unavailable(self) -> None:
        adapter = RoutedTextToSpeechAdapter(_router({}, {}))
        available, _reason = adapter.availability()
        assert available is False
        with pytest.raises(MobileApiError):
            adapter.require_available()
