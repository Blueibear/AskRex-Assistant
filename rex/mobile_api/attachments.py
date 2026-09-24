"""Private, short-lived mobile chat attachment storage.

This is intentionally only the first US-086 slice.  Attachments are bound
to the authenticated user, paired device, and one conversation.  They are
not indexed, promoted to memory, or injected into unrelated turns.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rex.mobile_api import errors as merr
from rex.mobile_api.db import connect
from rex.mobile_api.errors import MobileApiError

MAX_FILENAME_CHARS = 120
_FILENAME_CONTROL = re.compile(r"[\\/:\x00-\x1f\x7f]+")


@dataclass(frozen=True)
class Attachment:
    attachment_id: str
    conversation_id: str
    filename: str
    media_type: str
    size_bytes: int

    def public_dict(self) -> dict[str, object]:
        return {
            "attachment_id": self.attachment_id,
            "conversation_id": self.conversation_id,
            "filename": self.filename,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
        }


def sanitized_filename(name: object) -> str:
    """Return presentation-safe basename, never a local client path."""
    if not isinstance(name, str):
        raise MobileApiError(merr.BAD_REQUEST, "Attachment filename is invalid.", 400)
    cleaned = _FILENAME_CONTROL.sub("_", name).strip(" .")
    if not cleaned or cleaned in {".", ".."} or len(cleaned) > MAX_FILENAME_CHARS:
        raise MobileApiError(merr.BAD_REQUEST, "Attachment filename is invalid.", 400)
    return cleaned


def sniff_media_type(data: bytes) -> str | None:
    """Return an allowlisted type from bytes, never from a MIME claim."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {
        b"heic", b"heix", b"hevc", b"hevx", b"mif1",
    }:
        return "image/heic"
    if data.startswith(b"%PDF-"):
        return "application/pdf" if b"%%EOF" in data[-2048:] else None
    # Never reinterpret a known binary container as an innocent text file.
    if data.startswith((b"GIF87a", b"GIF89a", b"PK\x03\x04", b"Rar!", b"\x7fELF")):
        return None
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not decoded or "\x00" in decoded:
        return None
    return "text/plain"


class MobileAttachmentStore:
    """Metadata in the canonical mobile DB, content in a private temp root."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        retention_seconds: int,
        storage_root: Path | None = None,
    ):
        self.db_path = Path(db_path)
        self.retention_seconds = retention_seconds
        self.storage_root = storage_root or self.db_path.parent / "mobile_attachments"
        self.storage_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _cleanup_expired(self, conn, now: datetime) -> None:  # noqa: ANN001
        rows = conn.execute(
            "SELECT attachment_id, storage_name FROM mobile_conversation_attachments "
            "WHERE expires_at <= ?",
            (now.isoformat(),),
        ).fetchall()
        for row in rows:
            path = self.storage_root / str(row["storage_name"])
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # The database record is still removed: an inaccessible stale
                # temp file must never remain authorized.
                pass
        conn.execute(
            "DELETE FROM mobile_conversation_attachments WHERE expires_at <= ?",
            (now.isoformat(),),
        )

    def create(
        self,
        *,
        user_id: str,
        device_id: str,
        conversation_id: str,
        filename: str,
        data: bytes,
        max_per_conversation: int,
    ) -> Attachment:
        media_type = sniff_media_type(data)
        if media_type is None:
            raise MobileApiError(merr.INVALID_MEDIA, "Unsupported or malformed attachment.", 415)
        now = self._now()
        attachment_id = str(uuid.uuid4())
        storage_name = f"{attachment_id}.bin"
        expires_at = now + timedelta(seconds=self.retention_seconds)
        conn = connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup_expired(conn, now)
            count = conn.execute(
                "SELECT COUNT(*) FROM mobile_conversation_attachments "
                "WHERE user_id = ? AND device_id = ? AND conversation_id = ?",
                (user_id, device_id, conversation_id),
            ).fetchone()[0]
            if count >= max_per_conversation:
                raise MobileApiError(
                    merr.PAYLOAD_TOO_LARGE,
                    "Too many attachments for this conversation.",
                    413,
                )
            path = self.storage_root / storage_name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(path, flags, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(data)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            conn.execute(
                "INSERT INTO mobile_conversation_attachments "
                "(attachment_id, user_id, device_id, conversation_id, filename, media_type, "
                "size_bytes, storage_name, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attachment_id,
                    user_id,
                    device_id,
                    conversation_id,
                    filename,
                    media_type,
                    len(data),
                    storage_name,
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
        return Attachment(attachment_id, conversation_id, filename, media_type, len(data))

    def validate_references(
        self,
        *,
        user_id: str,
        device_id: str,
        conversation_id: str,
        attachment_ids: tuple[str, ...],
    ) -> tuple[Attachment, ...]:
        now = self._now()
        conn = connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup_expired(conn, now)
            if not attachment_ids:
                conn.execute("COMMIT")
                return ()
            found: list[Attachment] = []
            for attachment_id in attachment_ids:
                row = conn.execute(
                    "SELECT attachment_id, conversation_id, filename, media_type, size_bytes "
                    "FROM mobile_conversation_attachments WHERE attachment_id = ? AND user_id = ? "
                    "AND device_id = ? AND conversation_id = ?",
                    (attachment_id, user_id, device_id, conversation_id),
                ).fetchone()
                if row is None:
                    raise MobileApiError(
                        merr.FORBIDDEN,
                        "Attachment is not available for this conversation.",
                        403,
                    )
                if not (self.storage_root / str(row["storage_name"])).is_file():
                    raise MobileApiError(merr.FORBIDDEN, "Attachment is no longer available.", 403)
                found.append(
                    Attachment(
                        str(row["attachment_id"]),
                        str(row["conversation_id"]),
                        str(row["filename"]),
                        str(row["media_type"]),
                        int(row["size_bytes"]),
                    )
                )
            conn.execute("COMMIT")
            return tuple(found)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()


__all__ = [
    "Attachment",
    "MAX_FILENAME_CHARS",
    "MobileAttachmentStore",
    "sanitized_filename",
    "sniff_media_type",
]
