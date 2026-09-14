"""SpeechRouter-backed adapters for the authenticated mobile gateway (S35).

These implement the exact same public surface as the legacy
:class:`rex.mobile_api.voice.SpeechToTextAdapter` /
:class:`~rex.mobile_api.voice.TextToSpeechAdapter` (see
:class:`~rex.mobile_api.voice.MobileSpeechToTextService` /
:class:`~rex.mobile_api.voice.MobileTextToSpeechService`), so
``rex.mobile_api.routes.voice`` and ``rex.mobile_api.services`` never need to
know whether the active provider is native or VoiceStudio.

Decoding always uses the existing Whisper ffmpeg decode path
(:meth:`rex.mobile_api.voice.SpeechToTextAdapter.decode`) regardless of which
provider ultimately transcribes, so the caller's audio-duration validation
(``rex.mobile_api.routes.voice._decode_upload``) is completely unaffected by
provider selection. Only *transcription* and *synthesis* are routed.
"""

from __future__ import annotations

import logging
from typing import Any

from rex.mobile_api import errors as merr
from rex.mobile_api.errors import MobileApiError
from rex.mobile_api.voice import MAX_TTS_AUDIO_BYTES, WHISPER_SAMPLE_RATE, SpeechToTextAdapter
from rex.speech.contracts import (
    SpeechProviderResponseError,
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
)
from rex.speech.policy import SpeechPolicyError
from rex.speech.providers.native import NATIVE_PROVIDER_ID
from rex.speech.router import SpeechRouter
from rex.voice.audio_utils import _to_wav_buffer

logger = logging.getLogger(__name__)


def _map_provider_error(exc: Exception, *, retryable: bool = True) -> MobileApiError:
    if isinstance(exc, SpeechProviderTimeoutError):
        return MobileApiError(
            merr.BACKEND_UNAVAILABLE, "The speech provider timed out.", 503, retryable=True
        )
    if isinstance(exc, (SpeechProviderUnavailableError, SpeechPolicyError)):
        return MobileApiError(
            merr.BACKEND_UNAVAILABLE,
            "Speech processing is not available on this server.",
            503,
            retryable=False,
        )
    return MobileApiError(
        merr.BACKEND_UNAVAILABLE,
        "Speech processing failed on this server.",
        503,
        retryable=retryable,
    )


class RoutedSpeechToTextAdapter:
    """STT adapter that resolves its provider through :class:`SpeechRouter`."""

    def __init__(
        self, router: SpeechRouter, *, decoder: SpeechToTextAdapter | None = None
    ) -> None:
        self._router = router
        self._decoder = decoder or SpeechToTextAdapter()

    def availability(self) -> tuple[bool, str]:
        try:
            provider = self._router.resolve_stt_provider()
        except SpeechPolicyError as exc:
            return False, str(exc)
        return provider.availability()

    def require_available(self) -> None:
        available, reason = self.availability()
        if not available:
            logger.warning("Routed mobile STT unavailable: %s", reason)
            raise MobileApiError(
                merr.BACKEND_UNAVAILABLE,
                "Speech-to-text is not available on this server.",
                503,
                retryable=False,
            )

    def decode(self, path: str) -> Any:
        return self._decoder.decode(path)

    def transcribe(self, audio: Any) -> str:
        try:
            chain = self._router.resolve_stt_fallback_chain()
        except SpeechPolicyError as exc:
            raise _map_provider_error(exc) from exc

        last_error: Exception | None = None
        for provider in chain:
            try:
                if provider.provider_id == NATIVE_PROVIDER_ID:
                    return provider.transcribe(audio)
                wav_bytes = _to_wav_buffer(audio, WHISPER_SAMPLE_RATE)
                return provider.transcribe(wav_bytes)
            except (
                SpeechProviderTimeoutError,
                SpeechProviderUnavailableError,
                SpeechProviderResponseError,
            ) as exc:
                last_error = exc
                continue
        fallback_error = last_error or SpeechProviderUnavailableError("exhausted")
        raise _map_provider_error(fallback_error) from last_error


class RoutedTextToSpeechAdapter:
    """TTS adapter that resolves its provider through :class:`SpeechRouter`."""

    def __init__(self, router: SpeechRouter) -> None:
        self._router = router

    def availability(self) -> tuple[bool, str]:
        try:
            provider = self._router.resolve_tts_provider()
        except SpeechPolicyError as exc:
            return False, str(exc)
        return provider.availability()

    def require_available(self) -> None:
        available, reason = self.availability()
        if not available:
            logger.warning("Routed mobile TTS unavailable: %s", reason)
            raise MobileApiError(
                merr.BACKEND_UNAVAILABLE,
                "Text-to-speech is not available on this server.",
                503,
                retryable=False,
            )

    def mime_type(self) -> str:
        try:
            provider = self._router.resolve_tts_provider()
        except SpeechPolicyError:
            return "audio/wav"
        return provider.mime_type()

    def resolve_voice(self, requested: str | None) -> str:
        try:
            provider = self._router.resolve_tts_provider()
        except SpeechPolicyError as exc:
            raise _map_provider_error(exc) from exc
        try:
            return provider.resolve_voice(requested)
        except SpeechProviderResponseError as exc:
            raise MobileApiError(
                merr.BAD_REQUEST, "The requested voice is not available.", 400
            ) from exc
        except (SpeechProviderTimeoutError, SpeechProviderUnavailableError) as exc:
            raise _map_provider_error(exc) from exc

    def synthesize(self, text: str, voice_id: str) -> bytes:
        try:
            chain = self._router.resolve_tts_fallback_chain()
        except SpeechPolicyError as exc:
            raise _map_provider_error(exc) from exc

        audio: bytes | None = None
        last_error: Exception | None = None
        for provider in chain:
            try:
                audio = provider.synthesize(text, voice_id)
                break
            except (
                SpeechProviderTimeoutError,
                SpeechProviderUnavailableError,
                SpeechProviderResponseError,
            ) as exc:
                last_error = exc
                continue
        if audio is None:
            fallback_error = last_error or SpeechProviderUnavailableError("exhausted")
            raise _map_provider_error(fallback_error) from last_error
        if not audio:
            raise MobileApiError(
                merr.BACKEND_UNAVAILABLE,
                "Text-to-speech produced no audio.",
                503,
                retryable=True,
            )
        if len(audio) > MAX_TTS_AUDIO_BYTES:
            raise MobileApiError(
                merr.PAYLOAD_TOO_LARGE, "The synthesized audio is too large.", 413
            )
        return audio


__all__ = ["RoutedSpeechToTextAdapter", "RoutedTextToSpeechAdapter"]
