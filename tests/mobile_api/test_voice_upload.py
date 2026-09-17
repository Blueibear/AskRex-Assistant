"""Voice upload route tests (POST /mobile/voice/upload).

Matrix rows: VOI-001, VOI-003..VOI-013, VOI-016..VOI-019.
"""

from __future__ import annotations

import base64
import io
import struct
import wave

from rex.mobile_api.voice import sniff_audio_container
from tests.mobile_api.conftest import auth_header, create_user, paired_login_tokens


def _wav_bytes(seconds: float = 1.0, sample_rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = int(seconds * sample_rate)
        wav_file.writeframes(struct.pack(f"<{frames}h", *([0] * frames)))
    return buffer.getvalue()


def _authed(client, username: str = "james", password: str = "pw-123456") -> tuple[str, dict]:
    user_id = create_user(username, password)
    tokens = paired_login_tokens(client, username, password)
    return user_id, auth_header(tokens["access_token"])


def _upload(client, headers, data: bytes, *, filename="rec.wav", mimetype="audio/wav", extra=None):
    form = {"audio": (io.BytesIO(data), filename, mimetype), "mode": "mobile_voice"}
    if extra:
        form.update(extra)
    return client.post(
        "/mobile/voice/upload",
        data=form,
        headers=headers,
        content_type="multipart/form-data",
    )


class TestSniffing:
    def test_signatures(self) -> None:
        assert sniff_audio_container(_wav_bytes()) == "wav"
        assert sniff_audio_container(b"ID3" + b"\x00" * 16) == "mp3"
        assert sniff_audio_container(b"\xff\xfb\x90\x00" + b"\x00" * 16) == "mp3"
        assert sniff_audio_container(b"\xff\xf1\x50\x80" + b"\x00" * 16) == "aac"
        assert sniff_audio_container(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 8) == "m4a"
        assert sniff_audio_container(b"GIF89a" + b"\x00" * 16) is None
        assert sniff_audio_container(b"") is None


class TestVoiceUpload:
    def test_missing_auth_rejected_before_processing(self, client, fake_stt) -> None:
        """VOI-001."""
        response = client.post("/mobile/voice/upload", data={})
        assert response.status_code == 401
        assert fake_stt.decoded_paths == []

    def test_valid_audio_transcribes_and_answers(
        self, client, fake_stt, fake_chat_service, fake_tts
    ) -> None:
        """VOI-002: transcript → canonical Assistant with the principal ID."""
        user_id, headers = _authed(client)
        response = _upload(client, headers, _wav_bytes())
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert body["transcript"] == fake_stt.transcript
        assert body["response"] == f"echo[{user_id}]: {fake_stt.transcript}"
        assert body["status"] == "completed"
        assert body["tool_used"] is None
        assert body["request_id"]
        assert base64.b64decode(body["tts_base64"]) == fake_tts.audio
        assert body["tts_mime_type"] == "audio/wav"
        assert set(body) == {
            "request_id",
            "transcript",
            "response",
            "status",
            "tool_used",
            "tts_base64",
            "tts_mime_type",
        }
        assert fake_chat_service.calls == [(fake_stt.transcript, user_id)]

    def test_tts_unavailable_omits_both_optional_tts_fields(self, client, fake_tts) -> None:
        """The text response remains canonical when reply TTS is unavailable."""
        _, headers = _authed(client)
        fake_tts.available = False

        response = _upload(client, headers, _wav_bytes())

        assert response.status_code == 200
        assert set(response.get_json()) == {
            "request_id",
            "transcript",
            "response",
            "status",
            "tool_used",
        }

    def test_missing_audio_part(self, client) -> None:
        """VOI-003."""
        _, headers = _authed(client)
        response = client.post(
            "/mobile/voice/upload",
            data={"mode": "mobile_voice"},
            headers=headers,
            content_type="multipart/form-data",
        )
        assert response.status_code == 400

    def test_multiple_audio_parts_rejected(self, client) -> None:
        """VOI-004."""
        import io as _io

        from werkzeug.datastructures import MultiDict

        _, headers = _authed(client)
        form = MultiDict(
            [
                ("audio", (_io.BytesIO(_wav_bytes()), "a.wav", "audio/wav")),
                ("audio", (_io.BytesIO(_wav_bytes()), "b.wav", "audio/wav")),
                ("mode", "mobile_voice"),
            ]
        )
        response = client.post(
            "/mobile/voice/upload",
            data=form,
            headers=headers,
            content_type="multipart/form-data",
        )
        assert response.status_code == 400

    def test_empty_file_rejected(self, client) -> None:
        """VOI-005."""
        _, headers = _authed(client)
        response = _upload(client, headers, b"")
        assert response.status_code == 415
        assert response.get_json()["error"]["code"] == "INVALID_MEDIA"

    def test_lying_mime_and_extension_are_ignored(self, client, fake_stt) -> None:
        """VOI-006/VOI-007/VOI-008: signatures control, not declarations."""
        _, headers = _authed(client)
        junk = b"GIF89a" + b"\x00" * 64
        response = _upload(client, headers, junk, filename="real.wav", mimetype="audio/wav")
        assert response.status_code == 415
        assert response.get_json()["error"]["code"] == "INVALID_MEDIA"
        assert fake_stt.decoded_paths == []

    def test_malformed_container_fails_after_decode(self, client, fake_stt) -> None:
        """VOI-009: a valid signature with undecodable content is rejected."""
        _, headers = _authed(client)
        fake_stt.decode_fails = True
        response = _upload(client, headers, _wav_bytes())
        assert response.status_code == 415

    def test_oversized_file_rejected_before_stt(self, client, fake_stt, services) -> None:
        """VOI-010."""
        _, headers = _authed(client)
        big = _wav_bytes() + b"\x00" * services.config.max_audio_bytes
        response = _upload(client, headers, big)
        assert response.status_code == 413
        assert fake_stt.decoded_paths == []

    def test_overlong_duration_rejected_before_assistant(
        self, client, fake_stt, fake_chat_service
    ) -> None:
        """VOI-011."""
        _, headers = _authed(client)
        fake_stt.decode_seconds = 61.0
        response = _upload(client, headers, _wav_bytes())
        assert response.status_code == 413
        assert fake_chat_service.calls == []

    def test_stt_unavailable_is_truthful(self, client, fake_stt, fake_chat_service) -> None:
        """VOI-012/VOI-013: BACKEND_UNAVAILABLE, no mock transcript."""
        _, headers = _authed(client)
        fake_stt.available = False
        response = _upload(client, headers, _wav_bytes())
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "BACKEND_UNAVAILABLE"
        assert fake_chat_service.calls == []

    def test_client_user_id_field_rejected(self, client, fake_chat_service) -> None:
        _, headers = _authed(client)
        response = _upload(client, headers, _wav_bytes(), extra={"user_id": "someone-else"})
        assert response.status_code == 400
        assert fake_chat_service.calls == []

    def test_two_users_get_separate_identity(self, client, fake_chat_service, fake_stt) -> None:
        """VOI-016."""
        user_a, headers_a = _authed(client, "james", "pw-123456")
        user_b, headers_b = _authed(client, "cole", "pw-abcdef")
        assert _upload(client, headers_a, _wav_bytes()).status_code == 200
        assert _upload(client, headers_b, _wav_bytes()).status_code == 200
        assert [call[1] for call in fake_chat_service.calls] == [user_a, user_b]

    def test_temp_file_removed_after_request(self, client, fake_stt) -> None:
        """VOI-017: the private temp path is gone on success."""
        import os

        _, headers = _authed(client)
        assert _upload(client, headers, _wav_bytes()).status_code == 200
        assert len(fake_stt.decoded_paths) == 1
        assert not os.path.exists(fake_stt.decoded_paths[0])

    def test_temp_file_removed_after_failure(self, client, fake_stt) -> None:
        """VOI-017 (failure path)."""
        import os

        _, headers = _authed(client)
        fake_stt.decode_fails = True
        assert _upload(client, headers, _wav_bytes()).status_code == 415
        assert len(fake_stt.decoded_paths) == 1
        assert not os.path.exists(fake_stt.decoded_paths[0])

    def test_transcript_and_response_not_logged(self, client, fake_stt, caplog) -> None:
        """VOI-018."""
        _, headers = _authed(client)
        fake_stt.transcript = "extremely private transcript 7788"
        with caplog.at_level("DEBUG"):
            _upload(client, headers, _wav_bytes())
        assert fake_stt.transcript not in caplog.text

    def test_status_never_upgraded(self, client) -> None:
        """VOI-019: conversational replies are 'completed', never 'verified'."""
        _, headers = _authed(client)
        body = _upload(client, headers, _wav_bytes()).get_json()
        assert body["status"] == "completed"
