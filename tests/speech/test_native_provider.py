"""Native provider wrapper + voice-alias wiring tests (S35)."""

from __future__ import annotations

import pytest

from rex.assistant_errors import SpeechToTextError, TextToSpeechError
from rex.mobile_api import errors as merr
from rex.mobile_api.errors import MobileApiError
from rex.mobile_api.voice import TextToSpeechAdapter
from rex.runtime.cancellation import TurnCancelledError
from rex.speech.contracts import (
    SpeechProviderResponseError,
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
)
from rex.speech.providers.native import NativeSTTProvider, NativeTTSProvider
from tests.helpers.fake_speech_providers import LegacySTTEngine, LegacyTTSEngine


def _adapter_with_voices(monkeypatch: pytest.MonkeyPatch, voices: list[str]) -> TextToSpeechAdapter:
    adapter = TextToSpeechAdapter(provider="edge-tts")
    monkeypatch.setattr(adapter, "require_available", lambda: None)
    monkeypatch.setattr(
        "rex.tts_voices.list_voices",
        lambda provider, **kwargs: [{"id": v} for v in voices],
    )
    return adapter


class TestTextToSpeechAdapterAliasResolution:
    def test_known_alias_resolves_to_provider_voice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_voices(monkeypatch, ["en-US-AriaNeural", "en-US-AndrewNeural"])
        assert adapter.resolve_voice("majel") == "en-US-AriaNeural"
        assert adapter.resolve_voice("james") == "en-US-AndrewNeural"

    def test_alias_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_voices(monkeypatch, ["en-US-AriaNeural"])
        assert adapter.resolve_voice("MAJEL") == "en-US-AriaNeural"

    def test_non_alias_literal_voice_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_voices(monkeypatch, ["en-US-SomeOtherVoice"])
        assert adapter.resolve_voice("en-US-SomeOtherVoice") == "en-US-SomeOtherVoice"

    def test_unknown_voice_still_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_voices(monkeypatch, ["en-US-AriaNeural"])
        with pytest.raises(MobileApiError):
            adapter.resolve_voice("not-a-real-voice")


class TestNativeProviderWrapping:
    def test_stt_provider_metadata(self) -> None:
        provider = NativeSTTProvider()
        assert provider.provider_id == "native"
        assert provider.is_local is True

    def test_tts_provider_is_local_reflects_engine(self) -> None:
        xtts_provider = NativeTTSProvider(TextToSpeechAdapter(provider="xtts"))
        edge_provider = NativeTTSProvider(TextToSpeechAdapter(provider="edge-tts"))
        assert xtts_provider.is_local is True
        assert edge_provider.is_local is False

    def test_tts_resolve_voice_delegates_to_adapter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_voices(monkeypatch, ["en-US-AriaNeural"])
        provider = NativeTTSProvider(adapter)
        assert provider.resolve_voice("majel") == "en-US-AriaNeural"


class TestNativeSTTFailureNormalization:
    """Legacy failures must become recoverable ``SpeechProvider*`` errors.

    The router and the routed adapters recover only from ``SpeechProvider*``
    errors, so an un-normalized ``MobileApiError``/``SpeechToTextError`` would
    make native-to-VoiceStudio fallback unreachable.
    """

    def test_backend_unavailable_becomes_recoverable_provider_error(self) -> None:
        legacy = LegacySTTEngine(
            error=MobileApiError(
                merr.BACKEND_UNAVAILABLE,
                "Speech-to-text failed on this server.",
                503,
                retryable=True,
            )
        )
        with pytest.raises(SpeechProviderUnavailableError):
            NativeSTTProvider(legacy).transcribe([0.0])

    def test_legacy_speech_to_text_error_becomes_recoverable_provider_error(self) -> None:
        legacy = LegacySTTEngine(error=SpeechToTextError("Speech transcription failed"))
        with pytest.raises(SpeechProviderUnavailableError):
            NativeSTTProvider(legacy).transcribe([0.0])

    def test_unexpected_engine_exception_becomes_recoverable_provider_error(self) -> None:
        legacy = LegacySTTEngine(error=RuntimeError("engine exploded"))
        with pytest.raises(SpeechProviderUnavailableError):
            NativeSTTProvider(legacy).transcribe([0.0])

    def test_timeout_becomes_provider_timeout(self) -> None:
        legacy = LegacySTTEngine(error=TimeoutError("too slow"))
        with pytest.raises(SpeechProviderTimeoutError):
            NativeSTTProvider(legacy).transcribe([0.0])

    def test_client_input_error_is_not_normalized(self) -> None:
        """Undecodable audio is client truth, not a provider outage: it must
        keep its 415 and must never be retried against another provider."""
        legacy = LegacySTTEngine(
            error=MobileApiError(merr.INVALID_MEDIA, "The audio could not be decoded.", 415)
        )
        with pytest.raises(MobileApiError) as exc_info:
            NativeSTTProvider(legacy).decode("/tmp/a.m4a")
        assert exc_info.value.http_status == 415

    def test_cancellation_is_never_normalized(self) -> None:
        legacy = LegacySTTEngine(error=TurnCancelledError("turn cancelled"))
        with pytest.raises(TurnCancelledError):
            NativeSTTProvider(legacy).transcribe([0.0])

    def test_availability_failure_reports_unavailable_instead_of_raising(self) -> None:
        legacy = LegacySTTEngine(availability_error=RuntimeError("probe failed"))
        available, reason = NativeSTTProvider(legacy).availability()
        assert available is False
        assert "RuntimeError" in reason


class TestNativeTTSFailureNormalization:
    def test_synthesis_failure_becomes_recoverable_provider_error(self) -> None:
        legacy = LegacyTTSEngine(
            synthesis_error=MobileApiError(
                merr.BACKEND_UNAVAILABLE,
                "Text-to-speech failed on this server.",
                503,
                retryable=True,
            )
        )
        with pytest.raises(SpeechProviderUnavailableError):
            NativeTTSProvider(legacy).synthesize("hello", "native-voice")

    def test_legacy_text_to_speech_error_becomes_recoverable_provider_error(self) -> None:
        legacy = LegacyTTSEngine(synthesis_error=TextToSpeechError("engine failed"))
        with pytest.raises(SpeechProviderUnavailableError):
            NativeTTSProvider(legacy).synthesize("hello", "native-voice")

    def test_unknown_voice_becomes_recoverable_response_error(self) -> None:
        """A voice this engine cannot serve must let policy try the next
        permitted provider, not fail the whole request."""
        legacy = LegacyTTSEngine(
            voice_error=MobileApiError(
                merr.BAD_REQUEST, "The requested voice is not available.", 400
            )
        )
        with pytest.raises(SpeechProviderResponseError):
            NativeTTSProvider(legacy).resolve_voice("voicestudio-only-voice")

    def test_is_local_fails_closed_when_engine_cannot_be_identified(self) -> None:
        legacy = LegacyTTSEngine(provider_error=RuntimeError("no config"))
        assert NativeTTSProvider(legacy).is_local is False
