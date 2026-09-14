"""VoiceStudio provider client tests using an injected fake transport (S35).

No real network calls are made; the fake transport lets us exercise
availability, timeout, malformed-response, and truthful-failure behavior
deterministically.
"""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import patch

import pytest

from rex.speech.contracts import (
    SpeechProviderResponseError,
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
)
from rex.speech.providers.voicestudio import (
    UrllibVoiceStudioTransport,
    VoiceStudioConfig,
    VoiceStudioSTTProvider,
    VoiceStudioTTSProvider,
    is_loopback_url,
)


class _FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self._body = io.BytesIO(body)

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        self._body.close()

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


class FakeTransport:
    def __init__(
        self,
        *,
        get_json_result: Any = None,
        get_json_error: Exception | None = None,
        post_json_bytes_result: bytes | None = None,
        post_json_bytes_error: Exception | None = None,
        post_multipart_result: Any = None,
        post_multipart_error: Exception | None = None,
    ) -> None:
        self.get_json_result = get_json_result
        self.get_json_error = get_json_error
        self.post_json_bytes_result = post_json_bytes_result
        self.post_json_bytes_error = post_json_bytes_error
        self.post_multipart_result = post_multipart_result
        self.post_multipart_error = post_multipart_error
        self.calls: list[tuple[str, str]] = []

    def get_json(self, url: str, *, headers: dict[str, str], timeout: float) -> Any:
        self.calls.append(("get_json", url))
        if self.get_json_error:
            raise self.get_json_error
        return self.get_json_result

    def post_json_for_bytes(
        self, url: str, body: dict[str, Any], *, headers: dict[str, str], timeout: float
    ) -> bytes:
        self.calls.append(("post_json_for_bytes", url))
        if self.post_json_bytes_error:
            raise self.post_json_bytes_error
        return self.post_json_bytes_result or b""

    def post_multipart_for_json(
        self,
        url: str,
        fields: dict[str, str],
        file_field: tuple[str, str, bytes, str],
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> Any:
        self.calls.append(("post_multipart_for_json", url))
        if self.post_multipart_error:
            raise self.post_multipart_error
        return self.post_multipart_result


class TestIsLoopbackUrl:
    def test_loopback_ip_and_localhost(self) -> None:
        assert is_loopback_url("http://127.0.0.1:8020")
        assert is_loopback_url("http://localhost:8020")
        assert is_loopback_url("http://[::1]:8020")

    def test_remote_host_is_not_loopback(self) -> None:
        assert not is_loopback_url("http://example.com:8020")
        assert not is_loopback_url("http://10.0.0.5:8020")


class TestVoiceStudioSTTProvider:
    def test_is_local_reflects_base_url(self) -> None:
        local = VoiceStudioSTTProvider(VoiceStudioConfig(base_url="http://127.0.0.1:8020"))
        remote = VoiceStudioSTTProvider(VoiceStudioConfig(base_url="http://example.com"))
        assert local.is_local is True
        assert remote.is_local is False

    def test_availability_ok(self) -> None:
        transport = FakeTransport(get_json_result={"data": []})
        provider = VoiceStudioSTTProvider(transport=transport)
        assert provider.availability() == (True, "ok")

    def test_availability_uses_stt_health_when_voices_are_unavailable(self) -> None:
        transport = FakeTransport(get_json_result={"status": "ok"})
        config = VoiceStudioConfig(stt_health_path="/health/stt")
        provider = VoiceStudioSTTProvider(config, transport=transport)

        assert provider.availability() == (True, "ok")
        assert transport.calls == [("get_json", "http://127.0.0.1:3900/health/stt")]

    def test_availability_timeout(self) -> None:
        transport = FakeTransport(get_json_error=SpeechProviderTimeoutError("timed out"))
        provider = VoiceStudioSTTProvider(transport=transport)
        available, reason = provider.availability()
        assert available is False
        assert "time" in reason.lower()

    def test_availability_unreachable(self) -> None:
        transport = FakeTransport(get_json_error=SpeechProviderUnavailableError("down"))
        provider = VoiceStudioSTTProvider(transport=transport)
        available, _reason = provider.availability()
        assert available is False

    def test_transcribe_returns_text(self) -> None:
        transport = FakeTransport(post_multipart_result={"text": "hello world"})
        provider = VoiceStudioSTTProvider(transport=transport)
        assert provider.transcribe(b"fake-wav-bytes") == "hello world"
        expected_url = provider._config.base_url + "/v1/audio/transcriptions"
        assert transport.calls == [("post_multipart_for_json", expected_url)]

    def test_transcribe_malformed_response_raises(self) -> None:
        transport = FakeTransport(post_multipart_result={"unexpected": "shape"})
        provider = VoiceStudioSTTProvider(transport=transport)
        with pytest.raises(SpeechProviderResponseError):
            provider.transcribe(b"fake-wav-bytes")

    def test_transcribe_requires_bytes(self) -> None:
        provider = VoiceStudioSTTProvider(transport=FakeTransport())
        with pytest.raises(SpeechProviderResponseError):
            provider.transcribe("not-bytes")


class TestVoiceStudioTTSProvider:
    def test_mime_type_is_wav(self) -> None:
        provider = VoiceStudioTTSProvider(transport=FakeTransport())
        assert provider.mime_type() == "audio/wav"

    def test_list_voice_ids_from_data_list(self) -> None:
        transport = FakeTransport(get_json_result={"data": [{"id": "narrator"}, {"id": "cole"}]})
        provider = VoiceStudioTTSProvider(transport=transport)
        assert provider.list_voice_ids() == ["narrator", "cole"]

    def test_list_voice_ids_failure_returns_empty(self) -> None:
        transport = FakeTransport(get_json_error=SpeechProviderUnavailableError("down"))
        provider = VoiceStudioTTSProvider(transport=transport)
        assert provider.list_voice_ids() == []

    def test_resolve_voice_default_uses_configured_default(self) -> None:
        config = VoiceStudioConfig(default_voice="narrator")
        provider = VoiceStudioTTSProvider(config, transport=FakeTransport())
        assert provider.resolve_voice(None) == "narrator"
        assert provider.resolve_voice("default") == "narrator"

    def test_resolve_voice_default_falls_back_to_first_known(self) -> None:
        transport = FakeTransport(get_json_result={"data": [{"id": "narrator"}]})
        provider = VoiceStudioTTSProvider(transport=transport)
        assert provider.resolve_voice(None) == "narrator"

    def test_resolve_voice_default_raises_when_no_voices(self) -> None:
        transport = FakeTransport(get_json_result={"data": []})
        provider = VoiceStudioTTSProvider(transport=transport)
        with pytest.raises(SpeechProviderUnavailableError):
            provider.resolve_voice(None)

    def test_resolve_voice_alias_takes_precedence(self) -> None:
        config = VoiceStudioConfig(voice_aliases={"majel": "narrator-1"})
        provider = VoiceStudioTTSProvider(config, transport=FakeTransport())
        assert provider.resolve_voice("majel") == "narrator-1"

    def test_resolve_voice_unknown_explicit_id_raises(self) -> None:
        transport = FakeTransport(get_json_result={"data": [{"id": "narrator"}]})
        provider = VoiceStudioTTSProvider(transport=transport)
        with pytest.raises(SpeechProviderResponseError):
            provider.resolve_voice("does-not-exist")

    def test_synthesize_returns_audio_bytes(self) -> None:
        transport = FakeTransport(post_json_bytes_result=b"RIFF....WAVE")
        provider = VoiceStudioTTSProvider(transport=transport)
        assert provider.synthesize("hello", "narrator") == b"RIFF....WAVE"

    def test_synthesize_timeout_propagates(self) -> None:
        transport = FakeTransport(post_json_bytes_error=SpeechProviderTimeoutError("timed out"))
        provider = VoiceStudioTTSProvider(transport=transport)
        with pytest.raises(SpeechProviderTimeoutError):
            provider.synthesize("hello", "narrator")


class TestUrllibVoiceStudioTransportResponseBounds:
    @pytest.mark.parametrize(
        "operation",
        ["health", "transcription", "tts"],
    )
    def test_oversized_response_is_rejected(self, operation: str) -> None:
        transport = UrllibVoiceStudioTransport(max_response_bytes=8)
        response = _FakeHttpResponse(b"x" * 9)

        with patch(
            "rex.speech.providers.voicestudio.urllib.request.urlopen",
            return_value=response,
        ):
            with pytest.raises(SpeechProviderResponseError, match="response exceeded"):
                if operation == "health":
                    transport.get_json(
                        "http://127.0.0.1:3900/health", headers={}, timeout=1
                    )
                elif operation == "transcription":
                    transport.post_multipart_for_json(
                        "http://127.0.0.1:3900/v1/audio/transcriptions",
                        {},
                        ("file", "audio.wav", b"audio", "audio/wav"),
                        headers={},
                        timeout=1,
                    )
                else:
                    transport.post_json_for_bytes(
                        "http://127.0.0.1:3900/v1/audio/speech",
                        {"input": "hello"},
                        headers={},
                        timeout=1,
                    )
