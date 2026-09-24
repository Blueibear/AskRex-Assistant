"""Capability scope allowlist and grant contracts for mobile pairing (S5/S6).

A pairing grant is the desktop-authorized set of capability scopes a single
paired mobile device may request.  Scopes are drawn from a fixed server-side
allowlist — a device can never widen its own authority, and unknown or
client-invented scopes are rejected outright.

Immutability rules (master contract §6):

- An approved grant's scope set is canonical (deduplicated, sorted, nonempty)
  and immutable.  Changing what a device may do requires issuing a new grant
  *version* (or a replacement grant), never mutating an existing one.
- A password-only bootstrap session is explicitly unpaired and carries **no**
  action scopes; it can never be action-capable.

This module is pure/stateless (no I/O), so it is safe to import anywhere and
to reuse from both the desktop authority and future S6 enforcement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

ALLOWED_SCOPES: frozenset[str] = frozenset(
    {
        "chat.send",
        "chat.history.read",
        "attachments.upload",
        "voice.use",
        "home.read",
        "home.control",
        "notifications.read",
        "tasks.read",
        "tasks.write",
        "settings.read",
        "settings.write",
        "approvals.respond",
    }
)

ACTION_SCOPES: frozenset[str] = frozenset(
    {
        "chat.send",
        "attachments.upload",
        "voice.use",
        "home.control",
        "tasks.write",
        "settings.write",
        "approvals.respond",
    }
)

_SCOPE_TOKEN = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9]+)*$")
_MAX_SCOPE_LENGTH = 64
_MAX_SCOPES = 32


class ScopeError(ValueError):
    """A requested scope set is malformed, unknown, or empty."""


def canonicalize_scopes(raw: object) -> tuple[str, ...]:
    """Return a canonical, allowlisted, deduplicated, sorted, nonempty scope tuple."""
    if not isinstance(raw, (list, tuple)):
        raise ScopeError("Scopes must be provided as a list of strings.")
    if len(raw) > _MAX_SCOPES:
        raise ScopeError("Too many scopes requested.")
    canonical: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ScopeError("Each scope must be a string.")
        token = item.strip().lower()
        if not token or len(token) > _MAX_SCOPE_LENGTH or not _SCOPE_TOKEN.fullmatch(token):
            raise ScopeError(f"Scope {item!r} is malformed.")
        if token not in ALLOWED_SCOPES:
            raise ScopeError(f"Scope {token!r} is not permitted.")
        canonical.add(token)
    if not canonical:
        raise ScopeError("At least one scope is required.")
    return tuple(sorted(canonical))


def scopes_are_canonical(scopes: object) -> bool:
    """Return True when *scopes* is already exactly a canonical scope tuple/list."""
    if not isinstance(scopes, (list, tuple)):
        return False
    try:
        return tuple(scopes) == canonicalize_scopes(scopes)
    except ScopeError:
        return False


def has_action_scope(scopes: object) -> bool:
    """Return True when the scope set authorizes at least one mutating action."""
    if not isinstance(scopes, (list, tuple)):
        return False
    return any(scope in ACTION_SCOPES for scope in scopes)


@dataclass(frozen=True)
class Grant:
    """An immutable, desktop-authorized capability grant for one paired device."""

    grant_id: str
    device_id: str
    desktop_id: str
    user_id: str
    version: int
    scopes: tuple[str, ...]
    created_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    revoke_reason: str | None = None

    def is_active(self, *, now: datetime) -> bool:
        """Return True when the grant is neither revoked nor expired at *now*."""
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None:
            try:
                expires = datetime.fromisoformat(self.expires_at)
            except ValueError:
                return False
            if now >= expires:
                return False
        return True

    def action_capable(self) -> bool:
        """Return True when this grant authorizes at least one mutating action."""
        return has_action_scope(self.scopes)


__all__ = [
    "ACTION_SCOPES",
    "ALLOWED_SCOPES",
    "Grant",
    "ScopeError",
    "canonicalize_scopes",
    "has_action_scope",
    "scopes_are_canonical",
]
