"""Server-side mobile device grant authorization (S6).

All capability scopes are loaded from the current SQLite device/grant rows.
Client metadata and JWT claims never supply authorization.  A partially
bound, revoked, expired, superseded, or malformed binding fails closed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

from rex.mobile_api.grants import ScopeError, canonicalize_scopes

ROUTE_SCOPES = MappingProxyType(
    {
        "chat.send": "chat.send",
        "chat.stream": "chat.send",
        "chat.websocket": "chat.send",
        "attachments.upload": "attachments.upload",
        "voice.upload": "voice.use",
        "tts.playback": "voice.use",
        "home.read": "home.read",
        "home.control": "home.control",
        "tasks.read": "tasks.read",
        "tasks.write": "tasks.write",
        "settings.read": "settings.read",
        "settings.write": "settings.write",
        "approvals.respond": "approvals.respond",
    }
)

SCOPE_USER_PERMISSIONS = MappingProxyType(
    {
        "home.read": frozenset({"ha_control", "admin"}),
        "home.control": frozenset({"ha_control", "admin"}),
        "approvals.respond": frozenset({"admin"}),
    }
)


class GrantAuthorizationError(ValueError):
    """The persisted device/grant binding is not currently authorized."""


@dataclass(frozen=True)
class ActiveGrant:
    device_id: str
    grant_id: str
    desktop_id: str
    user_id: str
    version: int
    scopes: tuple[str, ...]
    expires_at: str
    last_strong_auth_at: str | None
    public_key_b64: str


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise GrantAuthorizationError("Grant expiry is invalid.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise GrantAuthorizationError("Grant expiry is invalid.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _canonical_stored_scopes(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, str):
        raise GrantAuthorizationError("Grant scopes are invalid.")
    try:
        value = json.loads(raw)
        canonical = canonicalize_scopes(value)
    except (ValueError, TypeError, ScopeError) as exc:
        raise GrantAuthorizationError("Grant scopes are invalid.") from exc
    if list(canonical) != value:
        raise GrantAuthorizationError("Grant scopes are not canonical.")
    return canonical


def load_active_grant(
    conn: sqlite3.Connection,
    *,
    device_id: str,
    grant_id: str,
    expected_user_id: str,
    now: datetime,
    expected_desktop_id: str | None = None,
    expected_version: int | None = None,
) -> ActiveGrant:
    """Load and validate one approved device/grant using server-side state."""
    row = conn.execute(
        """SELECT
               d.device_id, d.desktop_id AS device_desktop_id,
               d.user_id AS device_user_id, d.public_key_b64,
               d.revoked_at AS device_revoked_at,
               g.grant_id, g.desktop_id AS grant_desktop_id,
               g.user_id AS grant_user_id, g.version, g.scopes_json,
               g.expires_at, g.last_strong_auth_at,
               g.revoked_at AS grant_revoked_at
           FROM mobile_paired_devices d
           JOIN mobile_device_grants g ON g.device_id = d.device_id
           WHERE d.device_id = ? AND g.grant_id = ?""",
        (device_id, grant_id),
    ).fetchone()
    if row is None:
        raise GrantAuthorizationError("Device grant is invalid.")
    if row["device_revoked_at"] is not None or row["grant_revoked_at"] is not None:
        raise GrantAuthorizationError("Device grant has been revoked.")
    if row["device_user_id"] != expected_user_id or row["grant_user_id"] != expected_user_id:
        raise GrantAuthorizationError("Device grant user binding is invalid.")
    desktop_id = str(row["device_desktop_id"])
    if row["grant_desktop_id"] != desktop_id:
        raise GrantAuthorizationError("Device grant desktop binding is invalid.")
    if expected_desktop_id is not None and desktop_id != expected_desktop_id:
        raise GrantAuthorizationError("Device grant desktop binding is invalid.")
    version = int(row["version"])
    if expected_version is not None and version != expected_version:
        raise GrantAuthorizationError("Device grant version is invalid.")
    latest = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM mobile_device_grants WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    if latest is None or int(latest["version"]) != version:
        raise GrantAuthorizationError("Device grant has been superseded.")
    if now >= _parse_utc(row["expires_at"]):
        raise GrantAuthorizationError("Device grant has expired.")
    scopes = _canonical_stored_scopes(row["scopes_json"])
    public_key = row["public_key_b64"]
    if not isinstance(public_key, str) or not public_key:
        raise GrantAuthorizationError("Device public key is invalid.")
    return ActiveGrant(
        device_id=str(row["device_id"]),
        grant_id=str(row["grant_id"]),
        desktop_id=desktop_id,
        user_id=expected_user_id,
        version=version,
        scopes=scopes,
        expires_at=str(row["expires_at"]),
        last_strong_auth_at=(
            str(row["last_strong_auth_at"]) if row["last_strong_auth_at"] else None
        ),
        public_key_b64=public_key,
    )


def resolve_session_grant(
    conn: sqlite3.Connection, session: sqlite3.Row, *, now: datetime
) -> ActiveGrant | None:
    """Return the active grant for a fully bound session, or None for bootstrap."""
    fields = (
        session["paired_device_id"],
        session["grant_id"],
        session["grant_version"],
        session["desktop_id"],
    )
    if all(value is None for value in fields):
        return None
    if any(value is None for value in fields):
        raise GrantAuthorizationError("Session grant binding is incomplete.")
    return load_active_grant(
        conn,
        device_id=str(session["paired_device_id"]),
        grant_id=str(session["grant_id"]),
        expected_user_id=str(session["user_id"]),
        expected_desktop_id=str(session["desktop_id"]),
        expected_version=int(session["grant_version"]),
        now=now,
    )


def require_scope(
    scopes: frozenset[str] | set[str],
    required_scope: str,
    *,
    permissions: frozenset[str] | set[str] = frozenset(),
) -> None:
    if required_scope not in scopes:
        raise GrantAuthorizationError("Required device capability is not granted.")
    required_permissions = SCOPE_USER_PERMISSIONS.get(required_scope, frozenset())
    if required_permissions and not required_permissions.intersection(permissions):
        raise GrantAuthorizationError("Required user permission is not granted.")


__all__ = [
    "ActiveGrant",
    "GrantAuthorizationError",
    "ROUTE_SCOPES",
    "SCOPE_USER_PERMISSIONS",
    "load_active_grant",
    "require_scope",
    "resolve_session_grant",
]
