"""``SpeechRouter`` provider-resolution tests (S35)."""

from __future__ import annotations

from typing import Any

import pytest

from rex.speech.policy import SpeechPolicy, SpeechPolicyError, SpeechPolicyMode
from rex.speech.router import SpeechRouter


class FakeSTTProvider:
    def __init__(
        self, provider_id: str, *, is_local: bool = True, available: bool = True, text: str = "hi"
    ) -> None:
        self.provider_id = provider_id
        self.is_local = is_local
        self._available = available
        self._text = text
        self.decoded: list[str] = []
        self.transcribed: list[Any] = []

    def availability(self) -> tuple[bool, str]:
        return self._available, "ok" if self._available else "unavailable"

    def decode(self, path: str) -> Any:
        self.decoded.append(path)
        return f"decoded:{path}"

    def transcribe(self, audio: Any) -> str:
        self.transcribed.append(audio)
        return self._text


class FakeTTSProvider:
    def __init__(
        self, provider_id: str, *, is_local: bool = True, available: bool = True
    ) -> None:
        self.provider_id = provider_id
        self.is_local = is_local
        self._available = available
        self.synthesized: list[tuple[str, str]] = []

    def availability(self) -> tuple[bool, str]:
        return self._available, "ok" if self._available else "unavailable"

    def mime_type(self) -> str:
        return "audio/wav"

    def list_voice_ids(self) -> list[str]:
        return ["default-voice"]

    def resolve_voice(self, requested: str | None) -> str:
        return requested or "default-voice"

    def synthesize(self, text: str, voice_id: str) -> bytes:
        self.synthesized.append((text, voice_id))
        return b"audio-bytes"


def _router(
    *,
    stt: dict[str, Any],
    tts: dict[str, Any],
    mode: SpeechPolicyMode = SpeechPolicyMode.AUTOMATIC,
    allow_cloud: bool = False,
    stt_provider: str | None = None,
    tts_provider: str | None = None,
) -> SpeechRouter:
    policy = SpeechPolicy(
        mode=mode,
        allow_cloud=allow_cloud,
        stt_provider=stt_provider,
        tts_provider=tts_provider,
    )
    return SpeechRouter(stt_providers=stt, tts_providers=tts, policy=policy)


class TestSpeechRouter:
    def test_transcribe_file_uses_resolved_provider(self) -> None:
        native = FakeSTTProvider("native", text="hello there")
        router = _router(stt={"native": native}, tts={})
        result = router.transcribe_file("/tmp/audio.wav")
        assert result.text == "hello there"
        assert result.provider_id == "native"
        assert native.decoded == ["/tmp/audio.wav"]

    def test_synthesize_uses_resolved_provider_and_voice(self) -> None:
        native = FakeTTSProvider("native")
        router = _router(stt={}, tts={"native": native})
        result = router.synthesize("hello", voice="majel")
        assert result.audio == b"audio-bytes"
        assert result.provider_id == "native"
        assert result.voice_id == "majel"
        assert native.synthesized == [("hello", "majel")]

    def test_local_only_blocks_cloud_stt(self) -> None:
        cloud = FakeSTTProvider("cloud", is_local=False)
        router = _router(
            stt={"cloud": cloud}, tts={}, mode=SpeechPolicyMode.LOCAL_ONLY, allow_cloud=True
        )
        with pytest.raises(SpeechPolicyError):
            router.resolve_stt_provider()

    def test_cloud_provider_requires_explicit_permission(self) -> None:
        cloud = FakeTTSProvider("cloud", is_local=False)
        router = _router(stt={}, tts={"cloud": cloud}, allow_cloud=False)
        with pytest.raises(SpeechPolicyError):
            router.resolve_tts_provider()

    def test_custom_explicit_provider_selection(self) -> None:
        native = FakeSTTProvider("native")
        voicestudio = FakeSTTProvider("voicestudio", text="vs-transcript")
        router = _router(
            stt={"native": native, "voicestudio": voicestudio},
            tts={},
            mode=SpeechPolicyMode.CUSTOM,
            stt_provider="voicestudio",
        )
        result = router.transcribe_file("/tmp/a.wav")
        assert result.provider_id == "voicestudio"
        assert result.text == "vs-transcript"

    def test_stt_and_tts_health_are_independent(self) -> None:
        native_stt = FakeSTTProvider("native", available=True)
        native_tts = FakeTTSProvider("native", available=False)
        router = _router(stt={"native": native_stt}, tts={"native": native_tts})
        stt_health = {h.provider_id: h.available for h in router.stt_health()}
        tts_health = {h.provider_id: h.available for h in router.tts_health()}
        assert stt_health == {"native": True}
        assert tts_health == {"native": False}
