"""Tests for the IMAP/SMTP email backend.

All tests use fakes/mocks — no real network calls.
"""

from __future__ import annotations

import imaplib
import smtplib

import pytest

from rex.email_backends.imap_smtp import ImapSmtpEmailBackend
from rex.email_backends.stub import StubEmailBackend
from rex.integrations.email.backends.base import EmailBackend as IntegrationEmailBackend
from rex.integrations.email.backends.imap_smtp import (
    EmailAuthError,
    IMAPBackend,
    IMAPSMTPBackend,
    SMTPSendError,
)
from tests.helpers.fake_imap import DEFAULT_RAW_HEADERS, FakeIMAP4SSL
from tests.helpers.fake_smtp import FakeSMTP

# ---------------------------------------------------------------------------
# Stub backend tests
# ---------------------------------------------------------------------------


class TestStubEmailBackend:
    """Tests for the StubEmailBackend."""

    def test_connect_missing_fixture(self, tmp_path):
        backend = StubEmailBackend(fixture_path=tmp_path / "missing.json")
        assert backend.connect() is True
        assert backend.is_connected is True
        assert backend.fetch_unread() == []

    def test_connect_loads_fixture(self, tmp_path):
        import json

        fixture = tmp_path / "emails.json"
        fixture.write_text(
            json.dumps(
                [
                    {
                        "id": "e1",
                        "from_addr": "a@b.com",
                        "subject": "Hello",
                        "snippet": "Hi there",
                        "received_at": "2026-01-01T10:00:00",
                        "labels": ["unread"],
                    }
                ]
            ),
            encoding="utf-8",
        )

        backend = StubEmailBackend(fixture_path=fixture)
        assert backend.connect() is True
        unread = backend.fetch_unread()
        assert len(unread) == 1
        assert unread[0].message_id == "e1"
        assert unread[0].from_addr == "a@b.com"

    def test_fetch_unread_limit(self, tmp_path):
        import json

        fixture = tmp_path / "emails.json"
        fixture.write_text(
            json.dumps(
                [
                    {
                        "id": f"e{i}",
                        "from_addr": "a@b.com",
                        "subject": f"Msg {i}",
                        "snippet": "",
                        "received_at": "2026-01-01T10:00:00",
                        "labels": ["unread"],
                    }
                    for i in range(5)
                ]
            ),
            encoding="utf-8",
        )

        backend = StubEmailBackend(fixture_path=fixture)
        backend.connect()
        assert len(backend.fetch_unread(limit=2)) == 2

    def test_send_stub(self, tmp_path):
        backend = StubEmailBackend(fixture_path=tmp_path / "none.json")
        result = backend.send(
            from_addr="me@x.com",
            to_addrs=["you@y.com"],
            subject="Test",
            body="Hello",
        )
        assert result.ok is True
        assert result.message_id == "stub-msg-id"
        assert len(backend.sent_messages) == 1

    def test_mark_as_read(self, tmp_path):
        import json

        fixture = tmp_path / "emails.json"
        fixture.write_text(
            json.dumps(
                [
                    {
                        "id": "e1",
                        "from_addr": "a@b.com",
                        "subject": "S",
                        "snippet": "",
                        "received_at": "2026-01-01T10:00:00",
                        "labels": ["unread"],
                    }
                ]
            ),
            encoding="utf-8",
        )
        backend = StubEmailBackend(fixture_path=fixture)
        backend.connect()
        assert backend.mark_as_read("e1") is True
        assert backend.fetch_unread() == []
        assert backend.mark_as_read("nonexistent") is False

    def test_list_mailboxes(self, tmp_path):
        backend = StubEmailBackend(fixture_path=tmp_path / "none.json")
        assert backend.list_mailboxes() == ["INBOX"]

    def test_disconnect(self, tmp_path):
        backend = StubEmailBackend(fixture_path=tmp_path / "none.json")
        backend.connect()
        assert backend.is_connected is True
        backend.disconnect()
        assert backend.is_connected is False


# ---------------------------------------------------------------------------
# ImapSmtp backend tests (all mocked)
# ---------------------------------------------------------------------------


class TestImapSmtpBackend:
    """Tests for the ImapSmtpEmailBackend with mocked IMAP/SMTP."""

    def _make_backend(self, imap_mock=None, smtp_mock=None):
        return ImapSmtpEmailBackend(
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=587,
            username="user@example.com",
            password="secret",  # pragma: allowlist secret -- fake credential fixture
            use_starttls=True,
            imap_client_factory=imap_mock,
            smtp_client_factory=smtp_mock,
        )

    def test_connect_success(self):
        mock_imap = FakeIMAP4SSL()

        backend = self._make_backend(imap_mock=lambda: mock_imap)
        assert backend.connect() is True
        assert backend.is_connected is True
        assert mock_imap.login_calls == [("user@example.com", "secret")]

    def test_connect_auth_failure(self):
        mock_imap = FakeIMAP4SSL(login_raises=imaplib.IMAP4.error("auth failed"))

        backend = self._make_backend(imap_mock=lambda: mock_imap)
        assert backend.connect() is False
        assert backend.is_connected is False

    def test_connect_network_failure(self):
        def factory():
            raise OSError("Connection refused")

        backend = self._make_backend(imap_mock=factory)
        assert backend.connect() is False

    def test_fetch_unread_not_connected(self):
        backend = self._make_backend()
        assert backend.fetch_unread() == []

    def test_fetch_unread_empty(self):
        mock_imap = FakeIMAP4SSL(search_result=("OK", [b""]))

        backend = self._make_backend(imap_mock=lambda: mock_imap)
        backend.connect()
        assert backend.fetch_unread() == []

    def test_fetch_unread_parses_headers(self):
        raw_headers = (
            b"From: sender@example.com\r\n"
            b"To: me@example.com\r\n"
            b"Subject: Test Subject\r\n"
            b"Date: Mon, 20 Feb 2026 10:00:00 +0000\r\n"
            b"Message-ID: <msg-001@example.com>\r\n"
            b"\r\n"
        )
        mock_imap = FakeIMAP4SSL(
            search_result=("OK", [b"1"]),
            fetch_result=("OK", [(b"1 (RFC822.HEADER {200}", raw_headers)]),
        )

        backend = self._make_backend(imap_mock=lambda: mock_imap)
        backend.connect()

        envelopes = backend.fetch_unread(limit=10)
        assert len(envelopes) == 1
        env = envelopes[0]
        assert env.from_addr == "sender@example.com"
        assert env.subject == "Test Subject"
        assert "unread" in env.labels

    def test_send_success(self):
        mock_smtp = FakeSMTP()

        backend = self._make_backend(smtp_mock=lambda: mock_smtp)
        result = backend.send(
            from_addr="user@example.com",
            to_addrs=["recipient@example.com"],
            subject="Test Email",
            body="Hello, World!",
        )
        assert result.ok is True
        assert mock_smtp.login_calls == [("user@example.com", "secret")]
        assert len(mock_smtp.send_message_calls) == 1

    def test_send_auth_failure(self):
        mock_smtp = FakeSMTP(login_raises=smtplib.SMTPAuthenticationError(535, b"bad creds"))

        backend = self._make_backend(smtp_mock=lambda: mock_smtp)
        result = backend.send(
            from_addr="user@example.com",
            to_addrs=["recipient@example.com"],
            subject="Fail",
            body="Oops",
        )
        assert result.ok is False
        assert "authentication" in (result.error or "").lower()

    def test_send_smtp_error(self):
        mock_smtp = FakeSMTP(send_raises=smtplib.SMTPException("relay denied"))

        backend = self._make_backend(smtp_mock=lambda: mock_smtp)
        result = backend.send(
            from_addr="user@example.com",
            to_addrs=["recipient@example.com"],
            subject="Fail",
            body="Oops",
        )
        assert result.ok is False
        assert result.error is not None

    def test_list_mailboxes(self):
        mock_imap = FakeIMAP4SSL(
            list_result=(
                "OK",
                [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Sent"'],
            ),
        )

        backend = self._make_backend(imap_mock=lambda: mock_imap)
        backend.connect()
        boxes = backend.list_mailboxes()
        assert "INBOX" in boxes
        assert "Sent" in boxes

    def test_mark_as_read(self):
        mock_imap = FakeIMAP4SSL(search_result=("OK", [b"42"]))

        backend = self._make_backend(imap_mock=lambda: mock_imap)
        backend.connect()
        assert backend.mark_as_read("<msg-001@example.com>") is True
        assert mock_imap.store_calls == [(b"42", "+FLAGS", "\\Seen")]

    def test_disconnect(self):
        mock_imap = FakeIMAP4SSL()

        backend = self._make_backend(imap_mock=lambda: mock_imap)
        backend.connect()
        assert backend.is_connected is True
        backend.disconnect()
        assert backend.is_connected is False


# ---------------------------------------------------------------------------
# EmailBackend interface conformance
# ---------------------------------------------------------------------------


class TestBackendInterface:
    """Verify both backends implement the full EmailBackend interface."""

    @pytest.mark.parametrize(
        "method",
        ["connect", "fetch_unread", "list_mailboxes", "mark_as_read", "send", "disconnect"],
    )
    def test_stub_has_method(self, method):
        backend = StubEmailBackend()
        assert hasattr(backend, method)

    @pytest.mark.parametrize(
        "method",
        ["connect", "fetch_unread", "list_mailboxes", "mark_as_read", "send", "disconnect"],
    )
    def test_imap_smtp_has_method(self, method):
        backend = ImapSmtpEmailBackend(imap_host="x", smtp_host="x", username="x", password="x")
        assert hasattr(backend, method)


# ---------------------------------------------------------------------------
# IMAPBackend (new transport-layer interface) — US-206
# ---------------------------------------------------------------------------

# Use DEFAULT_RAW_HEADERS from FakeIMAP4SSL helper so tests share the same fixture.
_RAW_HEADER_BYTES = DEFAULT_RAW_HEADERS


def _make_imap_fake(
    search_result=("OK", [b"1"]),
    fetch_result=None,
    login_raises=None,
):
    """Return a FakeIMAP4SSL configured for common test scenarios."""
    return FakeIMAP4SSL(
        search_result=search_result,
        fetch_result=fetch_result or ("OK", [(b"1 (RFC822.HEADER {350})", DEFAULT_RAW_HEADERS)]),
        login_raises=login_raises,
    )


# Keep _make_imap_mock as an alias so existing TestImapSmtpBackend callers still work.
def _make_imap_mock(
    search_result=("OK", [b"1"]),
    fetch_result=None,
    login_raises=None,
):
    return _make_imap_fake(
        search_result=search_result,
        fetch_result=fetch_result,
        login_raises=login_raises,
    )


def _make_imap_backend(fake_conn):
    return IMAPBackend(
        host="imap.example.com",
        port=993,
        username="user@example.com",
        password="secret",  # pragma: allowlist secret -- fake credential fixture
        imap_factory=lambda: fake_conn,
    )


class TestIMAPBackend:
    """Tests for the new transport-layer IMAPBackend (US-206)."""

    def test_is_subclass_of_integration_email_backend(self):
        assert issubclass(IMAPBackend, IntegrationEmailBackend)

    def test_email_auth_error_is_exception_subclass(self):
        assert issubclass(EmailAuthError, Exception)

    # --- happy path ---

    def test_fetch_unread_returns_list_of_dicts(self):
        mock_conn = _make_imap_mock()
        backend = _make_imap_backend(mock_conn)
        results = backend.fetch_unread(limit=5)
        assert isinstance(results, list)
        assert len(results) == 1
        assert isinstance(results[0], dict)

    def test_fetch_unread_dict_has_required_keys(self):
        mock_conn = _make_imap_mock()
        backend = _make_imap_backend(mock_conn)
        msg = backend.fetch_unread()[0]
        for key in ("id", "from", "subject", "snippet", "received_at"):
            assert key in msg, f"Missing key: {key}"

    def test_fetch_unread_parses_from_and_subject(self):
        mock_conn = _make_imap_mock()
        backend = _make_imap_backend(mock_conn)
        msg = backend.fetch_unread()[0]
        assert "alice@example.com" in msg["from"]
        assert "Hello from Alice" in msg["subject"]

    def test_fetch_unread_returns_empty_when_no_unseen(self):
        mock_conn = _make_imap_mock(search_result=("OK", [b""]))
        backend = _make_imap_backend(mock_conn)
        assert backend.fetch_unread() == []

    def test_fetch_unread_respects_limit(self):
        mock_conn = _make_imap_mock(search_result=("OK", [b"1 2"]))
        backend = _make_imap_backend(mock_conn)
        assert len(backend.fetch_unread(limit=1)) == 1

    def test_fetch_unread_closes_connection_after_success(self):
        mock_conn = _make_imap_mock()
        backend = _make_imap_backend(mock_conn)
        backend.fetch_unread()
        assert mock_conn.close_calls == 1
        assert mock_conn.logout_calls == 1

    # --- auth failure ---

    def test_auth_failure_raises_email_auth_error(self):
        mock_conn = _make_imap_mock(login_raises=imaplib.IMAP4.error("LOGIN failed"))
        backend = _make_imap_backend(mock_conn)
        with pytest.raises(EmailAuthError):
            backend.fetch_unread()

    def test_auth_error_message_describes_failure(self):
        mock_conn = _make_imap_mock(login_raises=imaplib.IMAP4.error("LOGIN failed"))
        backend = _make_imap_backend(mock_conn)
        with pytest.raises(EmailAuthError) as exc_info:
            backend.fetch_unread()
        assert "authentication failed" in str(exc_info.value).lower()

    def test_auth_error_includes_username_not_password(self):
        mock_conn = _make_imap_mock(login_raises=imaplib.IMAP4.error("LOGIN failed"))
        backend = _make_imap_backend(mock_conn)
        with pytest.raises(EmailAuthError) as exc_info:
            backend.fetch_unread()
        msg = str(exc_info.value)
        assert "user@example.com" in msg
        assert "secret" not in msg

    def test_send_raises_not_implemented(self):
        mock_conn = _make_imap_mock()
        backend = _make_imap_backend(mock_conn)
        with pytest.raises(NotImplementedError):
            backend.send(to="x@example.com", subject="S", body="B")


# ---------------------------------------------------------------------------
# IMAPSMTPBackend — US-207
# ---------------------------------------------------------------------------


def _make_smtp_fake(
    login_raises=None,
    send_raises=None,
    tls_raises=None,
):
    """Return a FakeSMTP configured for common test scenarios.

    ``tls_raises`` is not directly supported by FakeSMTP (starttls is not the
    failure point there); for TLS-level failures the factory itself should raise.
    """
    return FakeSMTP(
        login_raises=login_raises,
        send_raises=send_raises,
    )


# Keep _make_smtp_mock as an alias for TestIMAPSMTPBackend callers.
def _make_smtp_mock(
    login_raises=None,
    send_raises=None,
    tls_raises=None,
    timeout_on_connect=False,
):
    return _make_smtp_fake(login_raises=login_raises, send_raises=send_raises)


def _make_smtp_backend(smtp_fake, timeout_on_connect=False):
    if timeout_on_connect:
        factory = None  # will be unused; we'll patch _create_smtp_connection
    else:
        factory = lambda: smtp_fake  # noqa: E731
    return IMAPSMTPBackend(
        host="imap.example.com",
        port=993,
        username="user@example.com",
        password="secret",  # pragma: allowlist secret -- fake credential fixture
        smtp_host="smtp.example.com",
        smtp_port=587,
        use_starttls=True,
        imap_factory=lambda: _make_imap_fake(),
        smtp_factory=factory,
    )


class TestIMAPSMTPBackend:
    """Tests for IMAPSMTPBackend.send() — US-207."""

    def test_is_subclass_of_imap_backend(self):
        assert issubclass(IMAPSMTPBackend, IMAPBackend)

    def test_smtp_send_error_is_exception(self):
        assert issubclass(SMTPSendError, Exception)

    # --- successful send ---

    def test_send_success_returns_none(self):
        smtp_mock = _make_smtp_mock()
        backend = _make_smtp_backend(smtp_mock)
        result = backend.send(to="bob@example.com", subject="Hello", body="World")
        assert result is None

    def test_send_calls_smtp_login_with_username(self):
        smtp_mock = _make_smtp_mock()
        backend = _make_smtp_backend(smtp_mock)
        backend.send(to="bob@example.com", subject="S", body="B")
        assert smtp_mock.login_calls == [("user@example.com", "secret")]

    def test_send_calls_send_message(self):
        smtp_mock = _make_smtp_mock()
        backend = _make_smtp_backend(smtp_mock)
        backend.send(to="bob@example.com", subject="Hello", body="World")
        assert len(smtp_mock.send_message_calls) == 1

    def test_send_calls_quit_on_success(self):
        smtp_mock = _make_smtp_mock()
        backend = _make_smtp_backend(smtp_mock)
        backend.send(to="bob@example.com", subject="S", body="B")
        assert smtp_mock.quit_calls == 1

    def test_send_never_logs_password(self, caplog):
        import logging as _logging

        smtp_mock = _make_smtp_mock()
        backend = _make_smtp_backend(smtp_mock)
        with caplog.at_level(_logging.DEBUG):
            backend.send(to="bob@example.com", subject="S", body="B")
        assert "secret" not in caplog.text

    # --- auth failure ---

    def test_send_smtp_auth_failure_raises_email_auth_error(self):
        smtp_mock = _make_smtp_mock(
            login_raises=smtplib.SMTPAuthenticationError(535, b"bad credentials")
        )
        backend = _make_smtp_backend(smtp_mock)
        with pytest.raises(EmailAuthError):
            backend.send(to="bob@example.com", subject="S", body="B")

    def test_auth_error_message_includes_username_not_password(self):
        smtp_mock = _make_smtp_mock(
            login_raises=smtplib.SMTPAuthenticationError(535, b"bad credentials")
        )
        backend = _make_smtp_backend(smtp_mock)
        with pytest.raises(EmailAuthError) as exc_info:
            backend.send(to="bob@example.com", subject="S", body="B")
        msg = str(exc_info.value)
        assert "user@example.com" in msg
        assert "secret" not in msg

    # --- TLS failure ---

    def test_send_tls_failure_raises_smtp_send_error(self):
        import ssl as _ssl

        def failing_factory():
            raise _ssl.SSLError("TLS handshake failed")

        backend = IMAPSMTPBackend(
            host="imap.example.com",
            port=993,
            username="user@example.com",
            password="secret",
            smtp_host="smtp.example.com",
            smtp_port=587,
            use_starttls=True,
            imap_factory=lambda: _make_imap_mock(),
            smtp_factory=failing_factory,
        )
        with pytest.raises(SMTPSendError):
            backend.send(to="bob@example.com", subject="S", body="B")

    # --- timeout ---

    def test_send_timeout_raises_smtp_send_error(self):
        smtp_mock = _make_smtp_mock(send_raises=TimeoutError("connection timed out"))
        backend = _make_smtp_backend(smtp_mock)
        with pytest.raises(SMTPSendError):
            backend.send(to="bob@example.com", subject="S", body="B")

    # --- credential_ref ---

    def test_send_uses_credential_ref_password(self, monkeypatch):
        """When credential_ref is set and resolves a token, it's used for login."""
        smtp_mock = _make_smtp_mock()

        class FakeMgr:
            def get_token(self, ref):
                return "resolved-password"

        import rex.integrations.email.backends.imap_smtp as _mod

        monkeypatch.setattr(_mod, "CredentialManager", FakeMgr)

        backend = IMAPSMTPBackend(
            host="imap.example.com",
            port=993,
            username="user@example.com",
            password="fallback",  # pragma: allowlist secret
            smtp_host="smtp.example.com",
            smtp_port=587,
            credential_ref="email",
            imap_factory=lambda: _make_imap_mock(),
            smtp_factory=lambda: smtp_mock,
        )
        backend.send(to="bob@example.com", subject="S", body="B")
        assert smtp_mock.login_calls == [("user@example.com", "resolved-password")]
