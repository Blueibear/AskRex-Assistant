"""``SpeechRouter`` provider-resolution tests (S35)."""

from __future__ import annotations

from typing import Any

import pytest

from rex.speech.contracts import SpeechProviderTimeoutError, SpeechProviderUnavailableError
from rex.speech.policy import SpeechPolicy, SpeechPolicyError, SpeechPolicyMode
from rex.speech.router import SpeechRouter
from tests.helpers.fake_speech_providers import AliasTTSProvider


class FakeSTTProvider:
    def __init__(
        self,
        provider_id: str,
        *,
        is_local: bool = True,
        available: bool = True,
        text: str = "hi",
        error: Exception | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.is_local = is_local
        self._available = available
        self._text = text
        self._error = error
        self.decoded: list[str] = []
        self.transcribed: list[Any] = []

    def availability(self) -> tuple[bool, str]:
        return self._available, "ok" if self._available else "unavailable"

    def decode(self, path: str) -> Any:
        self.decoded.append(path)
        return f"decoded:{path}"

    def transcribe(self, audio: Any) -> str:
        self.transcribed.append(audio)
        if self._error:
            raise self._error
        return self._text


class FakeTTSProvider:
    def __init__(
        self,
        provider_id: str,
        *,
        is_local: bool = True,
        available: bool = True,
        error: Exception | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.is_local = is_local
        self._available = available
        self._error = error
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
        if self._error:
            raise self._error
        return b"audio-bytes"


def _router(
    *,
    stt: dict[str, Any],
    tts: dict[str, Any],
    mode: SpeechPolicyMode = SpeechPolicyMode.AUTOMATIC,
    allow_cloud: bool = False,
    stt_provider: str | None = None,
    tts_provider: str | None = None,
    stt_fallback_order: tuple[str, ...] = (),
    tts_fallback_order: tuple[str, ...] = (),
) -> SpeechRouter:
    policy = SpeechPolicy(
        mode=mode,
        allow_cloud=allow_cloud,
        stt_provider=stt_provider,
        tts_provider=tts_provider,
        stt_fallback_order=stt_fallback_order,
        tts_fallback_order=tts_fallback_order,
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

    def test_transcribe_file_recovers_to_next_permitted_provider_on_runtime_failure(
        self,
    ) -> None:
        """A provider that passes its health check but times out at call time
        must not strand transcription: the router retries the next permitted
        (here: native) provider instead of raising immediately."""
        voicestudio = FakeSTTProvider(
            "voicestudio", error=SpeechProviderTimeoutError("VoiceStudio timed out")
        )
        native = FakeSTTProvider("native", text="native-recovered")
        router = _router(
            stt={"voicestudio": voicestudio, "native": native},
            tts={},
            mode=SpeechPolicyMode.CUSTOM,
            stt_provider="voicestudio",
            stt_fallback_order=("native",),
            allow_cloud=True,
        )
        result = router.transcribe_file("/tmp/audio.wav")
        assert result.text == "native-recovered"
        assert result.provider_id == "native"

    def test_transcribe_file_raises_once_entire_permitted_chain_fails(self) -> None:
        voicestudio = FakeSTTProvider(
            "voicestudio", error=SpeechProviderTimeoutError("VoiceStudio timed out")
        )
        router = _router(
            stt={"voicestudio": voicestudio},
            tts={},
            mode=SpeechPolicyMode.CUSTOM,
            stt_provider="voicestudio",
            allow_cloud=True,
        )
        with pytest.raises(SpeechPolicyError):
            router.transcribe_file("/tmp/audio.wav")

    def test_synthesize_recovers_to_next_permitted_provider_on_runtime_failure(self) -> None:
        voicestudio = FakeTTSProvider(
            "voicestudio", error=SpeechProviderTimeoutError("VoiceStudio timed out")
        )
        native = FakeTTSProvider("native")
        router = _router(
            stt={},
            tts={"voicestudio": voicestudio, "native": native},
            mode=SpeechPolicyMode.CUSTOM,
            tts_provider="voicestudio",
            tts_fallback_order=("native",),
            allow_cloud=True,
        )
        result = router.synthesize("hello")
        assert result.audio == b"audio-bytes"
        assert result.provider_id == "native"

    def test_synthesize_reports_the_voice_and_mime_of_the_provider_that_succeeded(
        self,
    ) -> None:
        """After a fallback the result must describe the provider that actually
        synthesized -- not the initially selected one."""
        voicestudio = AliasTTSProvider(
            "voicestudio",
            voices={"majel": "vs_majel_v2"},
            mime="audio/wav",
            synthesis_error=SpeechProviderTimeoutError("VoiceStudio timed out"),
        )
        native = AliasTTSProvider(
            "native",
            voices={"majel": "en-US-AriaNeural"},
            mime="audio/mpeg",
        )
        router = _router(
            stt={},
            tts={"voicestudio": voicestudio, "native": native},
            mode=SpeechPolicyMode.CUSTOM,
            tts_provider="voicestudio",
            tts_fallback_order=("native",),
            allow_cloud=True,
        )

        result = router.synthesize("hello", voice="majel")

        assert result.provider_id == "native"
        assert result.voice_id == "en-US-AriaNeural"
        assert result.mime_type == "audio/mpeg"
        assert native.synthesized == [("hello", "en-US-AriaNeural")]

    def test_native_to_voicestudio_fallback_resolves_the_alias_for_voicestudio(self) -> None:
        native = AliasTTSProvider(
            "native",
            voices={"majel": "en-US-AriaNeural"},
            mime="audio/mpeg",
            synthesis_error=SpeechProviderUnavailableError("native engine is unavailable"),
        )
        voicestudio = AliasTTSProvider(
            "voicestudio", voices={"majel": "vs_majel_v2"}, mime="audio/wav"
        )
        router = _router(
            stt={},
            tts={"native": native, "voicestudio": voicestudio},
            mode=SpeechPolicyMode.CUSTOM,
            tts_provider="native",
            tts_fallback_order=("voicestudio",),
            allow_cloud=True,
        )

        result = router.synthesize("hello", voice="majel")

        assert result.provider_id == "voicestudio"
        assert result.voice_id == "vs_majel_v2"
        assert result.mime_type == "audio/wav"

    def test_fallback_recovery_never_reaches_disallowed_cloud_provider(self) -> None:
        """Recovery must still honor Local Only: a failing local provider
        must never fall through to a cloud provider even if one is listed
        in the fallback order."""
        local = FakeSTTProvider(
            "voicestudio", error=SpeechProviderTimeoutError("VoiceStudio timed out")
        )
        cloud = FakeSTTProvider("cloud-stt", is_local=False, text="should-never-be-used")
        router = _router(
            stt={"voicestudio": local, "cloud-stt": cloud},
            tts={},
            mode=SpeechPolicyMode.LOCAL_ONLY,
            stt_provider="voicestudio",
            stt_fallback_order=("cloud-stt",),
            allow_cloud=True,
        )
        with pytest.raises(SpeechPolicyError):
            router.transcribe_file("/tmp/audio.wav")
        assert cloud.transcribed == []
