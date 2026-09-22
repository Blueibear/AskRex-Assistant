"""Resolve the immutable user identity for one Electron main-process session."""

from __future__ import annotations

import json
import sys

from rex.bridge_utils import bridge_error_response


def _discover_desktop_user_ids() -> set[str]:
    """Return IDs that can safely be restored by the local desktop app.

    Older installs keep profiles under ``Memory/`` while completed current
    setup creates the canonical auth record.  Both are local, authoritative
    sources for a desktop identity.  Do not select an arbitrary user when
    more than one exists.
    """
    from rex.auth import _open_db  # noqa: PLC2701
    from rex.identity import list_known_users, validate_user_id

    user_ids = {
        validate_user_id(str(user["id"]))
        for user in list_known_users()
        if isinstance(user, dict) and isinstance(user.get("id"), str)
    }
    with _open_db() as conn:
        rows = conn.execute("SELECT id FROM users").fetchall()
    user_ids.update(validate_user_id(str(row["id"])) for row in rows)
    return user_ids


class AmbiguousIdentityError(PermissionError):
    """Raised when identity state is missing/stale but more than one user is known.

    Electron must hand this off to the normal user-selection flow (the same
    persisted-session mechanism as non-interactive ``rex identify --user``)
    rather than fail the launch outright.
    """

    def __init__(self, known_user_ids: list[str]) -> None:
        super().__init__("AskRex needs a user selection before it can open.")
        self.known_user_ids = known_user_ids


def resolve_electron_session_user() -> str:
    """Resolve or safely restore the user for a normal Electron launch."""
    from rex.config_manager import load_config
    from rex.identity import resolve_active_user, set_session_user, validate_user_id

    configured_user = resolve_active_user(config=load_config())
    known_users = _discover_desktop_user_ids()

    if configured_user is not None and configured_user in known_users:
        return validate_user_id(configured_user)

    if len(known_users) == 1:
        restored_user = next(iter(known_users))
        # Repair missing or stale transient identity so subsequent launches
        # use the ordinary persisted-session path.
        set_session_user(restored_user)
        return restored_user

    if len(known_users) > 1:
        # Identity state is missing or stale and more than one desktop user
        # exists: never guess. Electron must present the existing
        # user-selection flow instead of failing the launch.
        raise AmbiguousIdentityError(sorted(known_users))

    if configured_user is not None:
        raise PermissionError("AskRex could not restore the saved desktop user.")
    raise PermissionError("AskRex setup has not created a desktop user yet.")


def select_electron_session_user(user_id: str) -> str:
    """Persist an explicit user selection from the Electron user-selection flow.

    This reuses the same persisted-session mechanism as non-interactive
    ``rex identify --user <id>``; it never selects a user Electron did not
    already discover locally.
    """
    from rex.identity import set_session_user, validate_user_id

    validated = validate_user_id(user_id)
    known_users = _discover_desktop_user_ids()
    if validated not in known_users:
        raise PermissionError("Selected user is not a known AskRex desktop user.")
    set_session_user(validated)
    return validated


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        action = payload.get("action")

        from rex.identity import validate_user_id

        if action == "resolve_electron_session":
            user_id = resolve_electron_session_user()
        elif action == "select_electron_session_user":
            selected = payload.get("user_id")
            if not isinstance(selected, str) or not selected:
                raise ValueError("user_id is required")
            user_id = select_electron_session_user(selected)
        else:
            raise ValueError("Unsupported identity action")

        print(
            json.dumps(
                {
                    "ok": True,
                    "user_id": validate_user_id(user_id),
                    "authentication": "local-os-session",
                }
            ),
            flush=True,
        )
    except AmbiguousIdentityError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "code": "ambiguous_identity",
                    "known_user_ids": exc.known_user_ids,
                }
            ),
            flush=True,
        )
        sys.exit(1)
    except Exception as exc:
        print(json.dumps(bridge_error_response(exc)), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
