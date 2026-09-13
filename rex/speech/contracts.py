"""Canonical provider-neutral STT/TTS contracts (S35).

These protocols are the single seam every speech provider (native, VoiceStudio,
future cloud providers) must implement. A provider may recognize or
synthesize speech; it may never decide identity, authorization, permissions,
confirmation state, action success, or verification state -- those remain
exclusively owned by ``rex.runtime`` / ``rex.actions.lifecycle``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class SpeechProviderError(Exception):
    """Base class for truthful, bounded speech-provider failures."""


class SpeechProviderUnavailableError(SpeechProviderError):
    """The provider is not currently usable (missing dependency/config/health)."""


class SpeechProviderTimeoutError(SpeechProviderError):
    """The provider did not respond within its bounded timeout."""


class SpeechProviderResponseError(SpeechProviderError):
    """The provider returned a malformed or unexpected response."""


@dataclass(frozen=True)
class ProviderHealth:
    """Content-free, bounded provider health snapshot."""

    provider_id: str
    available: bool
    reason: str
    is_local: bool


@dataclass(frozen=True)
class TranscriptionResult:
    """Result of one STT request."""

    text: str
    provider_id: str


@dataclass(frozen=True)
class SynthesisResult:
    """Result of one TTS request."""

    audio: bytes
    mime_type: str
    provider_id: str
    voice_id: str


@runtime_checkable
class SpeechToTextProvider(Protocol):
    """Provider-neutral STT contract."""

    provider_id: str
    is_local: bool

    def availability(self) -> tuple[bool, str]:
        """Return ``(available, reason)`` without triggering a download/call."""
        ...

    def decode(self, path: str) -> Any:
        """Decode an audio file into the provider's transcription input."""
        ...

    def transcribe(self, audio: Any) -> str:
        """Transcribe already-decoded audio to text."""
        ...


@runtime_checkable
class TextToSpeechProvider(Protocol):
    """Provider-neutral TTS contract."""

    provider_id: str
    is_local: bool

    def availability(self) -> tuple[bool, str]:
        """Return ``(available, reason)`` without triggering a network call."""
        ...

    def mime_type(self) -> str:
        """Return the MIME type of audio this provider synthesizes."""
        ...

    def list_voice_ids(self) -> list[str]:
        """Return known provider-specific voice IDs (best effort)."""
        ...

    def resolve_voice(self, requested: str | None) -> str:
        """Resolve a requested (possibly aliased) voice to a provider voice ID."""
        ...

    def synthesize(self, text: str, voice_id: str) -> bytes:
        """Synthesize ``text`` with ``voice_id`` and return raw audio bytes."""
        ...


__all__ = [
    "ProviderHealth",
    "SpeechProviderError",
    "SpeechProviderResponseError",
    "SpeechProviderTimeoutError",
    "SpeechProviderUnavailableError",
    "SpeechToTextProvider",
    "SynthesisResult",
    "TextToSpeechProvider",
    "TranscriptionResult",
]
