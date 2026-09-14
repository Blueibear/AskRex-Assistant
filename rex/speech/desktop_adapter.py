"""SpeechRouter-backed STT adapter for the desktop voice pipeline (S35).

Mirrors ``rex.speech.mobile_adapter``'s routed-provider pattern so the
desktop voice loop enforces exactly the same explicit policy
(``speech.enabled``, ``policy_mode`` including Local Only, ``allow_cloud``,
fallback order, provider availability, ``voicestudio_enabled``) the
authenticated mobile gateway already enforces through ``SpeechRouter`` --
desktop must never select a non-native provider by inspecting
``speech.stt_provider`` directly.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from rex.assistant_errors import SpeechToTextError
from rex.speech.contracts import (
    SpeechProviderResponseError,
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
)
from rex.speech.normalize import normalize_legacy_error
from rex.speech.policy import SpeechPolicyError
from rex.speech.providers.native import NATIVE_PROVIDER_ID
from rex.speech.router import SpeechRouter
from rex.voice.audio_utils import _to_wav_buffer

_RECOVERABLE_PROVIDER_ERRORS = (
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
    SpeechProviderResponseError,
)


class DesktopNativeSTT(Protocol):
    """The existing desktop STT surface (``rex.voice.stt.SpeechToText``)."""

    def is_loaded(self) -> bool: ...

    async def transcribe(self, audio: Any, sample_rate: int) -> str: ...


class RoutedDesktopSTT:
    """Desktop STT drop-in that resolves its provider through :class:`SpeechRouter`.

    A ``native`` resolution defers to the existing (already-loading/warm)
    ``native_stt`` instance unchanged, so ``speech.enabled=false`` (the
    default) and any policy that resolves to ``native`` are zero
    behaviour-change. Any other resolved provider (for example VoiceStudio)
    is transcribed directly through the resolved provider instead.
    """

    def __init__(self, router: SpeechRouter, *, native_stt: DesktopNativeSTT) -> None:
        self._router = router
        self._native_stt = native_stt

    def is_loaded(self) -> bool:
        return self._native_stt.is_loaded()

    async def transcribe(self, audio: Any, sample_rate: int) -> str:
        try:
            chain = self._router.resolve_stt_fallback_chain()
        except SpeechPolicyError as exc:
            raise SpeechToTextError(str(exc)) from exc

        last_error: BaseException | None = None
        for provider in chain:
            try:
                if provider.provider_id == NATIVE_PROVIDER_ID:
                    return await self._native_stt.transcribe(audio, sample_rate)

                wav_bytes = _to_wav_buffer(audio, sample_rate)

                def _transcribe(active_provider: Any = provider) -> str:
                    return active_provider.transcribe(wav_bytes)

                return await asyncio.to_thread(_transcribe)
            except _RECOVERABLE_PROVIDER_ERRORS as exc:
                last_error = exc
                continue
            except BaseException as exc:
                # The desktop native path is the live ``rex.voice.stt``
                # instance, not the normalizing provider wrapper, so its
                # legacy ``SpeechToTextError``/engine failures are normalized
                # here. Cancellation and interpreter-exit signals are never
                # normalized and propagate immediately.
                normalized = normalize_legacy_error(
                    exc, context=f"{provider.provider_id} speech-to-text"
                )
                if normalized is exc:
                    raise
                last_error = normalized
                continue

        message = str(last_error) if last_error else "No speech-to-text provider succeeded."
        raise SpeechToTextError(message) from last_error


__all__ = ["RoutedDesktopSTT"]
