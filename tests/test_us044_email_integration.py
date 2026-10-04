"""US-044: Email integration acceptance tests.

Acceptance criteria:
- email backend connects
- send works
- errors handled
- Typecheck passes
"""

from __future__ import annotations

import imaplib
import json
import smtplib
from pathlib import Path
from unittest.mock import MagicMock

from rex.email_backends.base import EmailEnvelope, SendResult
from rex.email_backends.imap_smtp import ImapSmtpEmailBackend
from rex.email_backends.stub import StubEmailBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_imap_mock() -> MagicMock:
    imap = MagicMock(spec=imaplib.IMAP4_SSL)
    imap.login.return_value = ("OK", [b"Logged in"])
    imap.select.return_value = ("OK", [b"1"])
    imap.search.return_value = ("OK", [b""])
    imap.list.return_value = ("OK", [b'"/" "INBOX"'])
    return imap


def _make_smtp_mock() -> MagicMock:
    smtp = MagicMock(spec=smtplib.SMTP)
    smtp.login.return_value = (235, b"Authentication successful")
    smtp.send_message.return_value = {}
    smtp.quit.return_value = None
    return smtp


def _make_imap_backend(imap_mock: MagicMock | None = None) -> ImapSmtpEmailBackend:
    imap_mock = imap_mock or _make_imap_mock()
    return ImapSmtpEmailBackend(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="secret",  # pragma: allowlist secret -- fixture
        imap_client_factory=lambda: imap_mock,
        smtp_client_factory=lambda: _make_smtp_mock(),
    )


def _make_stub_backend(tmp_path: Path) -> StubEmailBackend:
    fixture = tmp_path / "emails.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "id": "msg-1",
                    "from_addr": "alice@example.com",
                    "subject": "Hello",
                    "snippet": "Hi there",
                    "received_at": "2026-01-01T10:00:00Z",
                    "labels": ["unread"],
                }
            ]
        ),
        encoding="utf-8",
    )
    return StubEmailBackend(fixture_path=fixture)


# ---------------------------------------------------------------------------
# AC1: email backend connects
# ---------------------------------------------------------------------------


def test_stub_backend_connect(tmp_path: Path) -> None:
    backend = _make_stub_backend(tmp_path)
    result = backend.connect()
    assert result is True
    assert backend.is_connected is True


def test_imap_backend_connect() -> None:
    imap_mock = _make_imap_mock()
    backend = _make_imap_backend(imap_mock)
    result = backend.connect()
    assert result is True
    assert backend.is_connected is True
    imap_mock.login.assert_called_once_with("user@example.com", "secret")
    imap_mock.select.assert_called_once_with("INBOX")


def test_stub_backend_fetch_unread_after_connect(tmp_path: Path) -> None:
    backend = _make_stub_backend(tmp_path)
    backend.connect()
    unread = backend.fetch_unread()
    assert len(unread) == 1
    assert isinstance(unread[0], EmailEnvelope)
    assert unread[0].from_addr == "alice@example.com"


def test_stub_backend_list_mailboxes(tmp_path: Path) -> None:
    backend = _make_stub_backend(tmp_path)
    backend.connect()
    boxes = backend.list_mailboxes()
    assert "INBOX" in boxes


def test_imap_backend_list_mailboxes() -> None:
    backend = _make_imap_backend()
    backend.connect()
    boxes = backend.list_mailboxes()
    assert isinstance(boxes, list)


# ---------------------------------------------------------------------------
# AC2: send works
# ---------------------------------------------------------------------------


def test_stub_backend_send(tmp_path: Path) -> None:
    backend = _make_stub_backend(tmp_path)
    backend.connect()
    result = backend.send(
        from_addr="me@example.com",
        to_addrs=["you@example.com"],
        subject="Test",
        body="Hello world",
    )
    assert isinstance(result, SendResult)
    assert result.ok is True
    assert result.message_id is not None


def test_stub_backend_send_captured(tmp_path: Path) -> None:
    backend = _make_stub_backend(tmp_path)
    backend.connect()
    backend.send(
        from_addr="me@example.com",
        to_addrs=["you@example.com"],
        subject="Subject",
        body="Body text",
    )
    sent = backend.sent_messages
    assert len(sent) == 1
    assert sent[0]["subject"] == "Subject"
    assert sent[0]["body"] == "Body text"


def test_imap_backend_send() -> None:
    smtp_mock = _make_smtp_mock()
    backend = ImapSmtpEmailBackend(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="secret",  # pragma: allowlist secret -- fake credential fixture
        imap_client_factory=lambda: _make_imap_mock(),
        smtp_client_factory=lambda: smtp_mock,
    )
    backend.connect()
    result = backend.send(
        from_addr="user@example.com",
        to_addrs=["recipient@example.com"],
        subject="Hello",
        body="Test body",
    )
    assert isinstance(result, SendResult)
    assert result.ok is True
    smtp_mock.login.assert_called_once()
    smtp_mock.send_message.assert_called_once()


def test_imap_backend_send_with_reply_to() -> None:
    smtp_mock = _make_smtp_mock()
    backend = ImapSmtpEmailBackend(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="secret",  # pragma: allowlist secret -- fake credential fixture
        imap_client_factory=lambda: _make_imap_mock(),
        smtp_client_factory=lambda: smtp_mock,
    )
    backend.connect()
    result = backend.send(
        from_addr="user@example.com",
        to_addrs=["recipient@example.com"],
        subject="Hello",
        body="Test body",
        reply_to="other@example.com",
    )
    assert result.ok is True


# ---------------------------------------------------------------------------
# AC3: errors handled
# ---------------------------------------------------------------------------


def test_stub_backend_missing_fixture_handled() -> None:
    backend = StubEmailBackend(fixture_path=Path("/nonexistent/emails.json"))
    result = backend.connect()
    # Missing fixture: connect succeeds but loads empty list
    assert result is True
    assert backend.is_connected is True
    assert backend.fetch_unread() == []


def test_imap_backend_connect_auth_failure() -> None:
    imap_mock = _make_imap_mock()
    imap_mock.login.side_effect = imaplib.IMAP4.error("Authentication failed")
    backend = _make_imap_backend(imap_mock)
    result = backend.connect()
    assert result is False
    assert backend.is_connected is False


def test_imap_backend_connect_network_failure() -> None:
    backend = ImapSmtpEmailBackend(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="secret",  # pragma: allowlist secret -- fake credential fixture
        imap_client_factory=lambda: (_ for _ in ()).throw(OSError("Connection refused")),
    )
    result = backend.connect()
    assert result is False
    assert backend.is_connected is False


def test_imap_backend_send_auth_failure() -> None:
    smtp_mock = _make_smtp_mock()
    smtp_mock.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")
    backend = ImapSmtpEmailBackend(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="secret",
        imap_client_factory=lambda: _make_imap_mock(),
        smtp_client_factory=lambda: smtp_mock,
    )
    backend.connect()
    result = backend.send(
        from_addr="user@example.com",
        to_addrs=["recipient@example.com"],
        subject="Test",
        body="Body",
    )
    assert result.ok is False
    assert result.error is not None
    assert "authentication" in result.error.lower()


def test_imap_backend_send_smtp_error() -> None:
    smtp_mock = _make_smtp_mock()
    smtp_mock.send_message.side_effect = smtplib.SMTPException("Server error")
    backend = ImapSmtpEmailBackend(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="secret",
        imap_client_factory=lambda: _make_imap_mock(),
        smtp_client_factory=lambda: smtp_mock,
    )
    backend.connect()
    result = backend.send(
        from_addr="user@example.com",
        to_addrs=["recipient@example.com"],
        subject="Test",
        body="Body",
    )
    assert result.ok is False
    assert result.error is not None


def test_fetch_unread_when_not_connected() -> None:
    backend = ImapSmtpEmailBackend(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="secret",
    )
    result = backend.fetch_unread()
    assert result == []


def test_disconnect_cleans_up() -> None:
    imap_mock = _make_imap_mock()
    backend = _make_imap_backend(imap_mock)
    backend.connect()
    assert backend.is_connected is True
    backend.disconnect()
    assert backend.is_connected is False


def test_stub_backend_mark_as_read(tmp_path: Path) -> None:
    backend = _make_stub_backend(tmp_path)
    backend.connect()
    unread_before = backend.fetch_unread()
    assert len(unread_before) == 1
    result = backend.mark_as_read("msg-1")
    assert result is True
    unread_after = backend.fetch_unread()
    assert len(unread_after) == 0


def test_stub_backend_mark_as_read_nonexistent(tmp_path: Path) -> None:
    backend = _make_stub_backend(tmp_path)
    backend.connect()
    result = backend.mark_as_read("nonexistent-id")
    assert result is False
