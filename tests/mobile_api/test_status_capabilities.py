"""Status, capabilities, and capability-privacy tests.

Matrix rows: FND-016, FND-017, CAP-001, CAP-009, CAP-010.
"""

from __future__ import annotations

import json

from rex.mobile_api.voice import TextToSpeechAdapter


class TestStatus:
    def test_status_is_minimal_and_public(self, client) -> None:
        response = client.get("/mobile/status")
        assert response.status_code == 200
        body = response.get_json()
        assert body["status"] == "ok"
        assert body["api_version"] == "1.0"
        assert body["server_version"]
        assert body["request_id"]
        assert set(body.keys()) == {
            "status",
            "api_version",
            "server_version",
            "request_id",
        }


class TestCapabilities:
    def test_capabilities_are_truthful(self, client) -> None:
        """CAP-001/CAP-010: implemented+available features true; the rest false.

        The test services inject available STT/TTS adapters and the
        validated WebSocket stack is installed, so the Session 2 features
        report true; unimplemented surfaces stay false.
        """
        response = client.get("/mobile/capabilities")
        assert response.status_code == 200
        body = response.get_json()
        assert body["api_version"] == "1.0"
        assert body["minimum_app_version"] == "0.1.0"
        features = body["features"]
        for name in (
            "authentication",
            "chat",
            "chat_streaming",
            "websocket_chat",
            "voice_upload",
            "tts",
            "attachments",
        ):
            assert features[name] is True, name
        for name in (
            "live_voice",
            "notifications",
            "approvals",
            "home_assistant",
        ):
            assert features[name] is False, name

    def test_capabilities_reflect_runtime_dependencies(self, client, fake_stt, fake_tts) -> None:
        """CAP-004/CAP-005: a missing runtime dependency turns a feature false."""
        fake_stt.available = False
        fake_tts.available = False
        features = client.get("/mobile/capabilities").get_json()["features"]
        assert features["voice_upload"] is False
        assert features["tts"] is False
        assert features["chat"] is True
        assert features["attachments"] is True

    def test_chat_and_websocket_require_runtime_and_registration(
        self, client, services, fake_chat_service
    ) -> None:
        fake_chat_service.available = False
        features = client.get("/mobile/capabilities").get_json()["features"]
        assert features["chat"] is False
        assert features["chat_streaming"] is False
        assert features["websocket_chat"] is False
        assert features["attachments"] is True

        fake_chat_service.available = True
        services.websocket_registered = False
        features = client.get("/mobile/capabilities").get_json()["features"]
        assert features["chat"] is True
        assert features["websocket_chat"] is False

    def test_capability_adapter_exception_fails_closed(self, client, fake_stt) -> None:
        def explode():
            raise RuntimeError("readiness probe failed")

        fake_stt.availability = explode
        features = client.get("/mobile/capabilities").get_json()["features"]
        assert features["voice_upload"] is False

    def test_tts_readiness_requires_configured_default_voice(self, monkeypatch) -> None:
        adapter = TextToSpeechAdapter(provider="edge-tts", default_voice="missing-voice")
        monkeypatch.setattr("rex.mobile_api.voice.find_spec", lambda _name: object())
        monkeypatch.setattr(adapter, "_list_voice_ids", lambda: ["available-voice"])
        assert adapter.availability()[0] is False

    def test_capabilities_expose_no_sensitive_data(self, client) -> None:
        """CAP-009: no paths, tokens, account IDs, usernames, or model paths."""
        response = client.get("/mobile/capabilities")
        body = response.get_json()
        assert set(body.keys()) == {
            "api_version",
            "minimum_app_version",
            "server_version",
            "features",
        }
        text = json.dumps(body)
        assert "\\\\" not in text  # no Windows paths
        assert "C:/" not in text and "C:\\" not in text
        assert "users.db" not in text
        assert "secret" not in text.lower()
