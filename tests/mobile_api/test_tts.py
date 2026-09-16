"""Protected TTS route tests (POST /mobile/tts/playback).

Matrix rows: TTS-001..TTS-007, TTS-010..TTS-012, TTS-014 (rate limit shape
is covered by the shared limiter tests in test_app.py).
"""

from __future__ import annotations

import base64

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

    def test_valid_text_returns_base64_json(self, client, fake_tts) -> None:
        """TTS-002/TTS-010: JSON data URI with MIME type and request ID."""
        headers = _authed(client)
        response = client.post(
            "/mobile/tts/playback", json={"text": "The lights are off."}, headers=headers
        )
        assert response.status_code == 200
        body = response.get_json()
        assert set(body.keys()) == {
            "request_id",
            "audio_url",
            "voice",
            "requested_voice",
        }
        assert body["audio_url"] == (
            "data:audio/wav;base64," + base64.b64encode(fake_tts.audio).decode("ascii")
        )
        assert body["voice"] == "fake-default-voice"
        assert body["requested_voice"] == "default"
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
        assert response.get_json()["voice"] == "fake-alt-voice"
        assert response.get_json()["requested_voice"] == "fake-alt-voice"
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

    def test_playback_mime_is_the_provider_mime_inside_the_data_uri(
        self, client, fake_tts
    ) -> None:
        """S35 canonical contract: playback MIME lives inside ``audio_url``.

        The media type is whatever ``TextToSpeechAdapter.mime_type()`` reports
        for the configured provider — an edge-tts server must emit
        ``data:audio/mpeg;base64,...`` — and there is never a separate
        ``mime_type`` or ``audio_base64`` field.
        """
        headers = _authed(client)
        fake_tts.mime = "audio/mpeg"
        response = client.post("/mobile/tts/playback", json={"text": "hello"}, headers=headers)
        assert response.status_code == 200
        body = response.get_json()
        assert set(body.keys()) == {"request_id", "audio_url", "voice", "requested_voice"}
        assert body["audio_url"] == (
            "data:audio/mpeg;base64," + base64.b64encode(fake_tts.audio).decode("ascii")
        )
        for superseded in ("audio_base64", "mime_type", "tts_base64", "ttsBase64"):
            assert superseded not in body

    def test_adapter_mime_follows_the_provider_not_the_voice(self) -> None:
        """S35: MIME is provider-derived; a voice ID must never decide it."""
        from rex.mobile_api.voice import TextToSpeechAdapter

        assert TextToSpeechAdapter(provider="edge-tts").mime_type() == "audio/mpeg"
        assert TextToSpeechAdapter(provider="edge").mime_type() == "audio/mpeg"
        assert TextToSpeechAdapter(provider="xtts").mime_type() == "audio/wav"
        assert TextToSpeechAdapter(provider="pyttsx3").mime_type() == "audio/wav"
        assert (
            TextToSpeechAdapter(provider="xtts", default_voice="en-US-AriaNeural").mime_type()
            == "audio/wav"
        )

    def test_text_never_in_logs(self, client, caplog) -> None:
        """TTS-012 (and TTS-011 by construction: POST body, no query string)."""
        headers = _authed(client)
        private = "private-speech-text-5561"
        with caplog.at_level("DEBUG"):
            client.post("/mobile/tts/playback", json={"text": private}, headers=headers)
        assert private not in caplog.text
