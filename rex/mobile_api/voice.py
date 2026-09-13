"""Voice upload validation, STT adapter, and TTS adapter (issue #323, S2).

The mobile gateway reuses the existing Rex speech stack — it does not create
a second STT or TTS engine:

- Decoding uses Whisper's ffmpeg-based ``whisper.audio.load_audio`` (the
  same decode path the repository's STT already depends on), which is what
  genuinely handles M4A/MP4, AAC, MP3, and WAV.
- Transcription reuses :class:`rex.voice.stt.SpeechToText` with the
  configured model/device.
- Synthesis reuses the per-provider helpers in :mod:`rex.tts_voices`
  (XTTS / edge-tts / pyttsx3) selected by ``config.voice.tts_engine``.

Truthfulness rules: filename and declared MIME type are never trusted —
actual byte signatures are sniffed and a successful decode is required.
Missing runtime dependencies (whisper, ffmpeg, a locally cached model, the
configured TTS engine) produce ``BACKEND_UNAVAILABLE``; models are never
downloaded during a request and no mock transcript or audio is ever
returned.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
from importlib.util import find_spec
from typing import Any, Protocol, runtime_checkable

from rex.mobile_api import errors as merr
from rex.mobile_api.errors import MobileApiError

logger = logging.getLogger(__name__)

# Whisper's ffmpeg decode path resamples to 16 kHz mono float32.
WHISPER_SAMPLE_RATE = 16_000

# TTS bounds.
MAX_TTS_TEXT_CHARS = 2_000
MAX_TTS_AUDIO_BYTES = 10 * 1024 * 1024
TTS_TIMEOUT_SECONDS = 120.0

# Supported upload containers (sniffed from actual bytes, never declared
# metadata): M4A/MP4, AAC (ADTS), MP3, WAV.
SUPPORTED_CONTAINERS = ("m4a", "aac", "mp3", "wav")

_EDGE_DEFAULT_VOICE = "en-US-AriaNeural"


def sniff_audio_container(data: bytes) -> str | None:
    """Identify the audio container from file signatures (magic bytes).

    Returns one of :data:`SUPPORTED_CONTAINERS` or ``None`` when the bytes
    do not match any supported signature.  The declared MIME type and the
    filename are deliberately not consulted.
    """
    if len(data) < 12:
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3":
        return "mp3"
    if data[4:8] == b"ftyp":
        return "m4a"
    b0, b1 = data[0], data[1]
    if b0 == 0xFF and (b1 & 0xE0) == 0xE0:
        # 11-bit MPEG sync.  ADTS AAC uses layer bits '00'; MP3 does not.
        layer_bits = (b1 >> 1) & 0x03
        return "aac" if layer_bits == 0 else "mp3"
    return None


def _stt_unavailable(reason: str) -> MobileApiError:
    logger.warning("Mobile voice upload unavailable: %s", reason)
    return MobileApiError(
        merr.BACKEND_UNAVAILABLE,
        "Speech-to-text is not available on this server.",
        503,
        retryable=False,
    )


def _tts_unavailable(reason: str) -> MobileApiError:
    logger.warning("Mobile TTS unavailable: %s", reason)
    return MobileApiError(
        merr.BACKEND_UNAVAILABLE,
        "Text-to-speech is not available on this server.",
        503,
        retryable=False,
    )


@runtime_checkable
class MobileSpeechToTextService(Protocol):
    """The STT surface ``rex.mobile_api.routes.voice`` depends on.

    Both the legacy :class:`SpeechToTextAdapter` and the SpeechRouter-backed
    ``RoutedSpeechToTextAdapter`` (``rex.speech.mobile_adapter``) satisfy this
    structurally, so routes never need to know which one is active.
    """

    def availability(self) -> tuple[bool, str]: ...

    def require_available(self) -> None: ...

    def decode(self, path: str) -> Any: ...

    def transcribe(self, audio: Any) -> str: ...


@runtime_checkable
class MobileTextToSpeechService(Protocol):
    """The TTS surface ``rex.mobile_api.routes.voice`` depends on."""

    def availability(self) -> tuple[bool, str]: ...

    def require_available(self) -> None: ...

    def mime_type(self) -> str: ...

    def resolve_voice(self, requested: str | None) -> str: ...

    def synthesize(self, text: str, voice_id: str) -> bytes: ...


def _whisper_cache_dir() -> str:
    default_cache = os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(os.getenv("XDG_CACHE_HOME", default_cache), "whisper")


class SpeechToTextAdapter:
    """Adapter over the existing Whisper STT stack for file uploads."""

    def __init__(self, model_name: str | None = None, device: str | None = None) -> None:
        self._model_name = model_name
        self._device = device
        self._stt: Any = None
        self._lock = threading.Lock()

    def _resolved_model_name(self) -> str:
        if self._model_name:
            return self._model_name
        from rex.config import settings  # noqa: PLC0415

        return str(getattr(settings.voice, "stt_model", "base"))

    def _resolved_device(self) -> str:
        if self._device:
            return self._device
        from rex.config import settings  # noqa: PLC0415

        return str(getattr(settings.voice, "whisper_device", "auto"))

    def availability(self) -> tuple[bool, str]:
        """Return (available, reason).  Never triggers a model download."""
        if find_spec("whisper") is None:
            return False, "openai-whisper is not installed"
        if find_spec("numpy") is None:
            return False, "numpy is not installed"
        if shutil.which("ffmpeg") is None:
            return False, "ffmpeg is not on PATH"
        model_name = self._resolved_model_name()
        model_path = os.path.join(_whisper_cache_dir(), f"{model_name}.pt")
        if not os.path.exists(model_path):
            # Models are downloaded by explicit setup, never mid-request.
            return False, f"whisper model '{model_name}' is not downloaded"
        return True, "ok"

    def require_available(self) -> None:
        available, reason = self.availability()
        if not available:
            raise _stt_unavailable(reason)

    def decode(self, path: str) -> Any:
        """Decode an audio file to a 16 kHz mono float32 array.

        Uses Whisper's ffmpeg decode (the existing decode stack).  A decode
        failure means the media is invalid regardless of its signature.

        Raises:
            MobileApiError: 415 ``INVALID_MEDIA`` when decoding fails,
                ``BACKEND_UNAVAILABLE`` when the decoder is missing.
        """
        self.require_available()
        try:
            from whisper.audio import load_audio  # noqa: PLC0415

            audio = load_audio(path)
        except MobileApiError:
            raise
        except Exception as exc:
            logger.info("Mobile voice decode failed: %s", type(exc).__name__)
            raise MobileApiError(
                merr.INVALID_MEDIA,
                "The audio could not be decoded.",
                415,
            ) from exc
        if audio is None or getattr(audio, "size", 0) == 0:
            raise MobileApiError(merr.INVALID_MEDIA, "The audio contains no samples.", 415)
        return audio

    def _get_stt(self) -> Any:
        if self._stt is not None:
            return self._stt
        with self._lock:
            if self._stt is None:
                from rex.voice.stt import SpeechToText  # noqa: PLC0415

                self._stt = SpeechToText(
                    self._resolved_model_name(),
                    self._resolved_device(),
                )
        return self._stt

    def transcribe(self, audio: Any) -> str:
        """Transcribe a decoded 16 kHz audio array via the existing STT."""
        self.require_available()
        try:
            stt = self._get_stt()
            return str(asyncio.run(stt.transcribe(audio, WHISPER_SAMPLE_RATE))).strip()
        except MobileApiError:
            raise
        except Exception as exc:
            logger.error("Mobile voice transcription failed: %s", exc)
            raise MobileApiError(
                merr.BACKEND_UNAVAILABLE,
                "Speech-to-text failed on this server.",
                503,
                retryable=True,
            ) from exc


class TextToSpeechAdapter:
    """Adapter over the existing configured TTS providers."""

    def __init__(self, provider: str | None = None, default_voice: str | None = None) -> None:
        self._provider_override = provider
        self._default_voice_override = default_voice

    def provider(self) -> str:
        """Return the normalized configured provider name."""
        raw = self._provider_override
        if raw is None:
            from rex.config import settings  # noqa: PLC0415

            raw = str(getattr(settings.voice, "tts_engine", "xtts"))
        p = raw.lower().strip()
        if p in ("edge", "edge-tts", "edge_tts", "edgetts"):
            return "edge-tts"
        if p in ("xtts", "coqui", "coqui-tts"):
            return "xtts"
        return p

    def _configured_default_voice(self) -> str | None:
        if self._default_voice_override:
            return self._default_voice_override
        from rex.config import settings  # noqa: PLC0415

        voice = getattr(settings.voice, "tts_voice", None)
        return str(voice) if voice else None

    def availability(self) -> tuple[bool, str]:
        provider = self.provider()
        if provider == "xtts":
            if find_spec("TTS") is None:
                return False, "Coqui TTS is not installed"
        elif provider == "edge-tts":
            if find_spec("edge_tts") is None:
                return False, "edge-tts is not installed"
        elif provider == "pyttsx3":
            if find_spec("pyttsx3") is None:
                return False, "pyttsx3 is not installed"
        else:
            return False, f"unsupported TTS provider '{provider}'"

        configured = self._configured_default_voice()
        try:
            voices = self._list_voice_ids()
        except Exception:
            return False, "configured TTS voice list is unavailable"
        if configured and configured not in voices:
            return False, "configured default TTS voice is unavailable"
        if not configured and provider != "edge-tts" and not voices:
            return False, "no default TTS voice is available"
        return True, "ok"

    def require_available(self) -> None:
        available, reason = self.availability()
        if not available:
            raise _tts_unavailable(reason)

    def mime_type(self) -> str:
        return "audio/mpeg" if self.provider() == "edge-tts" else "audio/wav"

    def _list_voice_ids(self) -> list[str]:
        from rex.tts_voices import list_voices  # noqa: PLC0415

        return [str(v.get("id", "")) for v in list_voices(self.provider())]

    def resolve_voice(self, requested: str | None) -> str:
        """Resolve the requested voice to a concrete provider voice ID.

        ``None`` / ``"default"`` selects the configured or provider-default
        voice.  An explicit voice must exist for the provider — there is no
        silent fallback that pretends the requested voice was used.

        Raises:
            MobileApiError: 400 when the requested voice is unknown,
                ``BACKEND_UNAVAILABLE`` when no voice can be resolved.
        """
        self.require_available()
        provider = self.provider()
        if requested is None or requested.strip() in ("", "default"):
            configured = self._configured_default_voice()
            if configured:
                return configured
            if provider == "edge-tts":
                return _EDGE_DEFAULT_VOICE
            voices = self._list_voice_ids()
            if not voices:
                raise _tts_unavailable(f"no voices available for provider '{provider}'")
            return voices[0]

        requested = requested.strip()
        from rex.speech.aliases import resolve_alias  # noqa: PLC0415

        aliased = resolve_alias(requested, provider)
        if aliased:
            requested = aliased
        try:
            known = self._list_voice_ids()
        except Exception as exc:
            raise _tts_unavailable("voice list is unavailable") from exc
        if requested not in known:
            raise MobileApiError(
                merr.BAD_REQUEST,
                "The requested voice is not available.",
                400,
            )
        return requested

    def synthesize(self, text: str, voice_id: str) -> bytes:
        """Synthesize ``text`` with the existing provider implementation.

        Raises:
            MobileApiError: ``BACKEND_UNAVAILABLE`` on engine failure or
                timeout; ``PAYLOAD_TOO_LARGE`` when output exceeds the cap.
        """
        self.require_available()
        provider = self.provider()

        async def _run() -> bytes:
            # Reuse of the existing per-provider synthesis implementations
            # (rex.tts_voices) — not a parallel engine.
            from rex import tts_voices  # noqa: PLC0415

            if provider == "xtts":
                coro = tts_voices._synthesize_xtts(voice_id, text)
            elif provider == "edge-tts":
                coro = tts_voices._synthesize_edge_tts(voice_id, text)
            elif provider == "pyttsx3":
                coro = tts_voices._synthesize_pyttsx3(voice_id, text)
            else:  # pragma: no cover - guarded by require_available
                raise RuntimeError(f"unsupported TTS provider '{provider}'")
            return await asyncio.wait_for(coro, timeout=TTS_TIMEOUT_SECONDS)

        try:
            audio = asyncio.run(_run())
        except TimeoutError as exc:
            raise MobileApiError(
                merr.BACKEND_UNAVAILABLE,
                "Text-to-speech timed out.",
                503,
                retryable=True,
            ) from exc
        except MobileApiError:
            raise
        except Exception as exc:
            logger.error("Mobile TTS synthesis failed: %s", exc)
            raise MobileApiError(
                merr.BACKEND_UNAVAILABLE,
                "Text-to-speech failed on this server.",
                503,
                retryable=True,
            ) from exc

        if not audio:
            raise MobileApiError(
                merr.BACKEND_UNAVAILABLE,
                "Text-to-speech produced no audio.",
                503,
                retryable=True,
            )
        if len(audio) > MAX_TTS_AUDIO_BYTES:
            raise MobileApiError(
                merr.PAYLOAD_TOO_LARGE,
                "The synthesized audio is too large.",
                413,
            )
        return audio


__all__ = [
    "MAX_TTS_AUDIO_BYTES",
    "MAX_TTS_TEXT_CHARS",
    "SUPPORTED_CONTAINERS",
    "TTS_TIMEOUT_SECONDS",
    "WHISPER_SAMPLE_RATE",
    "MobileSpeechToTextService",
    "MobileTextToSpeechService",
    "SpeechToTextAdapter",
    "TextToSpeechAdapter",
    "sniff_audio_container",
]
