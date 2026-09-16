"""Shared fixtures for mobile API gateway tests.

All tests use temporary data directories, an injected controllable clock,
and deterministic ID generation.  No test touches the real ``data/`` or
``Memory/`` directories or leaves repository changes behind.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

TEST_JWT_SECRET = (
    "unit-test-jwt-secret-0123456789abcdef0123456789abcdef"  # pragma: allowlist secret
)


class FakeClock:
    """Controllable UTC clock.

    Starts at the real current time so PyJWT's real-time ``exp``/``nbf``
    validation agrees with tokens issued through the injected clock, then
    advances only when a test says so.
    """

    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float = 0, days: float = 0) -> None:
        self.current += timedelta(seconds=seconds, days=days)


def sequential_token_generator(prefix: str = "refresh") -> Callable[[], str]:
    """Deterministic high-length token generator for storage tests."""
    counter = itertools.count(1)

    def _generate() -> str:
        return f"{prefix}-token-{next(counter):04d}-" + "x" * 43

    return _generate


@pytest.fixture()
def mobile_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the canonical stores at a temp dir and set a strong JWT secret."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("REX_DATA_DIR", str(data_dir))
    monkeypatch.setenv("REX_JWT_SECRET", TEST_JWT_SECRET)
    return data_dir


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def mobile_config():
    from rex.config import MobileApiConfig

    return MobileApiConfig()


class RecordingAuditLogger:
    """In-memory stand-in for rex.audit.AuditLogger — writes no files."""

    def __init__(self) -> None:
        self.entries: list = []

    def log(self, entry) -> None:
        self.entries.append(entry)


@pytest.fixture()
def audit_recorder() -> RecordingAuditLogger:
    return RecordingAuditLogger()


# ---------------------------------------------------------------------------
# Fake runtime adapters (Session 2) — deterministic, no ML dependencies.
# ---------------------------------------------------------------------------


class FakeChatService:
    """Deterministic stand-in for MobileChatService.

    Replies embed the calling user's ID so cross-user isolation failures are
    visible, and every execution is recorded so idempotency tests can assert
    exactly-once behaviour.
    """

    def __init__(self) -> None:
        self.available = True
        self.calls: list[tuple[str, str]] = []
        self.fail_with: Exception | None = None
        self.stream_fail_after_chunks: int | None = None
        self.status_updates: list = []

    def availability(self) -> tuple[bool, str]:
        return (self.available, "ok" if self.available else "fake chat disabled")

    def _reply(self, message: str, user_id: str) -> str:
        return f"echo[{user_id}]: {message}"

    def generate(
        self,
        message: str,
        *,
        user_id: str,
        device_id: str | None = None,
        voice_mode: bool = False,
        capability_scopes=None,
        capability_permissions=None,
        authorization_check=None,
        strong_auth_authority=None,
        strong_auth_principal=None,
        strong_auth_approval_id=None,
        status_observer=None,
    ) -> str:
        if authorization_check is not None:
            authorization_check()
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append((message, user_id))
        if status_observer is not None:
            for update in self.status_updates:
                status_observer(update)
        return self._reply(message, user_id)

    def stream(
        self,
        message: str,
        *,
        user_id: str,
        device_id: str | None = None,
        capability_scopes=None,
        capability_permissions=None,
        authorization_check=None,
        strong_auth_authority=None,
        strong_auth_principal=None,
        strong_auth_approval_id=None,
        status_observer=None,
    ):
        if authorization_check is not None:
            authorization_check()
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append((message, user_id))
        if status_observer is not None:
            for update in self.status_updates:
                if not getattr(update, "terminal", False):
                    status_observer(update)
        text = self._reply(message, user_id)
        chunks = [text[i : i + 6] for i in range(0, len(text), 6)]
        for index, chunk in enumerate(chunks):
            if self.stream_fail_after_chunks is not None and index >= self.stream_fail_after_chunks:
                from rex.mobile_api import errors as merr
                from rex.mobile_api.errors import MobileApiError

                raise MobileApiError(
                    merr.BACKEND_UNAVAILABLE,
                    "Rex is temporarily unavailable.",
                    503,
                    retryable=True,
                )
            yield chunk
        if status_observer is not None:
            for update in self.status_updates:
                if getattr(update, "terminal", False):
                    status_observer(update)


class FakeSttAdapter:
    """Deterministic stand-in for SpeechToTextAdapter."""

    def __init__(self) -> None:
        self.available = True
        self.unavailable_reason = "fake STT disabled"
        self.transcript = "turn off the downstairs lights"
        self.decode_seconds = 2.0
        self.decoded_paths: list[str] = []
        self.decode_fails = False

    def availability(self) -> tuple[bool, str]:
        return (True, "ok") if self.available else (False, self.unavailable_reason)

    def require_available(self) -> None:
        if not self.available:
            from rex.mobile_api import errors as merr
            from rex.mobile_api.errors import MobileApiError

            raise MobileApiError(
                merr.BACKEND_UNAVAILABLE,
                "Speech-to-text is not available on this server.",
                503,
                retryable=False,
            )

    def decode(self, path: str):
        self.require_available()
        self.decoded_paths.append(path)
        if self.decode_fails:
            from rex.mobile_api import errors as merr
            from rex.mobile_api.errors import MobileApiError

            raise MobileApiError(merr.INVALID_MEDIA, "The audio could not be decoded.", 415)
        from rex.mobile_api.voice import WHISPER_SAMPLE_RATE

        return [0.0] * int(self.decode_seconds * WHISPER_SAMPLE_RATE)

    def transcribe(self, audio) -> str:
        self.require_available()
        return self.transcript


class FakeTtsAdapter:
    """Deterministic stand-in for TextToSpeechAdapter."""

    def __init__(self) -> None:
        self.available = True
        self.known_voices = ["fake-default-voice", "fake-alt-voice"]
        self.audio = b"FAKE-TTS-AUDIO"
        self.synthesized: list[tuple[str, str]] = []
        # Mirrors TextToSpeechAdapter.mime_type(), which follows the configured
        # provider ("audio/mpeg" for edge-tts, "audio/wav" otherwise).
        self.mime = "audio/wav"

    def availability(self) -> tuple[bool, str]:
        return (True, "ok") if self.available else (False, "fake TTS disabled")

    def require_available(self) -> None:
        if not self.available:
            from rex.mobile_api import errors as merr
            from rex.mobile_api.errors import MobileApiError

            raise MobileApiError(
                merr.BACKEND_UNAVAILABLE,
                "Text-to-speech is not available on this server.",
                503,
                retryable=False,
            )

    def mime_type(self) -> str:
        return self.mime

    def resolve_voice(self, requested: str | None) -> str:
        self.require_available()
        if requested is None or requested.strip() in ("", "default"):
            return self.known_voices[0]
        requested = requested.strip()
        if requested not in self.known_voices:
            from rex.mobile_api import errors as merr
            from rex.mobile_api.errors import MobileApiError

            raise MobileApiError(merr.BAD_REQUEST, "The requested voice is not available.", 400)
        return requested

    def synthesize(self, text: str, voice_id: str) -> bytes:
        self.require_available()
        self.synthesized.append((text, voice_id))
        return self.audio


@pytest.fixture()
def fake_chat_service() -> FakeChatService:
    return FakeChatService()


@pytest.fixture()
def fake_stt() -> FakeSttAdapter:
    return FakeSttAdapter()


@pytest.fixture()
def fake_tts() -> FakeTtsAdapter:
    return FakeTtsAdapter()


@pytest.fixture()
def services(
    mobile_env: Path,
    clock: FakeClock,
    mobile_config,
    audit_recorder: RecordingAuditLogger,
    fake_chat_service: FakeChatService,
    fake_stt: FakeSttAdapter,
    fake_tts: FakeTtsAdapter,
):
    from rex.mobile_api.db import migrate_users_db
    from rex.mobile_api.services import MobileApiServices

    db_path = mobile_env / "users.db"
    migrate_users_db(db_path)
    built = MobileApiServices.build(
        mobile_config,
        db_path=db_path,
        clock=clock,
        audit_logger=audit_recorder,
        chat_service=fake_chat_service,
        stt=fake_stt,
        tts=fake_tts,
    )
    from rex.mobile_api.pairing import PairingAuthority
    from rex.mobile_api.tls import TransportBinding

    binding = TransportBinding(
        server_url="https://rex.example.test:8765",
        certificate_fingerprint="a" * 64,
        spki_pins=("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",),
    )
    built.transport_binding = binding
    built.pairing_authority = PairingAuthority(
        db_path,
        clock=clock,
        transport_binding_provider=lambda: binding,
    )
    return built


@pytest.fixture()
def app(services):
    from rex.mobile_api.app import create_mobile_app

    application = create_mobile_app(services=services)
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def client(app):
    with app.test_client() as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def create_user(username: str, password: str, *, admin: bool = False) -> str:
    """Create a canonical user (env-scoped tmp db) and return its ID."""
    from rex.auth import create_user as _create
    from rex.permissions import grant_permission

    user = _create(username, password)
    if admin:
        grant_permission(user["id"], "admin")
    return str(user["id"])


def disable_user(db_path: Path, user_id: str) -> None:
    from rex.mobile_api.db import connect

    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE users SET disabled_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), user_id),
        )
    finally:
        conn.close()


def login(client, username: str, password: str, device: dict | None = None):
    payload: dict = {"username": username, "password": password}
    if device is not None:
        payload["device"] = device
    return client.post("/mobile/auth/login", json=payload)


def auth_header(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def paired_login_tokens(
    client,
    username: str,
    password: str,
    *,
    scopes: list[str] | None = None,
) -> dict:
    """Create a real S5 grant and upgrade a password bootstrap session.

    Tests use the same P-256 pairing and S6 activation transcripts as the
    production protocol.  The private key remains local to this helper.
    """
    from rex.mobile_api.db import connect
    from rex.mobile_api.device_proof import (
        canonical_session_transcript,
        canonical_transcript,
        generate_p256_private_key,
        public_key_spki_b64,
        sign_transcript,
    )

    services = client.application.extensions["mobile_api_services"]
    bootstrap = login_tokens(client, username, password)
    conn = connect(services.db_path)
    try:
        user = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()
    assert user is not None
    user_id = str(user["id"])
    requested_scopes = scopes or ["chat.send", "chat.history.read", "voice.use"]
    private_key = generate_p256_private_key()
    public_key = public_key_spki_b64(private_key)
    challenge = services.pairing_authority.create_challenge(
        user_id=user_id,
        scopes=requested_scopes,
    )
    transcript = canonical_transcript(
        desktop_id=challenge.desktop_id,
        challenge_id=challenge.challenge_id,
        nonce_b64=challenge.nonce_b64,
        mobile_public_key_b64=public_key,
        user_id=challenge.user_id,
        scopes=challenge.scopes,
        code=challenge.code,
        server_url=challenge.server_url,
        certificate_fingerprint=challenge.certificate_fingerprint,
        spki_pins=challenge.spki_pins,
    )
    submitted = services.pairing_authority.submit_proof(
        {
            "desktop_id": challenge.desktop_id,
            "challenge_id": challenge.challenge_id,
            "nonce": challenge.nonce_b64,
            "code": challenge.code,
            "user_id": challenge.user_id,
            "scopes": list(challenge.scopes),
            "public_key": public_key,
            "signature": sign_transcript(private_key, transcript),
            "device_name": "Test iPhone",
            "platform": "ios",
        }
    )
    grant = services.pairing_authority.approve(
        submitted.request_id,
        approved_by="TEST\\DesktopOwner",
    )
    challenge_response = client.post(
        "/mobile/auth/device-challenge",
        json={"device_id": grant.device_id, "grant_id": grant.grant_id},
        headers=auth_header(bootstrap["access_token"]),
    )
    assert challenge_response.status_code == 200, challenge_response.get_json()
    activation = challenge_response.get_json()
    activation_transcript = canonical_session_transcript(
        desktop_id=activation["desktop_id"],
        bootstrap_session_id=activation["bootstrap_session_id"],
        challenge_id=activation["challenge_id"],
        nonce_b64=activation["nonce"],
        device_id=activation["device_id"],
        grant_id=activation["grant_id"],
        grant_version=activation["grant_version"],
        user_id=activation["user_id"],
    )
    response = client.post(
        "/mobile/auth/activate-device",
        json={
            "challenge_id": activation["challenge_id"],
            "signature": sign_transcript(private_key, activation_transcript),
        },
        headers=auth_header(bootstrap["access_token"]),
    )
    assert response.status_code == 200, response.get_json()
    tokens = response.get_json()
    tokens["_paired_device_id"] = grant.device_id
    tokens["_grant_id"] = grant.grant_id
    tokens["_private_key"] = private_key
    tokens["_bootstrap"] = bootstrap
    return tokens


def login_tokens(client, username: str, password: str) -> dict:
    response = login(client, username, password)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def chat_payload(message: str = "Hello Rex", **overrides) -> dict:
    """Canonical valid chat request body with fresh UUIDs."""
    import uuid

    payload = {
        "message_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
        "sent_at": "2026-07-15T12:00:00+00:00",
        "message": message,
        "mode": "mobile_text",
        "client_context": {"device": "iphone", "response_preference": "brief"},
    }
    payload.update(overrides)
    return payload


def parse_sse_events(body: bytes) -> list[dict]:
    """Parse an SSE body into its JSON event payloads (strict)."""
    import json as _json

    events = []
    for line in body.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        assert line.startswith("data: "), f"non-SSE line in stream: {line!r}"
        events.append(_json.loads(line[len("data: ") :]))
    return events
