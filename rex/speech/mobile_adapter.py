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

Synthesis is one atomic provider decision: :meth:`
RoutedTextToSpeechAdapter.synthesize_request` resolves the requested AskRex
voice alias independently for every attempted provider and reports the
voice/MIME type of the provider that actually produced the audio. Resolving
a voice once against the initially selected provider and reusing that
provider-specific ID across a fallback would either be rejected by the next
provider or return mislabeled audio.
"""

from __future__ import annotations

import logging
from typing import Any

from rex.mobile_api import errors as merr
from rex.mobile_api.errors import MobileApiError
from rex.mobile_api.voice import (
    MAX_TTS_AUDIO_BYTES,
    WHISPER_SAMPLE_RATE,
    SpeechToTextAdapter,
    SynthesizedSpeech,
)
from rex.speech.contracts import (
    SpeechProviderResponseError,
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
)
from rex.speech.normalize import normalized_provider_errors
from rex.speech.policy import SpeechPolicyError
from rex.speech.providers.native import NATIVE_PROVIDER_ID
from rex.speech.router import SpeechRouter
from rex.voice.audio_utils import _to_wav_buffer

logger = logging.getLogger(__name__)

_RECOVERABLE_PROVIDER_ERRORS = (
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
    SpeechProviderResponseError,
)


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


def _unknown_voice_error() -> MobileApiError:
    return MobileApiError(merr.BAD_REQUEST, "The requested voice is not available.", 400)


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
                # A conversion failure is normalized so it disables only this
                # provider rather than the whole permitted chain.
                with normalized_provider_errors("audio conversion"):
                    wav_bytes = _to_wav_buffer(audio, WHISPER_SAMPLE_RATE)
                return provider.transcribe(wav_bytes)
            except _RECOVERABLE_PROVIDER_ERRORS as exc:
                logger.info(
                    "Routed mobile STT provider failed; trying next permitted provider "
                    "(provider=%s, error=%s)",
                    provider.provider_id,
                    type(exc).__name__,
                )
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
        """Return the currently resolved provider's MIME type.

        Compatibility surface only. Callers that need the MIME type of the
        audio they just received must use :meth:`synthesize_request`, which
        reports the provider that actually synthesized.
        """
        try:
            provider = self._router.resolve_tts_provider()
        except SpeechPolicyError:
            return "audio/wav"
        return provider.mime_type()

    def resolve_voice(self, requested: str | None) -> str:
        """Resolve ``requested`` against the first permitted provider that accepts it.

        Compatibility surface only: the returned ID belongs to one specific
        provider's namespace, so pairing it with a later
        :meth:`synthesize` call can still cross a provider boundary. The
        canonical single-decision path is :meth:`synthesize_request`.
        """
        try:
            chain = self._router.resolve_tts_fallback_chain()
        except SpeechPolicyError as exc:
            raise _map_provider_error(exc) from exc

        attempts = 0
        voice_rejections = 0
        last_error: Exception | None = None
        for provider in chain:
            attempts += 1
            try:
                return provider.resolve_voice(requested)
            except SpeechProviderResponseError as exc:
                voice_rejections += 1
                last_error = exc
                continue
            except (SpeechProviderTimeoutError, SpeechProviderUnavailableError) as exc:
                last_error = exc
                continue
        if attempts and voice_rejections == attempts:
            raise _unknown_voice_error() from last_error
        fallback_error = last_error or SpeechProviderUnavailableError("exhausted")
        raise _map_provider_error(fallback_error) from last_error

    def synthesize(self, text: str, voice_id: str) -> bytes:
        """Synthesize with an already-resolved voice ID (compatibility surface).

        Every attempted provider re-resolves ``voice_id`` into its own voice
        namespace, so a fallback provider is never handed another provider's
        ID verbatim.
        """
        return self.synthesize_request(text, voice_id).audio

    def synthesize_request(
        self, text: str, requested_voice: str | None = None
    ) -> SynthesizedSpeech:
        """Resolve voice, MIME type, and audio from one provider decision.

        The requested (possibly aliased) voice is resolved independently for
        each attempted provider, and the returned metadata always describes
        the provider that actually synthesized.
        """
        try:
            chain = self._router.resolve_tts_fallback_chain()
        except SpeechPolicyError as exc:
            raise _map_provider_error(exc) from exc

        attempts = 0
        voice_rejections = 0
        last_error: Exception | None = None
        for provider in chain:
            attempts += 1
            try:
                voice_id = provider.resolve_voice(requested_voice)
            except SpeechProviderResponseError as exc:
                # This provider has no such voice; another permitted provider
                # may map the same AskRex alias to its own voice ID.
                voice_rejections += 1
                last_error = exc
                continue
            except (SpeechProviderTimeoutError, SpeechProviderUnavailableError) as exc:
                last_error = exc
                continue

            try:
                # Read the MIME type before synthesis so the returned metadata
                # can never come from a different provider than the audio.
                mime_type = provider.mime_type()
                audio = provider.synthesize(text, voice_id)
            except _RECOVERABLE_PROVIDER_ERRORS as exc:
                logger.info(
                    "Routed mobile TTS provider failed; trying next permitted provider "
                    "(provider=%s, error=%s)",
                    provider.provider_id,
                    type(exc).__name__,
                )
                last_error = exc
                continue

            self._validate_audio(audio)
            return SynthesizedSpeech(
                audio=audio,
                mime_type=mime_type,
                voice_id=voice_id,
                provider_id=provider.provider_id,
            )

        if attempts and voice_rejections == attempts:
            raise _unknown_voice_error() from last_error
        fallback_error = last_error or SpeechProviderUnavailableError("exhausted")
        raise _map_provider_error(fallback_error) from last_error

    @staticmethod
    def _validate_audio(audio: bytes) -> None:
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


__all__ = ["RoutedSpeechToTextAdapter", "RoutedTextToSpeechAdapter"]
