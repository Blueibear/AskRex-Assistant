"""Private, short-lived mobile chat attachment storage.

This is intentionally only the first US-086 slice.  Attachments are bound
to the authenticated user, paired device, and one conversation.  They are
not indexed, promoted to memory, or injected into unrelated turns.
"""

from __future__ import annotations

import os
import re
import uuid
import zlib
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
    # Multipart clients can submit either Windows or POSIX paths.  Drop every
    # directory component before sanitizing the basename so response metadata
    # never discloses the client's local directory names.
    basename = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _FILENAME_CONTROL.sub("_", basename).strip(" .")
    if not cleaned or cleaned in {".", ".."} or len(cleaned) > MAX_FILENAME_CHARS:
        raise MobileApiError(merr.BAD_REQUEST, "Attachment filename is invalid.", 400)
    return cleaned


def sniff_media_type(data: bytes) -> str | None:
    """Return an allowlisted type from bytes, never from a MIME claim."""
    if _is_valid_png(data):
        return "image/png"
    if _is_valid_jpeg(data):
        return "image/jpeg"
    if _is_valid_heic(data):
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


def _is_valid_png(data: bytes) -> bool:
    """Perform bounded, non-decoding PNG container validation."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    seen_ihdr = False
    seen_idat = False
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            return False
        chunk_type = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        checksum = int.from_bytes(data[offset + 8 + length : chunk_end], "big")
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != checksum:
            return False
        if not seen_ihdr:
            if chunk_type != b"IHDR" or length != 13:
                return False
            width = int.from_bytes(payload[:4], "big")
            height = int.from_bytes(payload[4:8], "big")
            if not width or not height:
                return False
            seen_ihdr = True
        elif chunk_type == b"IDAT":
            seen_idat = True
        elif chunk_type == b"IEND":
            return length == 0 and seen_idat and chunk_end == len(data)
        offset = chunk_end
    return False


def _is_valid_jpeg(data: bytes) -> bool:
    """Check JPEG segment framing, image dimensions, and terminal EOI marker."""
    if not data.startswith(b"\xff\xd8"):
        return False
    offset = 2
    seen_frame = False
    while offset < len(data):
        if data[offset] != 0xFF:
            return False
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            return False
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            return seen_frame and offset == len(data)
        if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            return False
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            return False
        payload = data[offset + 2 : offset + segment_length]
        offset += segment_length
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if (
                len(payload) < 6
                or not int.from_bytes(payload[1:3], "big")
                or not int.from_bytes(payload[3:5], "big")
            ):
                return False
            seen_frame = True
        if marker == 0xDA:
            if not seen_frame or len(payload) < 6:
                return False
            while offset < len(data):
                if data[offset] != 0xFF:
                    offset += 1
                    continue
                if offset + 1 >= len(data):
                    return False
                next_byte = data[offset + 1]
                if next_byte == 0x00 or 0xD0 <= next_byte <= 0xD7:
                    offset += 2
                    continue
                if next_byte == 0xD9:
                    return offset + 2 == len(data)
                return False
    return False


_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"mif1"}


def _bmff_boxes(data: bytes, start: int, end: int):
    """Yield bounded ISO BMFF boxes, rejecting truncated/extended containers."""
    offset = start
    while offset < end:
        if offset + 8 > end:
            return
        size = int.from_bytes(data[offset : offset + 4], "big")
        box_type = data[offset + 4 : offset + 8]
        header_size = 8
        if size == 1:
            if offset + 16 > end:
                return
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
            header_size = 16
        if size == 0:
            size = end - offset
        if size < header_size or offset + size > end:
            return
        yield box_type, offset + header_size, offset + size
        offset += size


def _is_valid_heic(data: bytes) -> bool:
    """Require a complete HEIF BMFF file with ftyp and pict meta handler."""
    boxes = list(_bmff_boxes(data, 0, len(data)))
    if not boxes or boxes[-1][2] != len(data) or boxes[0][0] != b"ftyp":
        return False
    _, ftyp_start, ftyp_end = boxes[0]
    ftyp = data[ftyp_start:ftyp_end]
    if len(ftyp) < 8 or len(ftyp) % 4:
        return False
    brands = {ftyp[:4], *(ftyp[index : index + 4] for index in range(8, len(ftyp), 4))}
    if not brands.intersection(_HEIC_BRANDS):
        return False
    for box_type, meta_start, meta_end in boxes:
        if box_type != b"meta" or meta_end - meta_start < 4:
            continue
        children = list(_bmff_boxes(data, meta_start + 4, meta_end))
        if not children or children[-1][2] != meta_end:
            continue
        for child_type, child_start, child_end in children:
            if child_type == b"hdlr" and child_end - child_start >= 12:
                if data[child_start + 8 : child_start + 12] == b"pict":
                    return True
    return False


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
                # Preserve the expired row as retryable cleanup state. It is
                # already unauthorized (all reads require a future expiry),
                # and a later cleanup pass can still locate the private bytes.
                continue
            conn.execute(
                "DELETE FROM mobile_conversation_attachments WHERE attachment_id = ?",
                (str(row["attachment_id"]),),
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
        path: Path | None = None
        metadata_persisted = False
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
            metadata_persisted = True
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            # Content is authorized only by its metadata row.  If inserting
            # or committing that row fails, do not leave an unreachable file
            # containing private upload data behind.
            if path is not None and not metadata_persisted:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
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
                    "SELECT attachment_id, conversation_id, filename, media_type, size_bytes, "
                    "storage_name "
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
