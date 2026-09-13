"""``build_default_router`` / ``build_speech_policy`` construction tests (S35)."""

from __future__ import annotations

from rex.config import AppConfig, SpeechConfig
from rex.speech.policy import SpeechPolicyMode
from rex.speech.providers.native import NATIVE_PROVIDER_ID
from rex.speech.providers.voicestudio import VOICESTUDIO_PROVIDER_ID
from rex.speech.registry import build_default_router, build_speech_policy


def _config(**speech_overrides: object) -> AppConfig:
    return AppConfig(speech=SpeechConfig(**speech_overrides))


class TestBuildSpeechPolicy:
    def test_defaults_are_prefer_local_native_only(self) -> None:
        policy = build_speech_policy(_config())
        assert policy.mode == SpeechPolicyMode.PREFER_LOCAL
        assert policy.allow_cloud is False
        assert policy.stt_provider is None
        assert policy.tts_provider is None
        assert policy.stt_fallback_order == ("native",)
        assert policy.tts_fallback_order == ("native",)

    def test_explicit_custom_provider_is_preserved(self) -> None:
        policy = build_speech_policy(
            _config(policy_mode="custom", stt_provider="voicestudio", tts_provider="voicestudio")
        )
        assert policy.mode == SpeechPolicyMode.CUSTOM
        assert policy.stt_provider == "voicestudio"
        assert policy.tts_provider == "voicestudio"


class TestBuildDefaultRouter:
    def test_native_only_by_default(self) -> None:
        router = build_default_router(_config())
        stt_ids = {h.provider_id for h in router.stt_health()}
        tts_ids = {h.provider_id for h in router.tts_health()}
        assert stt_ids == {NATIVE_PROVIDER_ID}
        assert tts_ids == {NATIVE_PROVIDER_ID}

    def test_voicestudio_registered_only_when_enabled(self) -> None:
        router = build_default_router(_config(voicestudio_enabled=True))
        stt_ids = {h.provider_id for h in router.stt_health()}
        tts_ids = {h.provider_id for h in router.tts_health()}
        assert stt_ids == {NATIVE_PROVIDER_ID, VOICESTUDIO_PROVIDER_ID}
        assert tts_ids == {NATIVE_PROVIDER_ID, VOICESTUDIO_PROVIDER_ID}
