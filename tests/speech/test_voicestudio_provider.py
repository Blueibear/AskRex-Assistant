"""VoiceStudio provider client tests using an injected fake transport (S35).

No real network calls are made; the fake transport lets us exercise
availability, timeout, malformed-response, and truthful-failure behavior
deterministically.
"""

from __future__ import annotations

import io
import threading
import urllib.request
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import Mock, patch

import pytest

from rex.speech.contracts import (
    SpeechProviderResponseError,
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
)
from rex.speech.providers.voicestudio import (
    _NoRedirectHandler,
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


class _RedirectServers:
    """Two real loopback servers for exercising urllib redirect behavior."""

    def __init__(self) -> None:
        self.source_requests: list[tuple[str, dict[str, str], bytes]] = []
        self.target_requests: list[tuple[str, dict[str, str], bytes]] = []
        self._target = ThreadingHTTPServer(
            ("127.0.0.1", 0), self._target_handler()
        )
        self._source = ThreadingHTTPServer(
            ("127.0.0.1", 0), self._source_handler()
        )
        self._threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (self._target, self._source)
        ]

    @property
    def source_url(self) -> str:
        return f"http://127.0.0.1:{self._source.server_port}"

    @property
    def target_url(self) -> str:
        return f"http://127.0.0.1:{self._target.server_port}/stolen"

    def __enter__(self) -> "_RedirectServers":
        for thread in self._threads:
            thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        for server in (self._source, self._target):
            server.shutdown()
            server.server_close()
        for thread in self._threads:
            thread.join()

    @staticmethod
    def _request(handler: BaseHTTPRequestHandler) -> tuple[str, dict[str, str], bytes]:
        size = int(handler.headers.get("Content-Length", "0"))
        return handler.path, dict(handler.headers), handler.rfile.read(size)

    def _source_handler(self) -> type[BaseHTTPRequestHandler]:
        requests = self.source_requests
        target_url = self.target_url

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                requests.append(_RedirectServers._request(self))
                self.send_response(302)
                self.send_header("Location", target_url)
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                self.do_GET()

            def log_message(self, format: str, *args: Any) -> None:
                return None

        return Handler

    def _target_handler(self) -> type[BaseHTTPRequestHandler]:
        requests = self.target_requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                requests.append(_RedirectServers._request(self))
                self.send_response(200)
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                self.do_GET()

            def log_message(self, format: str, *args: Any) -> None:
                return None

        return Handler


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


class TestVoiceStudioConfigUrlSecurity:
    def test_remote_plaintext_url_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="remote base URLs must use https"):
            VoiceStudioConfig(base_url="http://speech.example.test:3900")

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://127.0.0.1:3900",
            "http://localhost:3900",
            "https://speech.example.test",
        ],
    )
    def test_loopback_http_and_remote_https_are_accepted(self, base_url: str) -> None:
        config = VoiceStudioConfig(base_url=base_url)
        assert config.base_url == base_url

    @pytest.mark.parametrize("base_url", ["ftp://127.0.0.1", "not-a-url", "https://"])
    def test_invalid_scheme_or_hostname_is_rejected(self, base_url: str) -> None:
        with pytest.raises(ValueError):
            VoiceStudioConfig(base_url=base_url)


class TestVoiceStudioSTTProvider:
    def test_is_local_reflects_base_url(self) -> None:
        local = VoiceStudioSTTProvider(VoiceStudioConfig(base_url="http://127.0.0.1:8020"))
        remote = VoiceStudioSTTProvider(
            VoiceStudioConfig(base_url="https://example.com")
        )
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

        with patch.object(transport._opener, "open", return_value=response):
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


class TestUrllibVoiceStudioTransportUrlSecurity:
    @pytest.mark.parametrize("operation", ["health", "transcription", "tts"])
    def test_remote_plaintext_url_is_rejected_before_transport(
        self, operation: str
    ) -> None:
        transport = UrllibVoiceStudioTransport()
        open_request = Mock()

        with patch.object(transport._opener, "open", open_request):
            with pytest.raises(SpeechProviderResponseError, match="TLS policy"):
                if operation == "health":
                    transport.get_json(
                        "http://speech.example.test/health", headers={}, timeout=1
                    )
                elif operation == "transcription":
                    transport.post_multipart_for_json(
                        "http://speech.example.test/v1/audio/transcriptions",
                        {},
                        ("file", "audio.wav", b"audio", "audio/wav"),
                        headers={},
                        timeout=1,
                    )
                else:
                    transport.post_json_for_bytes(
                        "http://speech.example.test/v1/audio/speech",
                        {"input": "private text"},
                        headers={},
                        timeout=1,
                    )

        open_request.assert_not_called()


class TestUrllibVoiceStudioTransportRedirectPolicy:
    """Redirects must not defeat Local Only's loopback-only provider policy."""

    def test_transport_installs_a_handler_that_refuses_redirects(self) -> None:
        transport = UrllibVoiceStudioTransport()
        handler = next(
            item
            for item in transport._opener.handlers
            if isinstance(item, _NoRedirectHandler)
        )

        assert (
            handler.redirect_request(
                urllib.request.Request("http://127.0.0.1:3900/health"),
                None,
                302,
                "Found",
                Message(),
                "https://remote.example.invalid/stolen",
            )
            is None
        )

    @pytest.mark.parametrize("operation", ["health", "transcription", "tts"])
    def test_redirect_is_rejected_without_contacting_remote_target(
        self, operation: str
    ) -> None:
        transport = UrllibVoiceStudioTransport()
        with _RedirectServers() as servers:
            with pytest.raises(SpeechProviderResponseError, match="redirects are not allowed"):
                if operation == "health":
                    transport.get_json(
                        servers.source_url + "/health",
                        headers={"Authorization": "Bearer secret"},
                        timeout=1,
                    )
                elif operation == "transcription":
                    transport.post_multipart_for_json(
                        servers.source_url + "/v1/audio/transcriptions",
                        {},
                        ("file", "audio.wav", b"private-audio", "audio/wav"),
                        headers={"Authorization": "Bearer secret"},
                        timeout=1,
                    )
                else:
                    transport.post_json_for_bytes(
                        servers.source_url + "/v1/audio/speech",
                        {"input": "private text"},
                        headers={"Authorization": "Bearer secret"},
                        timeout=1,
                    )

            # These are live HTTP servers, not an opener mock: source proves a
            # 30x was emitted and an empty target ledger proves urllib never
            # reissued the request or forwarded its Authorization/body.
            assert len(servers.source_requests) == 1
            assert servers.target_requests == []
