"""Canonical users database access and idempotent mobile schema migrations.

The mobile gateway reuses ``data/users.db`` (the database owned by
``rex.auth`` / ``rex.permissions``).  It never creates a second users
database.  This module adds, through idempotent migrations:

- ``users.disabled_at`` — explicit user-active state (existing rows stay
  active because the new column defaults to NULL);
- ``mobile_sessions`` — per-device mobile sessions;
- ``mobile_refresh_tokens`` — hashed, rotating refresh-token families.

Raw refresh tokens are never stored; only SHA-256 hashes.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from rex.runtime_paths import household_data_path

logger = logging.getLogger(__name__)

_BUSY_TIMEOUT_MS = 5000


def default_users_db_path() -> Path:
    """Return the canonical household users database path."""
    return household_data_path("users.db")


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a connection to the users database with safe defaults.

    ``isolation_level=None`` puts sqlite3 in autocommit mode so transaction
    boundaries are explicit (``BEGIN IMMEDIATE`` / ``COMMIT``), which the
    refresh-rotation race guarantees depend on.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def migrate_users_db(db_path: Path | str) -> None:
    """Apply all mobile gateway migrations to the users database.

    Safe to run repeatedly and safe on fresh, legacy, and already-migrated
    databases.  Existing users, password hashes, and permissions are
    preserved; existing users remain active (``disabled_at`` defaults NULL).
    """
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Canonical tables (identical shape to rex.auth / rex.permissions) so
        # a fresh database is usable without importing those modules first.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id       TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                created  TEXT NOT NULL
            )
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_permissions (
                user_id    TEXT NOT NULL,
                permission TEXT NOT NULL,
                PRIMARY KEY (user_id, permission)
            )
            """)
        if "disabled_at" not in _table_columns(conn, "users"):
            conn.execute("ALTER TABLE users ADD COLUMN disabled_at TEXT NULL")
            logger.info("users.db migration: added users.disabled_at column")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                device_name TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                app_version TEXT NOT NULL DEFAULT '',
                paired_device_id TEXT NULL,
                grant_id TEXT NULL,
                grant_version INTEGER NULL,
                desktop_id TEXT NULL,
                strong_auth_at TEXT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT NULL,
                revoke_reason TEXT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """)
        session_columns = _table_columns(conn, "mobile_sessions")
        session_migrations = {
            "paired_device_id": "TEXT NULL",
            "grant_id": "TEXT NULL",
            "grant_version": "INTEGER NULL",
            "desktop_id": "TEXT NULL",
            "strong_auth_at": "TEXT NULL",
        }
        for column, definition in session_migrations.items():
            if column not in session_columns:
                conn.execute(f"ALTER TABLE mobile_sessions ADD COLUMN {column} {definition}")
                logger.info("users.db migration: added mobile_sessions.%s", column)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mobile_sessions_user
            ON mobile_sessions(user_id, revoked_at)
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mobile_sessions_grant
            ON mobile_sessions(paired_device_id, grant_id, revoked_at)
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_refresh_tokens (
                token_hash TEXT PRIMARY KEY,
                family_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT NULL,
                revoked_at TEXT NULL,
                replacement_hash TEXT NULL,
                FOREIGN KEY (session_id) REFERENCES mobile_sessions(session_id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mobile_refresh_family
            ON mobile_refresh_tokens(family_id)
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_message_requests (
                user_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                response_json TEXT NULL,
                error_code TEXT NULL,
                PRIMARY KEY (user_id, message_id)
            )
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mobile_message_requests_created
            ON mobile_message_requests(created_at)
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_conversation_attachments (
                attachment_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                storage_name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mobile_conversation_attachments_owner
            ON mobile_conversation_attachments(user_id, device_id, conversation_id, expires_at)
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_pairing_authority (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                desktop_id TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            )
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_pairing_challenges (
                challenge_id TEXT PRIMARY KEY,
                desktop_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                nonce_b64 TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT NULL,
                status TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """)
        challenge_columns = _table_columns(conn, "mobile_pairing_challenges")
        if "desktop_cert_fingerprint" not in challenge_columns:
            conn.execute(
                "ALTER TABLE mobile_pairing_challenges "
                "ADD COLUMN desktop_cert_fingerprint TEXT NOT NULL DEFAULT ''"
            )
            logger.info(
                "users.db migration: added mobile_pairing_challenges.desktop_cert_fingerprint"
            )
        for column, definition in {
            "server_url": "TEXT NOT NULL DEFAULT ''",
            "spki_pins_json": "TEXT NOT NULL DEFAULT '[]'",
        }.items():
            if column not in challenge_columns:
                conn.execute(
                    f"ALTER TABLE mobile_pairing_challenges ADD COLUMN {column} {definition}"
                )
                logger.info("users.db migration: added mobile_pairing_challenges.%s", column)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_pairing_requests (
                request_id TEXT PRIMARY KEY,
                challenge_id TEXT UNIQUE NOT NULL,
                desktop_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                public_key_b64 TEXT NOT NULL,
                key_thumbprint TEXT NOT NULL,
                device_name TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                scopes_json TEXT NOT NULL,
                poll_token_hash TEXT NOT NULL,
                submitted_at TEXT NOT NULL,
                status TEXT NOT NULL,
                decision_at TEXT NULL,
                decision_by TEXT NULL,
                denial_reason TEXT NULL,
                device_id TEXT NULL,
                grant_id TEXT NULL,
                FOREIGN KEY (challenge_id) REFERENCES mobile_pairing_challenges(challenge_id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """)
        request_columns = _table_columns(conn, "mobile_pairing_requests")
        if "desktop_cert_fingerprint" not in request_columns:
            conn.execute(
                "ALTER TABLE mobile_pairing_requests "
                "ADD COLUMN desktop_cert_fingerprint TEXT NOT NULL DEFAULT ''"
            )
            logger.info(
                "users.db migration: added mobile_pairing_requests.desktop_cert_fingerprint"
            )
        for column, definition in {
            "server_url": "TEXT NOT NULL DEFAULT ''",
            "spki_pins_json": "TEXT NOT NULL DEFAULT '[]'",
        }.items():
            if column not in request_columns:
                conn.execute(
                    f"ALTER TABLE mobile_pairing_requests ADD COLUMN {column} {definition}"
                )
                logger.info("users.db migration: added mobile_pairing_requests.%s", column)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mobile_pairing_requests_status
            ON mobile_pairing_requests(status, submitted_at)
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_paired_devices (
                device_id TEXT PRIMARY KEY,
                desktop_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                public_key_b64 TEXT NOT NULL,
                key_thumbprint TEXT NOT NULL,
                device_name TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                revoked_at TEXT NULL,
                revoke_reason TEXT NULL,
                UNIQUE (desktop_id, key_thumbprint),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """)
        device_columns = _table_columns(conn, "mobile_paired_devices")
        if "desktop_cert_fingerprint" not in device_columns:
            # Immutable once set (S7), matching key_thumbprint. Existing rows
            # migrated from a pre-S7 database get '' (unbound) so a TLS-enabled
            # activation fails closed and forces re-pairing rather than
            # silently trusting an unbound legacy device.
            conn.execute(
                "ALTER TABLE mobile_paired_devices "
                "ADD COLUMN desktop_cert_fingerprint TEXT NOT NULL DEFAULT ''"
            )
            logger.info("users.db migration: added mobile_paired_devices.desktop_cert_fingerprint")
        for column, definition in {
            "server_url": "TEXT NOT NULL DEFAULT ''",
            "spki_pins_json": "TEXT NOT NULL DEFAULT '[]'",
        }.items():
            if column not in device_columns:
                conn.execute(f"ALTER TABLE mobile_paired_devices ADD COLUMN {column} {definition}")
                logger.info("users.db migration: added mobile_paired_devices.%s", column)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_device_grants (
                grant_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                desktop_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                scopes_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_strong_auth_at TEXT NULL,
                revoked_at TEXT NULL,
                revoke_reason TEXT NULL,
                UNIQUE (device_id, version),
                FOREIGN KEY (device_id) REFERENCES mobile_paired_devices(device_id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """)
        grant_columns = _table_columns(conn, "mobile_device_grants")
        if "last_strong_auth_at" not in grant_columns:
            conn.execute(
                "ALTER TABLE mobile_device_grants ADD COLUMN last_strong_auth_at TEXT NULL"
            )
            logger.info("users.db migration: added mobile_device_grants.last_strong_auth_at")
        for column, definition in {
            "desktop_cert_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "server_url": "TEXT NOT NULL DEFAULT ''",
            "spki_pins_json": "TEXT NOT NULL DEFAULT '[]'",
        }.items():
            if column not in grant_columns:
                conn.execute(f"ALTER TABLE mobile_device_grants ADD COLUMN {column} {definition}")
                logger.info("users.db migration: added mobile_device_grants.%s", column)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_device_session_challenges (
                challenge_id TEXT PRIMARY KEY,
                bootstrap_session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                grant_id TEXT NOT NULL,
                grant_version INTEGER NOT NULL,
                desktop_id TEXT NOT NULL,
                nonce_b64 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT NULL,
                replacement_session_id TEXT NULL,
                FOREIGN KEY (bootstrap_session_id) REFERENCES mobile_sessions(session_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (device_id) REFERENCES mobile_paired_devices(device_id),
                FOREIGN KEY (grant_id) REFERENCES mobile_device_grants(grant_id)
            )
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mobile_device_session_challenges_expiry
            ON mobile_device_session_challenges(expires_at, used_at)
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_strong_auth_challenges (
                challenge_id TEXT PRIMARY KEY,
                approval_id TEXT UNIQUE NULL,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                grant_id TEXT NOT NULL,
                grant_version INTEGER NOT NULL,
                desktop_id TEXT NOT NULL,
                action_name TEXT NOT NULL,
                action_hash TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                required_scope TEXT NOT NULL,
                nonce_b64 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                verified_at TEXT NULL,
                approval_expires_at TEXT NULL,
                consumed_at TEXT NULL,
                FOREIGN KEY (session_id) REFERENCES mobile_sessions(session_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (device_id) REFERENCES mobile_paired_devices(device_id),
                FOREIGN KEY (grant_id) REFERENCES mobile_device_grants(grant_id)
            )
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mobile_strong_auth_challenges_expiry
            ON mobile_strong_auth_challenges(expires_at, approval_expires_at, consumed_at)
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mobile_strong_auth_challenges_binding
            ON mobile_strong_auth_challenges(session_id, device_id, grant_id, action_hash)
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_strong_auth_audit (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                challenge_id TEXT NULL,
                approval_id TEXT NULL,
                session_id TEXT NULL,
                user_id TEXT NULL,
                device_id TEXT NULL,
                grant_id TEXT NULL,
                action_name TEXT NULL,
                action_hash TEXT NULL,
                risk_level TEXT NULL,
                reason TEXT NULL,
                created_at TEXT NOT NULL
            )
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mobile_strong_auth_audit_created
            ON mobile_strong_auth_audit(created_at, event_type)
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_pairing_audit (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                request_id TEXT NULL,
                device_id TEXT NULL,
                grant_id TEXT NULL,
                desktop_id TEXT NULL,
                user_id TEXT NULL,
                created_at TEXT NOT NULL,
                detail TEXT NULL
            )
            """)
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


__all__ = [
    "connect",
    "default_users_db_path",
    "migrate_users_db",
]
