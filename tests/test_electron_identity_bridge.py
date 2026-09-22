"""Regression coverage for Electron active-user restoration on relaunch."""

from __future__ import annotations

import pytest

from bridge import rex_identity_bridge


def test_completed_setup_relaunch_restores_only_discoverable_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: list[str] = []
    monkeypatch.setattr(rex_identity_bridge, "_discover_desktop_user_ids", lambda: {"james"})
    monkeypatch.setattr("rex.identity.resolve_active_user", lambda **_kwargs: None)
    monkeypatch.setattr("rex.identity.set_session_user", persisted.append)

    assert rex_identity_bridge.resolve_electron_session_user() == "james"
    assert persisted == ["james"]


def test_valid_persisted_user_wins_over_other_discoverable_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rex_identity_bridge, "_discover_desktop_user_ids", lambda: {"james", "cole"}
    )
    monkeypatch.setattr("rex.identity.resolve_active_user", lambda **_kwargs: "james")

    assert rex_identity_bridge.resolve_electron_session_user() == "james"


def test_stale_identity_recovers_when_exactly_one_user_is_discoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: list[str] = []
    monkeypatch.setattr(rex_identity_bridge, "_discover_desktop_user_ids", lambda: {"james"})
    monkeypatch.setattr("rex.identity.resolve_active_user", lambda **_kwargs: "removed-user")
    monkeypatch.setattr("rex.identity.set_session_user", persisted.append)

    assert rex_identity_bridge.resolve_electron_session_user() == "james"
    assert persisted == ["james"]


@pytest.mark.parametrize(
    ("configured_user", "known_users"),
    [(None, set()), ("removed-user", {"james", "cole"})],
)
def test_missing_or_ambiguous_identity_fails_without_selecting_a_user(
    monkeypatch: pytest.MonkeyPatch,
    configured_user: str | None,
    known_users: set[str],
) -> None:
    monkeypatch.setattr(rex_identity_bridge, "_discover_desktop_user_ids", lambda: known_users)
    monkeypatch.setattr(
        "rex.identity.resolve_active_user", lambda **_kwargs: configured_user
    )

    with pytest.raises(PermissionError):
        rex_identity_bridge.resolve_electron_session_user()
