"""Protected TTS route tests (POST /mobile/tts/playback).

Matrix rows: TTS-001..TTS-007, TTS-010..TTS-012, TTS-014 (rate limit shape
is covered by the shared limiter tests in test_app.py).
"""

from __future__ import annotations

from tests.mobile_api.conftest import auth_header, create_user, paired_login_tokens


def _authed(client, username: str = "james", password: str = "pw-123456") -> dict:
    create_user(username, password)
    tokens = paired_login_tokens(client, username, password)
    return auth_header(tokens["access_token"])


class TestTtsPlayback:
    def test_missing_auth_rejected_before_synthesis(self, client, fake_tts) -> None:
        """TTS-001."""
        response = client.post("/mobile/tts/playback", json={"text": "hello"})
        assert response.status_code == 401
        assert fake_tts.synthesized == []

    def test_valid_text_returns_mobile_playable_data_url(self, client, fake_tts) -> None:
        """TTS-002/TTS-010: canonical mobile URL response is directly playable."""
        headers = _authed(client)
        response = client.post(
            "/mobile/tts/playback", json={"text": "The lights are off."}, headers=headers
        )
        assert response.status_code == 200
        body = response.get_json()
        assert set(body.keys()) == {"request_id", "audio_url"}
        assert body["audio_url"] == "data:audio/wav;base64,ZmFrZS10dHMtYXVkaW8="
        assert body["request_id"]
        assert fake_tts.synthesized == [("The lights are off.", "fake-default-voice")]

    def test_empty_text_rejected(self, client, fake_tts) -> None:
        """TTS-003."""
        headers = _authed(client)
        response = client.post("/mobile/tts/playback", json={"text": "   "}, headers=headers)
        assert response.status_code == 400
        assert fake_tts.synthesized == []

    def test_oversized_text_rejected_before_synthesis(self, client, fake_tts) -> None:
        """TTS-004."""
        headers = _authed(client)
        response = client.post("/mobile/tts/playback", json={"text": "x" * 2_001}, headers=headers)
        assert response.status_code == 400
        assert fake_tts.synthesized == []

    def test_unknown_voice_is_an_error_not_a_silent_fallback(self, client, fake_tts) -> None:
        """TTS-005."""
        headers = _authed(client)
        response = client.post(
            "/mobile/tts/playback",
            json={"text": "hello", "voice": "nonexistent-voice"},
            headers=headers,
        )
        assert response.status_code == 400
        assert fake_tts.synthesized == []

    def test_allowed_voice_is_used(self, client, fake_tts) -> None:
        """TTS-006."""
        headers = _authed(client)
        response = client.post(
            "/mobile/tts/playback",
            json={"text": "hello", "voice": "fake-alt-voice"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.get_json()["audio_url"].startswith("data:audio/wav;base64,")
        assert fake_tts.synthesized == [("hello", "fake-alt-voice")]

    def test_engine_unavailable_is_truthful(self, client, fake_tts) -> None:
        """TTS-007."""
        headers = _authed(client)
        fake_tts.available = False
        response = client.post("/mobile/tts/playback", json={"text": "hello"}, headers=headers)
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "BACKEND_UNAVAILABLE"

    def test_unknown_fields_rejected(self, client) -> None:
        headers = _authed(client)
        response = client.post(
            "/mobile/tts/playback",
            json={"text": "hello", "user_id": "someone"},
            headers=headers,
        )
        assert response.status_code == 400

    def test_routed_fallback_reports_the_provider_that_actually_synthesized(
        self, client, services
    ) -> None:
        """S35: after a VoiceStudio-to-native fallback the response must carry
        the native voice ID and MIME type, never the initially selected
        provider's ones."""
        from rex.speech.contracts import SpeechProviderTimeoutError
        from rex.speech.mobile_adapter import RoutedTextToSpeechAdapter
        from rex.speech.policy import SpeechPolicy, SpeechPolicyMode
        from rex.speech.router import SpeechRouter
        from tests.helpers.fake_speech_providers import AliasTTSProvider

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
            audio=b"NATIVE-TTS-AUDIO",
        )
        services.tts = RoutedTextToSpeechAdapter(
            SpeechRouter(
                stt_providers={},
                tts_providers={"voicestudio": voicestudio, "native": native},
                policy=SpeechPolicy(
                    mode=SpeechPolicyMode.CUSTOM,
                    allow_cloud=True,
                    tts_provider="voicestudio",
                    tts_fallback_order=("native",),
                ),
            )
        )

        headers = _authed(client)
        response = client.post(
            "/mobile/tts/playback",
            json={"text": "hello", "voice": "majel"},
            headers=headers,
        )

        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert body["audio_url"] == "data:audio/mpeg;base64,TkFUSVZFLVRUUy1BVURJTw=="
        assert native.synthesized == [("hello", "en-US-AriaNeural")]

    def test_text_never_in_logs(self, client, caplog) -> None:
        """TTS-012 (and TTS-011 by construction: POST body, no query string)."""
        headers = _authed(client)
        private = "private-speech-text-5561"
        with caplog.at_level("DEBUG"):
            client.post("/mobile/tts/playback", json={"text": private}, headers=headers)
        assert private not in caplog.text
