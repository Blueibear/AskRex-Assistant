"""``SpeechConfig`` validation and default-rollback tests (S35)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rex.config import SpeechConfig


class TestSpeechConfigDefaults:
    def test_disabled_by_default(self) -> None:
        cfg = SpeechConfig()
        assert cfg.enabled is False
        assert cfg.voicestudio_enabled is False
        assert cfg.stt_provider == "native"
        assert cfg.tts_provider == "native"
        assert cfg.allow_cloud is False
        assert cfg.policy_mode == "prefer_local"


class TestSpeechConfigValidation:
    def test_invalid_policy_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SpeechConfig(policy_mode="not-a-real-mode")

    def test_empty_provider_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SpeechConfig(stt_provider="   ")

    def test_non_positive_voicestudio_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SpeechConfig(voicestudio_timeout_seconds=0)

    def test_provider_names_are_normalized_to_lowercase(self) -> None:
        cfg = SpeechConfig(stt_provider="VoiceStudio")
        assert cfg.stt_provider == "voicestudio"
