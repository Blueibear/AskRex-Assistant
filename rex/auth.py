"""User authentication module for Rex.

Provides user registration, authentication, and session token management.

- Passwords are hashed with bcrypt.
- Users are stored in data/users.db (SQLite).
- Session tokens are JWTs with a 24-hour expiry.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import bcrypt
import jwt

from rex.runtime_paths import household_data_path

logger = logging.getLogger(__name__)

_JWT_ALGORITHM = "HS256"
_TOKEN_EXPIRY_HOURS = 24


def get_jwt_secret() -> str:
    """Return the JWT signing secret from the credential authority.

    Raises:
        RuntimeError: If ``REX_JWT_SECRET`` is not stored. A missing secret is a
            configuration error — there is no hardcoded fallback.  Generate a
            value with: ``python -c "import secrets; print(secrets.token_hex(32))"``
    """
    from rex.credentials import get_persisted_credential

    secret = get_persisted_credential("REX_JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "REX_JWT_SECRET is not set in the credential vault. "
            "Generate a value with: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
    return secret


def _get_db_path() -> Path:
    """Return the canonical household users database path."""
    return household_data_path("users.db")


def _open_db() -> sqlite3.Connection:
    """Open (and initialise) the users database."""
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create the users table if it does not already exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id       TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created  TEXT NOT NULL
        )
        """)
    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_user(username: str, password: str) -> dict[str, Any]:
    """Register a new user.

    Args:
        username: Desired username (must be unique).
        password: Plain-text password (will be hashed with bcrypt).

    Returns:
        Dict with ``id`` and ``username`` on success.

    Raises:
        ValueError: If username already exists or inputs are invalid.
    """
    username = username.strip()
    if not username:
        raise ValueError("username must not be empty")
    if not password:
        raise ValueError("password must not be empty")

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    with _open_db() as conn:
        try:
            conn.execute(
                "INSERT INTO users (id, username, password, created) VALUES (?, ?, ?, ?)",
                (user_id, username, password_hash, now),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"username '{username}' is already taken") from exc

    logger.info("Created user: %s", username)
    return {"id": user_id, "username": username}


def authenticate(username: str, password: str) -> str:
    """Verify credentials and return a JWT session token.

    Args:
        username: Registered username.
        password: Plain-text password to verify.

    Returns:
        Signed JWT string valid for 24 hours.

    Raises:
        ValueError: If the credentials are invalid.
    """
    username = username.strip()

    with _open_db() as conn:
        row = conn.execute(
            "SELECT id, username, password FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    if row is None or not bcrypt.checkpw(password.encode(), row["password"].encode()):
        raise ValueError("invalid username or password")

    payload = {
        "sub": row["id"],
        "username": row["username"],
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(hours=_TOKEN_EXPIRY_HOURS),
    }
    token = jwt.encode(payload, get_jwt_secret(), algorithm=_JWT_ALGORITHM)
    logger.info("Authenticated user: %s", username)
    return str(token)


def get_current_user(token: str) -> dict[str, Any]:
    """Decode a JWT and return the current user's identity.

    Args:
        token: A JWT issued by :func:`authenticate`.

    Returns:
        Dict with ``id`` and ``username`` on success.

    Raises:
        ValueError: If the token is missing, expired, or invalid.
    """
    if not token:
        raise ValueError("no token provided")

    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[_JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise ValueError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise ValueError(f"invalid token: {exc}") from exc

    return {"id": payload["sub"], "username": payload["username"]}


__all__ = [
    "authenticate",
    "create_user",
    "get_current_user",
    "get_jwt_secret",
]
