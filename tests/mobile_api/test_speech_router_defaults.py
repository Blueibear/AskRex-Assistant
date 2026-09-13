"""Mobile gateway SpeechRouter opt-in / rollback tests (S35).

``speech.enabled`` defaults to false, so ``MobileApiServices.build()`` must
keep using the pre-existing native STT/TTS adapters completely unchanged
unless an operator explicitly opts in -- the documented rollback path.
"""

from __future__ import annotations

from pathlib import Path

import rex.config as rex_config_module
from rex.config import MobileApiConfig, SpeechConfig
from rex.mobile_api.db import migrate_users_db
from rex.mobile_api.services import MobileApiServices
from rex.mobile_api.voice import SpeechToTextAdapter, TextToSpeechAdapter
from rex.speech.mobile_adapter import RoutedSpeechToTextAdapter, RoutedTextToSpeechAdapter


def _build_services(mobile_env: Path) -> MobileApiServices:
    db_path = mobile_env / "users.db"
    migrate_users_db(db_path)
    return MobileApiServices.build(MobileApiConfig(), db_path=db_path)


class TestSpeechRouterDefaults:
    def test_disabled_by_default_uses_legacy_adapters(self, mobile_env: Path) -> None:
        assert rex_config_module.settings.speech.enabled is False
        services = _build_services(mobile_env)
        assert isinstance(services.stt, SpeechToTextAdapter)
        assert isinstance(services.tts, TextToSpeechAdapter)

    def test_enabled_uses_routed_adapters(
        self, mobile_env: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(rex_config_module.settings, "speech", SpeechConfig(enabled=True))
        services = _build_services(mobile_env)
        assert isinstance(services.stt, RoutedSpeechToTextAdapter)
        assert isinstance(services.tts, RoutedTextToSpeechAdapter)

    def test_explicit_override_always_wins_regardless_of_speech_config(
        self, mobile_env: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(rex_config_module.settings, "speech", SpeechConfig(enabled=True))
        db_path = mobile_env / "users.db"
        migrate_users_db(db_path)
        legacy_stt = SpeechToTextAdapter()
        legacy_tts = TextToSpeechAdapter()
        services = MobileApiServices.build(
            MobileApiConfig(), db_path=db_path, stt=legacy_stt, tts=legacy_tts
        )
        assert services.stt is legacy_stt
        assert services.tts is legacy_tts
