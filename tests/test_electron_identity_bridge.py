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


def test_missing_identity_with_no_known_users_fails_without_selecting_a_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rex_identity_bridge, "_discover_desktop_user_ids", lambda: set())
    monkeypatch.setattr("rex.identity.resolve_active_user", lambda **_kwargs: None)

    with pytest.raises(PermissionError) as excinfo:
        rex_identity_bridge.resolve_electron_session_user()
    assert not isinstance(excinfo.value, rex_identity_bridge.AmbiguousIdentityError)


@pytest.mark.parametrize("configured_user", [None, "removed-user"])
def test_missing_or_stale_identity_with_multiple_users_hands_off_to_selection(
    monkeypatch: pytest.MonkeyPatch,
    configured_user: str | None,
) -> None:
    monkeypatch.setattr(
        rex_identity_bridge, "_discover_desktop_user_ids", lambda: {"james", "cole"}
    )
    monkeypatch.setattr(
        "rex.identity.resolve_active_user", lambda **_kwargs: configured_user
    )

    with pytest.raises(rex_identity_bridge.AmbiguousIdentityError) as excinfo:
        rex_identity_bridge.resolve_electron_session_user()
    assert excinfo.value.known_user_ids == ["cole", "james"]


def test_select_electron_session_user_persists_a_known_discoverable_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: list[str] = []
    monkeypatch.setattr(
        rex_identity_bridge, "_discover_desktop_user_ids", lambda: {"james", "cole"}
    )
    monkeypatch.setattr("rex.identity.set_session_user", persisted.append)

    assert rex_identity_bridge.select_electron_session_user("cole") == "cole"
    assert persisted == ["cole"]


def test_select_electron_session_user_rejects_an_undiscoverable_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rex_identity_bridge, "_discover_desktop_user_ids", lambda: {"james"})
    monkeypatch.setattr(
        "rex.identity.set_session_user",
        lambda _user_id: pytest.fail("must not persist an undiscovered user"),
    )

    with pytest.raises(PermissionError):
        rex_identity_bridge.select_electron_session_user("cole")
