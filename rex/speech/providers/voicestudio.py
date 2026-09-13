"""VoiceStudio provider client (S35).

VoiceStudio (``debpalash/VoiceStudio``) is an external speech service
consumed only through HTTP; its source/internals are never imported into
AskRex. The previously observed OpenAI-compatible routes
(``/v1/audio/speech``, ``/v1/audio/transcriptions``, ``/v1/audio/voices``)
are treated as configurable hypotheses -- operators may override any path
without a code change if the deployed VoiceStudio instance differs.

VoiceStudio is expected on loopback (``127.0.0.1``) by default; a
non-loopback ``base_url`` is treated as cloud-equivalent for Local Only /
cloud-permission policy purposes, since audio would leave the local machine.

No silent cloud fallback: callers (the router/policy layer) decide whether
this provider is even offered, based on ``is_local`` and the configured
``allow_cloud`` policy. This module only performs bounded, truthful HTTP
calls and never invents transcripts/audio on failure.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Any, Protocol
from urllib.parse import urlsplit

from rex.speech.aliases import resolve_alias
from rex.speech.contracts import (
    SpeechProviderResponseError,
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
)

logger = logging.getLogger(__name__)

VOICESTUDIO_PROVIDER_ID = "voicestudio"

_LOOPBACK_HOSTS = {"localhost"}


def is_loopback_url(url: str) -> bool:
    """Return True when ``url`` resolves to a loopback host.

    Used only to classify a configured VoiceStudio endpoint as local vs.
    cloud-equivalent for policy purposes; it never grants network access by
    itself.
    """
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class VoiceStudioConfig:
    """Bounded VoiceStudio client configuration."""

    base_url: str = "http://127.0.0.1:8020"
    timeout_seconds: float = 30.0
    api_key: str | None = None
    stt_model: str | None = None
    tts_model: str | None = None
    default_voice: str | None = None
    transcriptions_path: str = "/v1/audio/transcriptions"
    speech_path: str = "/v1/audio/speech"
    voices_path: str = "/v1/audio/voices"
    voice_aliases: dict[str, str] = field(default_factory=dict)

    @property
    def is_local(self) -> bool:
        return is_loopback_url(self.base_url)


class VoiceStudioTransport(Protocol):
    """Injectable HTTP transport so providers are unit-testable without a network."""

    def get_json(self, url: str, *, headers: dict[str, str], timeout: float) -> Any: ...

    def post_json_for_bytes(
        self, url: str, body: dict[str, Any], *, headers: dict[str, str], timeout: float
    ) -> bytes: ...

    def post_multipart_for_json(
        self,
        url: str,
        fields: dict[str, str],
        file_field: tuple[str, str, bytes, str],
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> Any: ...


class UrllibVoiceStudioTransport:
    """Default :mod:`urllib` transport (no new third-party HTTP dependency)."""

    def get_json(self, url: str, *, headers: dict[str, str], timeout: float) -> Any:
        request = urllib.request.Request(url, headers=headers, method="GET")
        return self._send(request, timeout)

    def post_json_for_bytes(
        self, url: str, body: dict[str, Any], *, headers: dict[str, str], timeout: float
    ) -> bytes:
        payload = json.dumps(body).encode("utf-8")
        merged_headers = {**headers, "Content-Type": "application/json"}
        request = urllib.request.Request(
            url, data=payload, headers=merged_headers, method="POST"
        )
        return self._send_raw(request, timeout)

    def post_multipart_for_json(
        self,
        url: str,
        fields: dict[str, str],
        file_field: tuple[str, str, bytes, str],
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> Any:
        boundary = uuid.uuid4().hex
        body = bytearray()
        for name, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(value.encode("utf-8"))
            body.extend(b"\r\n")
        field_name, filename, data, content_type = file_field
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{filename}"\r\n'
            ).encode()
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.extend(data)
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode())

        merged_headers = {
            **headers,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        request = urllib.request.Request(
            url, data=bytes(body), headers=merged_headers, method="POST"
        )
        return self._send(request, timeout)

    def _send_raw(self, request: urllib.request.Request, timeout: float) -> bytes:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except TimeoutError as exc:
            raise SpeechProviderTimeoutError("VoiceStudio request timed out") from exc
        except urllib.error.URLError as exc:
            raise SpeechProviderUnavailableError(f"VoiceStudio is unreachable: {exc}") from exc

    def _send(self, request: urllib.request.Request, timeout: float) -> Any:
        raw = self._send_raw(request, timeout)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SpeechProviderResponseError(
                "VoiceStudio returned a malformed JSON response"
            ) from exc


def _auth_headers(config: VoiceStudioConfig) -> dict[str, str]:
    if not config.api_key:
        return {}
    return {"Authorization": f"Bearer {config.api_key}"}


class VoiceStudioSTTProvider:
    """VoiceStudio STT provider over its OpenAI-compatible transcription route."""

    provider_id = VOICESTUDIO_PROVIDER_ID

    def __init__(
        self,
        config: VoiceStudioConfig | None = None,
        *,
        transport: VoiceStudioTransport | None = None,
    ) -> None:
        self._config = config or VoiceStudioConfig()
        self._transport = transport or UrllibVoiceStudioTransport()

    @property
    def is_local(self) -> bool:
        return self._config.is_local

    def availability(self) -> tuple[bool, str]:
        try:
            self._transport.get_json(
                self._config.base_url + self._config.voices_path,
                headers=_auth_headers(self._config),
                timeout=min(5.0, self._config.timeout_seconds),
            )
        except SpeechProviderTimeoutError:
            logger.warning("VoiceStudio STT availability check timed out")
            return False, "VoiceStudio did not respond in time"
        except SpeechProviderUnavailableError as exc:
            logger.warning("VoiceStudio STT unavailable: %s", exc)
            return False, str(exc)
        except SpeechProviderResponseError as exc:
            logger.warning("VoiceStudio STT returned a malformed response: %s", exc)
            return False, str(exc)
        return True, "ok"

    def decode(self, path: str) -> Any:
        """Read the raw audio bytes; VoiceStudio decodes server-side."""
        with open(path, "rb") as fh:
            return fh.read()

    def transcribe(self, audio: Any) -> str:
        if not isinstance(audio, (bytes, bytearray)):
            raise SpeechProviderResponseError("VoiceStudio STT requires raw decoded audio bytes")
        fields: dict[str, str] = {}
        if self._config.stt_model:
            fields["model"] = self._config.stt_model
        response = self._transport.post_multipart_for_json(
            self._config.base_url + self._config.transcriptions_path,
            fields,
            ("file", "audio.wav", bytes(audio), "application/octet-stream"),
            headers=_auth_headers(self._config),
            timeout=self._config.timeout_seconds,
        )
        if not isinstance(response, dict) or "text" not in response:
            raise SpeechProviderResponseError(
                "VoiceStudio transcription response did not include 'text'"
            )
        return str(response["text"]).strip()


class VoiceStudioTTSProvider:
    """VoiceStudio TTS provider over its OpenAI-compatible speech route."""

    provider_id = VOICESTUDIO_PROVIDER_ID

    def __init__(
        self,
        config: VoiceStudioConfig | None = None,
        *,
        transport: VoiceStudioTransport | None = None,
    ) -> None:
        self._config = config or VoiceStudioConfig()
        self._transport = transport or UrllibVoiceStudioTransport()

    @property
    def is_local(self) -> bool:
        return self._config.is_local

    def availability(self) -> tuple[bool, str]:
        try:
            self._transport.get_json(
                self._config.base_url + self._config.voices_path,
                headers=_auth_headers(self._config),
                timeout=min(5.0, self._config.timeout_seconds),
            )
        except SpeechProviderTimeoutError:
            logger.warning("VoiceStudio TTS availability check timed out")
            return False, "VoiceStudio did not respond in time"
        except SpeechProviderUnavailableError as exc:
            logger.warning("VoiceStudio TTS unavailable: %s", exc)
            return False, str(exc)
        except SpeechProviderResponseError as exc:
            logger.warning("VoiceStudio TTS returned a malformed response: %s", exc)
            return False, str(exc)
        return True, "ok"

    def mime_type(self) -> str:
        return "audio/wav"

    def list_voice_ids(self) -> list[str]:
        try:
            response = self._transport.get_json(
                self._config.base_url + self._config.voices_path,
                headers=_auth_headers(self._config),
                timeout=self._config.timeout_seconds,
            )
        except (
            SpeechProviderTimeoutError,
            SpeechProviderUnavailableError,
            SpeechProviderResponseError,
        ):
            return []
        entries = response.get("data") if isinstance(response, dict) else response
        if not isinstance(entries, list):
            return []
        ids: list[str] = []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id"):
                ids.append(str(entry["id"]))
            elif isinstance(entry, str):
                ids.append(entry)
        return ids

    def resolve_voice(self, requested: str | None) -> str:
        if requested and requested.strip() not in ("", "default"):
            requested = requested.strip()
            aliased = self._config.voice_aliases.get(requested) or resolve_alias(
                requested, self.provider_id
            )
            if aliased:
                return aliased
            known = self.list_voice_ids()
            if known and requested not in known:
                raise SpeechProviderResponseError(
                    f"VoiceStudio has no voice named '{requested}'"
                )
            return requested
        if self._config.default_voice:
            return self._config.default_voice
        known = self.list_voice_ids()
        if not known:
            raise SpeechProviderUnavailableError("VoiceStudio has no voices available")
        return known[0]

    def synthesize(self, text: str, voice_id: str) -> bytes:
        body: dict[str, Any] = {
            "input": text,
            "voice": voice_id,
            "response_format": "wav",
        }
        if self._config.tts_model:
            body["model"] = self._config.tts_model
        return self._transport.post_json_for_bytes(
            self._config.base_url + self._config.speech_path,
            body,
            headers=_auth_headers(self._config),
            timeout=self._config.timeout_seconds,
        )


class VoiceStudioDesktopSTT:
    """Desktop voice-loop STT drop-in that transcribes via VoiceStudio.

    Matches the minimal duck-typed surface the desktop pipeline uses
    (``async def transcribe(self, audio, sample_rate) -> str`` and
    ``is_loaded()``) so ``rex.voice.builder`` can select it in place of the
    Whisper-backed :class:`rex.voice.stt.SpeechToText` behind an explicit
    ``speech.stt_provider = "voicestudio"`` opt-in, with no effect on the
    default native STT path.
    """

    def __init__(self, config: VoiceStudioConfig | None = None) -> None:
        self._provider = VoiceStudioSTTProvider(config)

    def is_loaded(self) -> bool:
        available, _reason = self._provider.availability()
        return available

    async def transcribe(self, audio: Any, sample_rate: int) -> str:
        import asyncio

        from rex.voice.audio_utils import _to_wav_buffer

        wav_bytes = _to_wav_buffer(audio, sample_rate)

        def _transcribe() -> str:
            return self._provider.transcribe(wav_bytes)

        return await asyncio.to_thread(_transcribe)


__all__ = [
    "VOICESTUDIO_PROVIDER_ID",
    "UrllibVoiceStudioTransport",
    "VoiceStudioConfig",
    "VoiceStudioDesktopSTT",
    "VoiceStudioSTTProvider",
    "VoiceStudioTTSProvider",
    "VoiceStudioTransport",
    "is_loopback_url",
]
