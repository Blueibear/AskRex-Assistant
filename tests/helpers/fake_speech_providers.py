"""Deterministic SpeechRouter provider fakes (S35).

Shared by the router, routed-adapter, and mobile-gateway route tests so
provider-neutral routing behaviour is asserted against one definition of a
provider that owns a private voice-ID namespace.

``AliasTTSProvider`` deliberately rejects any voice ID it did not issue.
That is what makes a defect observable: if a caller resolves an AskRex voice
alias once against the initially selected provider and then reuses that
provider-specific ID on a fallback provider, the fallback fails instead of
silently returning audio labelled with a voice it never used.
"""

from __future__ import annotations

from typing import Any

from rex.speech.contracts import SpeechProviderResponseError


class AliasTTSProvider:
    """TTS provider with its own voice-ID namespace, MIME type, and audio."""

    def __init__(
        self,
        provider_id: str,
        *,
        voices: dict[str, str],
        mime: str = "audio/wav",
        audio: bytes = b"audio-bytes",
        is_local: bool = True,
        synthesis_error: Exception | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.is_local = is_local
        self._voices = dict(voices)
        self._mime = mime
        self._audio = audio
        self._synthesis_error = synthesis_error
        self.synthesized: list[tuple[str, str]] = []

    def availability(self) -> tuple[bool, str]:
        return True, "ok"

    def mime_type(self) -> str:
        return self._mime

    def list_voice_ids(self) -> list[str]:
        return sorted(set(self._voices.values()))

    def resolve_voice(self, requested: str | None) -> str:
        if requested is None or requested in ("", "default"):
            return next(iter(self._voices.values()))
        if requested in self._voices:
            return self._voices[requested]
        if requested in self._voices.values():
            return requested
        raise SpeechProviderResponseError(f"{self.provider_id} has no voice '{requested}'")

    def synthesize(self, text: str, voice_id: str) -> bytes:
        if voice_id not in self._voices.values():
            raise SpeechProviderResponseError(
                f"{self.provider_id} was handed a foreign voice ID '{voice_id}'"
            )
        if self._synthesis_error is not None:
            raise self._synthesis_error
        self.synthesized.append((text, voice_id))
        return self._audio


class LegacyTTSEngine:
    """Legacy ``TextToSpeechAdapter`` surface with injectable failures.

    Used to prove the native wrapper normalizes the pre-existing stack's
    ``MobileApiError``/engine exceptions into recoverable provider errors.
    """

    def __init__(
        self,
        *,
        provider_name: str = "xtts",
        voice_id: str = "en-US-AriaNeural",
        provider_error: BaseException | None = None,
        voice_error: BaseException | None = None,
        synthesis_error: BaseException | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._voice_id = voice_id
        self._provider_error = provider_error
        self._voice_error = voice_error
        self._synthesis_error = synthesis_error

    def provider(self) -> str:
        if self._provider_error is not None:
            raise self._provider_error
        return self._provider_name

    def availability(self) -> tuple[bool, str]:
        return True, "ok"

    def mime_type(self) -> str:
        return "audio/wav"

    def _list_voice_ids(self) -> list[str]:
        return [self._voice_id]

    def resolve_voice(self, requested: str | None) -> str:
        if self._voice_error is not None:
            raise self._voice_error
        return self._voice_id

    def synthesize(self, text: str, voice_id: str) -> bytes:
        if self._synthesis_error is not None:
            raise self._synthesis_error
        return b"native-audio"


class LegacySTTEngine:
    """Legacy ``SpeechToTextAdapter`` surface with injectable failures."""

    def __init__(
        self,
        *,
        error: BaseException | None = None,
        availability_error: BaseException | None = None,
        text: str = "native-text",
    ) -> None:
        self._error = error
        self._availability_error = availability_error
        self._text = text

    def availability(self) -> tuple[bool, str]:
        if self._availability_error is not None:
            raise self._availability_error
        return True, "ok"

    def decode(self, path: str) -> Any:
        if self._error is not None:
            raise self._error
        import numpy as np

        return np.zeros(16_000, dtype=np.float32)

    def transcribe(self, audio: Any) -> str:
        if self._error is not None:
            raise self._error
        return self._text


__all__ = ["AliasTTSProvider", "LegacySTTEngine", "LegacyTTSEngine"]
