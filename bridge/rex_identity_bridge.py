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

    if configured_user is not None:
        raise PermissionError("AskRex could not restore the saved desktop user.")
    if known_users:
        raise PermissionError("AskRex needs a user selection before it can open.")
    raise PermissionError("AskRex setup has not created a desktop user yet.")


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if payload.get("action") != "resolve_electron_session":
            raise ValueError("Unsupported identity action")

        from rex.identity import validate_user_id

        user_id = resolve_electron_session_user()

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
    except Exception as exc:
        print(json.dumps(bridge_error_response(exc)), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
