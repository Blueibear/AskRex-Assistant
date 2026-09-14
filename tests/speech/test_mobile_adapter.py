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

from rex.mobile_api import errors as merr
from rex.mobile_api.errors import MobileApiError
from rex.mobile_api.voice import synthesize_for_request
from rex.speech.contracts import SpeechProviderResponseError, SpeechProviderTimeoutError
from rex.speech.mobile_adapter import RoutedSpeechToTextAdapter, RoutedTextToSpeechAdapter
from rex.speech.policy import SpeechPolicy, SpeechPolicyMode
from rex.speech.providers.native import (
    NATIVE_PROVIDER_ID,
    NativeSTTProvider,
    NativeTTSProvider,
)
from rex.speech.router import SpeechRouter
from tests.helpers.fake_speech_providers import (
    AliasTTSProvider,
    LegacySTTEngine,
    LegacyTTSEngine,
)


def _custom_router(
    stt: dict[str, Any],
    tts: dict[str, Any],
    *,
    stt_provider: str | None = None,
    stt_fallback_order: tuple[str, ...] = (),
    tts_provider: str | None = None,
    tts_fallback_order: tuple[str, ...] = (),
) -> SpeechRouter:
    return SpeechRouter(
        stt_providers=stt,
        tts_providers=tts,
        policy=SpeechPolicy(
            mode=SpeechPolicyMode.CUSTOM,
            allow_cloud=True,
            stt_provider=stt_provider,
            stt_fallback_order=stt_fallback_order,
            tts_provider=tts_provider,
            tts_fallback_order=tts_fallback_order,
        ),
    )


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

    def test_provider_timeout_recovers_to_next_permitted_provider(self) -> None:
        flaky = FakeSTTProvider(
            "voicestudio", error=SpeechProviderTimeoutError("VoiceStudio timed out")
        )
        native = FakeSTTProvider(NATIVE_PROVIDER_ID, text="native-recovered")
        router = _custom_router(
            {"voicestudio": flaky, NATIVE_PROVIDER_ID: native},
            {},
            stt_provider="voicestudio",
            stt_fallback_order=(NATIVE_PROVIDER_ID,),
        )
        adapter = RoutedSpeechToTextAdapter(router, decoder=FakeDecoder())
        audio = adapter.decode("/tmp/a.wav")
        assert adapter.transcribe(audio) == "native-recovered"


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

    def test_synthesize_recovers_to_next_permitted_provider_on_timeout(self) -> None:
        flaky = FakeTTSProvider(
            "voicestudio", error=SpeechProviderTimeoutError("VoiceStudio timed out")
        )
        native = FakeTTSProvider(NATIVE_PROVIDER_ID)
        router = _custom_router(
            {},
            {"voicestudio": flaky, NATIVE_PROVIDER_ID: native},
            tts_provider="voicestudio",
            tts_fallback_order=(NATIVE_PROVIDER_ID,),
        )
        adapter = RoutedTextToSpeechAdapter(router)
        assert adapter.synthesize("hello", "default-voice") == b"audio-bytes"


class TestRoutedTextToSpeechVoiceResolutionAcrossFallback:
    """The requested AskRex alias must be resolved per attempted provider."""

    def test_voicestudio_to_native_fallback_uses_native_voice_and_mime(self) -> None:
        voicestudio = AliasTTSProvider(
            "voicestudio",
            voices={"majel": "vs_majel_v2"},
            mime="audio/wav",
            synthesis_error=SpeechProviderTimeoutError("VoiceStudio timed out"),
        )
        native = AliasTTSProvider(
            NATIVE_PROVIDER_ID,
            voices={"majel": "en-US-AriaNeural"},
            mime="audio/mpeg",
            audio=b"native-audio",
        )
        router = _custom_router(
            {},
            {"voicestudio": voicestudio, NATIVE_PROVIDER_ID: native},
            tts_provider="voicestudio",
            tts_fallback_order=(NATIVE_PROVIDER_ID,),
        )

        result = RoutedTextToSpeechAdapter(router).synthesize_request("hello", "majel")

        assert result.provider_id == NATIVE_PROVIDER_ID
        assert result.voice_id == "en-US-AriaNeural"
        assert result.mime_type == "audio/mpeg"
        assert result.audio == b"native-audio"
        assert native.synthesized == [("hello", "en-US-AriaNeural")]

    def test_native_to_voicestudio_fallback_uses_voicestudio_voice_and_mime(self) -> None:
        native = AliasTTSProvider(
            NATIVE_PROVIDER_ID,
            voices={"majel": "en-US-AriaNeural"},
            mime="audio/mpeg",
            synthesis_error=SpeechProviderTimeoutError("native engine timed out"),
        )
        voicestudio = AliasTTSProvider(
            "voicestudio",
            voices={"majel": "vs_majel_v2"},
            mime="audio/wav",
            audio=b"vs-audio",
        )
        router = _custom_router(
            {},
            {NATIVE_PROVIDER_ID: native, "voicestudio": voicestudio},
            tts_provider=NATIVE_PROVIDER_ID,
            tts_fallback_order=("voicestudio",),
        )

        result = RoutedTextToSpeechAdapter(router).synthesize_request("hello", "majel")

        assert result.provider_id == "voicestudio"
        assert result.voice_id == "vs_majel_v2"
        assert result.mime_type == "audio/wav"
        assert voicestudio.synthesized == [("hello", "vs_majel_v2")]

    def test_alias_unknown_to_primary_still_resolves_on_next_provider(self) -> None:
        voicestudio = AliasTTSProvider("voicestudio", voices={"cole": "vs_cole"})
        native = AliasTTSProvider(
            NATIVE_PROVIDER_ID, voices={"majel": "en-US-AriaNeural"}, audio=b"native-audio"
        )
        router = _custom_router(
            {},
            {"voicestudio": voicestudio, NATIVE_PROVIDER_ID: native},
            tts_provider="voicestudio",
            tts_fallback_order=(NATIVE_PROVIDER_ID,),
        )

        result = RoutedTextToSpeechAdapter(router).synthesize_request("hello", "majel")

        assert result.provider_id == NATIVE_PROVIDER_ID
        assert result.voice_id == "en-US-AriaNeural"
        assert voicestudio.synthesized == []

    def test_voice_unknown_to_every_provider_is_a_bad_request(self) -> None:
        voicestudio = AliasTTSProvider("voicestudio", voices={"cole": "vs_cole"})
        native = AliasTTSProvider(NATIVE_PROVIDER_ID, voices={"majel": "en-US-AriaNeural"})
        router = _custom_router(
            {},
            {"voicestudio": voicestudio, NATIVE_PROVIDER_ID: native},
            tts_provider="voicestudio",
            tts_fallback_order=(NATIVE_PROVIDER_ID,),
        )

        with pytest.raises(MobileApiError) as exc_info:
            RoutedTextToSpeechAdapter(router).synthesize_request("hello", "nobody")
        assert exc_info.value.http_status == 400

    def test_legacy_synthesize_never_hands_a_foreign_voice_id_to_a_fallback(self) -> None:
        """The compatibility two-step surface re-resolves per provider too.

        A provider-specific ID cannot be re-mapped into another provider's
        namespace, so the fallback truthfully refuses instead of synthesizing
        audio under a voice that provider never used.
        """
        voicestudio = AliasTTSProvider(
            "voicestudio",
            voices={"majel": "vs_majel_v2"},
            synthesis_error=SpeechProviderTimeoutError("VoiceStudio timed out"),
        )
        native = AliasTTSProvider(
            NATIVE_PROVIDER_ID, voices={"majel": "en-US-AriaNeural"}, audio=b"native-audio"
        )
        router = _custom_router(
            {},
            {"voicestudio": voicestudio, NATIVE_PROVIDER_ID: native},
            tts_provider="voicestudio",
            tts_fallback_order=(NATIVE_PROVIDER_ID,),
        )
        adapter = RoutedTextToSpeechAdapter(router)

        voice_id = adapter.resolve_voice("majel")
        assert voice_id == "vs_majel_v2"
        with pytest.raises(MobileApiError):
            adapter.synthesize("hello", voice_id)
        assert native.synthesized == []

    def test_default_voice_round_trips_through_the_legacy_surface_after_fallback(self) -> None:
        """Without an explicit voice each provider uses its own default, so the
        compatibility surface still recovers."""
        voicestudio = AliasTTSProvider(
            "voicestudio",
            voices={"majel": "vs_majel_v2"},
            synthesis_error=SpeechProviderTimeoutError("VoiceStudio timed out"),
        )
        native = AliasTTSProvider(
            NATIVE_PROVIDER_ID, voices={"majel": "en-US-AriaNeural"}, audio=b"native-audio"
        )
        router = _custom_router(
            {},
            {"voicestudio": voicestudio, NATIVE_PROVIDER_ID: native},
            tts_provider="voicestudio",
            tts_fallback_order=(NATIVE_PROVIDER_ID,),
        )
        adapter = RoutedTextToSpeechAdapter(router)

        assert adapter.synthesize("hello", "default") == b"native-audio"
        assert native.synthesized == [("hello", "en-US-AriaNeural")]

    def test_synthesize_for_request_prefers_the_single_decision_api(self) -> None:
        native = AliasTTSProvider(
            NATIVE_PROVIDER_ID,
            voices={"majel": "en-US-AriaNeural"},
            mime="audio/mpeg",
        )
        router = _custom_router(
            {},
            {NATIVE_PROVIDER_ID: native},
            tts_provider=NATIVE_PROVIDER_ID,
        )

        result = synthesize_for_request(RoutedTextToSpeechAdapter(router), "hello", "majel")

        assert result.voice_id == "en-US-AriaNeural"
        assert result.mime_type == "audio/mpeg"
        assert result.provider_id == NATIVE_PROVIDER_ID


class TestNativeRuntimeFailureFallback:
    """Native failures raised by the legacy stack must still trigger fallback."""

    def test_native_tts_legacy_failure_falls_back_to_voicestudio(self) -> None:
        native = NativeTTSProvider(
            LegacyTTSEngine(
                synthesis_error=MobileApiError(
                    merr.BACKEND_UNAVAILABLE,
                    "Text-to-speech failed on this server.",
                    503,
                    retryable=True,
                )
            )
        )
        voicestudio = AliasTTSProvider(
            "voicestudio", voices={"majel": "vs_majel_v2"}, audio=b"vs-audio"
        )
        router = _custom_router(
            {},
            {NATIVE_PROVIDER_ID: native, "voicestudio": voicestudio},
            tts_provider=NATIVE_PROVIDER_ID,
            tts_fallback_order=("voicestudio",),
        )

        result = RoutedTextToSpeechAdapter(router).synthesize_request("hello", "majel")

        assert result.provider_id == "voicestudio"
        assert result.voice_id == "vs_majel_v2"
        assert result.audio == b"vs-audio"

    def test_native_stt_legacy_failure_falls_back_to_voicestudio(self) -> None:
        native = NativeSTTProvider(
            LegacySTTEngine(
                error=MobileApiError(
                    merr.BACKEND_UNAVAILABLE,
                    "Speech-to-text failed on this server.",
                    503,
                    retryable=True,
                )
            )
        )
        voicestudio = FakeSTTProvider("voicestudio", text="vs-recovered")
        router = _custom_router(
            {NATIVE_PROVIDER_ID: native, "voicestudio": voicestudio},
            {},
            stt_provider=NATIVE_PROVIDER_ID,
            stt_fallback_order=("voicestudio",),
        )
        adapter = RoutedSpeechToTextAdapter(router, decoder=FakeDecoder())

        assert adapter.transcribe(adapter.decode("/tmp/a.wav")) == "vs-recovered"

    def test_undecodable_audio_is_not_retried_against_another_provider(self) -> None:
        """Client-input truth stays a 415 and must not be relabeled as an outage."""
        native = NativeSTTProvider(
            LegacySTTEngine(
                error=MobileApiError(merr.INVALID_MEDIA, "The audio could not be decoded.", 415)
            )
        )
        voicestudio = FakeSTTProvider("voicestudio", text="should-never-be-used")
        router = _custom_router(
            {NATIVE_PROVIDER_ID: native, "voicestudio": voicestudio},
            {},
            stt_provider=NATIVE_PROVIDER_ID,
            stt_fallback_order=("voicestudio",),
        )
        adapter = RoutedSpeechToTextAdapter(router, decoder=FakeDecoder())

        with pytest.raises(MobileApiError) as exc_info:
            adapter.transcribe(adapter.decode("/tmp/a.wav"))
        assert exc_info.value.http_status == 415
        assert voicestudio.received == []
