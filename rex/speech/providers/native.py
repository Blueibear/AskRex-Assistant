"""Native provider wrappers over the existing Whisper/XTTS/edge-tts/pyttsx3 stack.

These wrap :class:`rex.mobile_api.voice.SpeechToTextAdapter` and
:class:`rex.mobile_api.voice.TextToSpeechAdapter` -- the existing, already
production-used STT/TTS seams -- as ``SpeechRouter`` providers. No engine
logic is reimplemented here; this is representation only (S35 phase 2).

This wrapper is also the normalization boundary: the legacy adapters raise
``MobileApiError`` (and the engines beneath them raise arbitrary runtime
exceptions), while the router/adapters recover only from ``SpeechProvider*``
errors. Every legacy failure is therefore translated here through
:mod:`rex.speech.normalize` so ordered fallback works in both directions
(native -> VoiceStudio and VoiceStudio -> native). Client-input truth such
as undecodable audio (HTTP 415) is deliberately *not* normalized and
propagates unchanged.
"""

from __future__ import annotations

from typing import Any

from rex.mobile_api.voice import SpeechToTextAdapter, TextToSpeechAdapter
from rex.speech.contracts import SpeechProviderResponseError
from rex.speech.normalize import normalize_legacy_error, normalized_provider_errors

NATIVE_PROVIDER_ID = "native"

# edge-tts synthesizes via a remote Microsoft cloud endpoint; the other native
# engines (xtts, pyttsx3) run entirely on-device.
_CLOUD_BACKED_TTS_ENGINES = {"edge-tts"}


class NativeSTTProvider:
    """Provider-neutral wrapper over the existing Whisper STT adapter."""

    provider_id = NATIVE_PROVIDER_ID
    is_local = True

    def __init__(self, adapter: SpeechToTextAdapter | None = None) -> None:
        self._adapter = adapter or SpeechToTextAdapter()

    def availability(self) -> tuple[bool, str]:
        try:
            return self._adapter.availability()
        except Exception as exc:
            # A failing health probe must report unavailability, never abort
            # the resolution of every other registered provider.
            return False, f"native speech-to-text availability check failed: {type(exc).__name__}"

    def decode(self, path: str) -> Any:
        with normalized_provider_errors("native speech-to-text decode"):
            return self._adapter.decode(path)

    def transcribe(self, audio: Any) -> str:
        with normalized_provider_errors("native speech-to-text"):
            return self._adapter.transcribe(audio)


class NativeTTSProvider:
    """Provider-neutral wrapper over the existing native TTS adapter.

    ``is_local`` reflects the *configured* native engine: xtts/pyttsx3 run
    on-device, while edge-tts synthesizes through a remote Microsoft
    endpoint and is therefore treated as cloud-backed for Local Only policy.
    """

    provider_id = NATIVE_PROVIDER_ID

    def __init__(self, adapter: TextToSpeechAdapter | None = None) -> None:
        self._adapter = adapter or TextToSpeechAdapter()

    @property
    def is_local(self) -> bool:
        try:
            return self._adapter.provider() not in _CLOUD_BACKED_TTS_ENGINES
        except Exception:
            # Fail closed for cloud policy: an engine that cannot be
            # identified is never assumed to keep audio on this machine.
            return False

    def availability(self) -> tuple[bool, str]:
        try:
            return self._adapter.availability()
        except Exception as exc:
            return False, f"native text-to-speech availability check failed: {type(exc).__name__}"

    def mime_type(self) -> str:
        with normalized_provider_errors("native text-to-speech MIME type"):
            return self._adapter.mime_type()

    def list_voice_ids(self) -> list[str]:
        try:
            return self._adapter._list_voice_ids()
        except Exception:
            return []

    def resolve_voice(self, requested: str | None) -> str:
        """Resolve an AskRex alias/voice ID for the *native* engine.

        An unknown voice becomes :class:`SpeechProviderResponseError` -- the
        same recoverable signal the VoiceStudio provider raises -- so a
        requested alias that this engine cannot serve lets policy try the
        next permitted provider instead of failing the whole request.
        """
        try:
            return self._adapter.resolve_voice(requested)
        except Exception as exc:
            mobile_status = getattr(exc, "http_status", None)
            if mobile_status == 400:
                raise SpeechProviderResponseError(
                    "The native text-to-speech engine has no matching voice"
                ) from exc
            normalized = normalize_legacy_error(exc, context="native text-to-speech voice")
            if normalized is exc:
                raise
            raise normalized from exc

    def synthesize(self, text: str, voice_id: str) -> bytes:
        with normalized_provider_errors("native text-to-speech"):
            return self._adapter.synthesize(text, voice_id)


__all__ = ["NATIVE_PROVIDER_ID", "NativeSTTProvider", "NativeTTSProvider"]
