"""SpeechRouter-backed desktop STT adapter tests (S35).

Verifies ``RoutedDesktopSTT`` resolves its provider through a real
``SpeechRouter``/``SpeechPolicy`` (not a stub), so Local Only, cloud
permission, and fallback-order enforcement are proven for the desktop path
exactly as they already are for the mobile gateway path.
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pytest

from rex.assistant_errors import SpeechToTextError
from rex.speech.contracts import SpeechProviderTimeoutError, SpeechProviderUnavailableError
from rex.speech.desktop_adapter import RoutedDesktopSTT
from rex.speech.policy import SpeechPolicy, SpeechPolicyMode
from rex.speech.providers.native import NATIVE_PROVIDER_ID
from rex.speech.router import SpeechRouter


class FakeNativeSTT:
    """Stand-in for the existing desktop ``rex.voice.stt.SpeechToText``."""

    def __init__(self, *, text: str = "native-text", loaded: bool = True) -> None:
        self._text = text
        self._loaded = loaded
        self.calls: list[tuple[Any, int]] = []

    def is_loaded(self) -> bool:
        return self._loaded

    async def transcribe(self, audio: Any, sample_rate: int) -> str:
        self.calls.append((audio, sample_rate))
        return self._text


class FakeProvider:
    def __init__(
        self, provider_id: str, *, is_local: bool = True, text: str = "hi", error=None
    ) -> None:
        self.provider_id = provider_id
        self.is_local = is_local
        self._text = text
        self._error = error
        self.received: list[Any] = []

    def availability(self) -> tuple[bool, str]:
        return True, "ok"

    def transcribe(self, audio: Any) -> str:
        self.received.append(audio)
        if self._error:
            raise self._error
        return self._text


def _router(stt: dict[str, Any], *, policy: SpeechPolicy) -> SpeechRouter:
    return SpeechRouter(stt_providers=stt, tts_providers={}, policy=policy)


class TestRoutedDesktopSTTNativePassthrough:
    def test_native_resolution_delegates_unchanged_to_native_stt(self) -> None:
        native = FakeNativeSTT(text="hello native")
        router = _router(
            {NATIVE_PROVIDER_ID: FakeProvider(NATIVE_PROVIDER_ID)},
            policy=SpeechPolicy(mode=SpeechPolicyMode.AUTOMATIC),
        )
        adapter = RoutedDesktopSTT(router, native_stt=native)

        audio = np.zeros(16_000, dtype=np.float32)
        result = adapter.transcribe(audio, 16_000)
        assert asyncio.run(result) == "hello native"
        assert native.calls == [(audio, 16_000)]

    def test_is_loaded_reflects_native_stt(self) -> None:
        native = FakeNativeSTT(loaded=False)
        router = _router(
            {NATIVE_PROVIDER_ID: FakeProvider(NATIVE_PROVIDER_ID)},
            policy=SpeechPolicy(mode=SpeechPolicyMode.AUTOMATIC),
        )
        adapter = RoutedDesktopSTT(router, native_stt=native)
        assert adapter.is_loaded() is False


class TestRoutedDesktopSTTNonNativeProvider:
    def test_non_native_provider_receives_wav_bytes(self) -> None:
        voicestudio = FakeProvider("voicestudio", text="vs-transcript")
        router = _router(
            {"voicestudio": voicestudio},
            policy=SpeechPolicy(
                mode=SpeechPolicyMode.CUSTOM, stt_provider="voicestudio", allow_cloud=True
            ),
        )
        native = FakeNativeSTT()
        adapter = RoutedDesktopSTT(router, native_stt=native)

        audio = np.zeros(16_000, dtype=np.float32)
        result = asyncio.run(adapter.transcribe(audio, 16_000))
        assert result == "vs-transcript"
        assert native.calls == []
        assert len(voicestudio.received) == 1
        assert voicestudio.received[0][:4] == b"RIFF"

    def test_provider_error_becomes_truthful_speech_to_text_error(self) -> None:
        voicestudio = FakeProvider(
            "voicestudio", error=SpeechProviderTimeoutError("VoiceStudio timed out")
        )
        router = _router(
            {"voicestudio": voicestudio},
            policy=SpeechPolicy(
                mode=SpeechPolicyMode.CUSTOM, stt_provider="voicestudio", allow_cloud=True
            ),
        )
        adapter = RoutedDesktopSTT(router, native_stt=FakeNativeSTT())

        audio = np.zeros(16_000, dtype=np.float32)
        with pytest.raises(SpeechToTextError):
            asyncio.run(adapter.transcribe(audio, 16_000))


class TestRoutedDesktopSTTPolicyEnforcement:
    def test_local_only_never_reaches_cloud_provider(self) -> None:
        """A non-loopback provider must never be selected under Local Only, even if explicit."""
        remote = FakeProvider("cloud-stt", is_local=False, text="should-never-be-used")
        router = _router(
            {"cloud-stt": remote},
            policy=SpeechPolicy(
                mode=SpeechPolicyMode.LOCAL_ONLY, stt_provider="cloud-stt", allow_cloud=True
            ),
        )
        adapter = RoutedDesktopSTT(router, native_stt=FakeNativeSTT())

        audio = np.zeros(16_000, dtype=np.float32)
        with pytest.raises(SpeechToTextError):
            asyncio.run(adapter.transcribe(audio, 16_000))
        assert remote.received == []

    def test_cloud_provider_blocked_when_allow_cloud_false(self) -> None:
        remote = FakeProvider("cloud-stt", is_local=False)
        router = _router(
            {"cloud-stt": remote},
            policy=SpeechPolicy(
                mode=SpeechPolicyMode.AUTOMATIC, stt_provider="cloud-stt", allow_cloud=False
            ),
        )
        adapter = RoutedDesktopSTT(router, native_stt=FakeNativeSTT())

        audio = np.zeros(16_000, dtype=np.float32)
        with pytest.raises(SpeechToTextError):
            asyncio.run(adapter.transcribe(audio, 16_000))
        assert remote.received == []

    def test_fallback_order_reaches_native_when_explicit_provider_unavailable(self) -> None:
        unavailable = FakeProvider("voicestudio")
        unavailable.availability = lambda: (False, "unreachable")  # type: ignore[method-assign]
        native = FakeNativeSTT(text="fallback-native")
        router = _router(
            {NATIVE_PROVIDER_ID: FakeProvider(NATIVE_PROVIDER_ID), "voicestudio": unavailable},
            policy=SpeechPolicy(
                mode=SpeechPolicyMode.CUSTOM,
                stt_provider="voicestudio",
                stt_fallback_order=(NATIVE_PROVIDER_ID,),
                allow_cloud=True,
            ),
        )
        adapter = RoutedDesktopSTT(router, native_stt=native)

        audio = np.zeros(16_000, dtype=np.float32)
        result = asyncio.run(adapter.transcribe(audio, 16_000))
        assert result == "fallback-native"

    def test_provider_unavailable_error_at_transcribe_time_is_truthful(self) -> None:
        flaky = FakeProvider(
            "voicestudio", error=SpeechProviderUnavailableError("VoiceStudio is unreachable")
        )
        router = _router(
            {"voicestudio": flaky},
            policy=SpeechPolicy(
                mode=SpeechPolicyMode.CUSTOM, stt_provider="voicestudio", allow_cloud=True
            ),
        )
        adapter = RoutedDesktopSTT(router, native_stt=FakeNativeSTT())

        audio = np.zeros(16_000, dtype=np.float32)
        with pytest.raises(SpeechToTextError):
            asyncio.run(adapter.transcribe(audio, 16_000))
