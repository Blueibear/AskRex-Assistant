"""Native provider wrapper + voice-alias wiring tests (S35)."""

from __future__ import annotations

import pytest

from rex.mobile_api.errors import MobileApiError
from rex.mobile_api.voice import TextToSpeechAdapter
from rex.speech.providers.native import NativeSTTProvider, NativeTTSProvider


def _adapter_with_voices(monkeypatch: pytest.MonkeyPatch, voices: list[str]) -> TextToSpeechAdapter:
    adapter = TextToSpeechAdapter(provider="edge-tts")
    monkeypatch.setattr(adapter, "require_available", lambda: None)
    monkeypatch.setattr(
        "rex.tts_voices.list_voices",
        lambda provider, **kwargs: [{"id": v} for v in voices],
    )
    return adapter


class TestTextToSpeechAdapterAliasResolution:
    def test_known_alias_resolves_to_provider_voice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_voices(monkeypatch, ["en-US-AriaNeural", "en-US-AndrewNeural"])
        assert adapter.resolve_voice("majel") == "en-US-AriaNeural"
        assert adapter.resolve_voice("james") == "en-US-AndrewNeural"

    def test_alias_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_voices(monkeypatch, ["en-US-AriaNeural"])
        assert adapter.resolve_voice("MAJEL") == "en-US-AriaNeural"

    def test_non_alias_literal_voice_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_voices(monkeypatch, ["en-US-SomeOtherVoice"])
        assert adapter.resolve_voice("en-US-SomeOtherVoice") == "en-US-SomeOtherVoice"

    def test_unknown_voice_still_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_voices(monkeypatch, ["en-US-AriaNeural"])
        with pytest.raises(MobileApiError):
            adapter.resolve_voice("not-a-real-voice")


class TestNativeProviderWrapping:
    def test_stt_provider_metadata(self) -> None:
        provider = NativeSTTProvider()
        assert provider.provider_id == "native"
        assert provider.is_local is True

    def test_tts_provider_is_local_reflects_engine(self) -> None:
        xtts_provider = NativeTTSProvider(TextToSpeechAdapter(provider="xtts"))
        edge_provider = NativeTTSProvider(TextToSpeechAdapter(provider="edge-tts"))
        assert xtts_provider.is_local is True
        assert edge_provider.is_local is False

    def test_tts_resolve_voice_delegates_to_adapter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_voices(monkeypatch, ["en-US-AriaNeural"])
        provider = NativeTTSProvider(adapter)
        assert provider.resolve_voice("majel") == "en-US-AriaNeural"
