"""US-086 slice (a): authenticated temporary mobile chat attachments."""

from __future__ import annotations

import io
import os
import sqlite3
import uuid
from datetime import timedelta

import pytest

from rex.mobile_api import attachments as attachment_module
from rex.mobile_api.errors import MobileApiError
from rex.mobile_api.routes.attachments import (
    _MULTIPART_OVERHEAD_BYTES,
    _UPLOAD_READ_CHUNK_BYTES,
    _read_attachment_limited,
)
from tests.mobile_api.conftest import (
    auth_header,
    chat_payload,
    create_user,
    paired_login_tokens,
)


def _authed(client, username="james", password="pw-123456"):
    user_id = create_user(username, password)
    tokens = paired_login_tokens(
        client, username, password, scopes=["chat.send", "attachments.upload"]
    )
    return user_id, auth_header(tokens["access_token"])


def _upload(client, headers, conversation_id, data=b"private note", filename="note.txt"):
    return client.post(
        "/mobile/attachments",
        headers=headers,
        data={
            "conversation_id": conversation_id,
            "attachment": (io.BytesIO(data), filename, "application/octet-stream"),
        },
        content_type="multipart/form-data",
    )


class TestMobileAttachments:
    def test_lengthless_oversized_multipart_is_rejected_during_route_parsing(
        self, client, services
    ) -> None:
        """A chunked request must hit the route ceiling before form access completes."""
        _, headers = _authed(client)
        services.config.max_attachment_bytes = 32
        boundary = "askrex-test-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="conversation_id"\r\n\r\n'
            f"{uuid.uuid4()}\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="attachment"; filename="note.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode("ascii") + (b"x" * (services.config.max_attachment_bytes + _MULTIPART_OVERHEAD_BYTES)) + (
            f"\r\n--{boundary}--\r\n"
        ).encode("ascii")

        response = client.open(
            "/mobile/attachments",
            method="POST",
            headers={
                **headers,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            environ_overrides={
                "CONTENT_LENGTH": "",
                "wsgi.input": io.BytesIO(body),
                "wsgi.input_terminated": True,
            },
        )

        assert response.status_code == 413
        assert response.get_json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

    def test_lengthless_upload_stream_is_read_in_bounded_chunks(self) -> None:
        class BoundedStream:
            def __init__(self) -> None:
                self.remaining = _UPLOAD_READ_CHUNK_BYTES * 2
                self.read_sizes: list[int] = []

            def read(self, size: int) -> bytes:
                self.read_sizes.append(size)
                if not self.remaining:
                    return b""
                self.remaining -= size
                return b"x" * size

        stream = BoundedStream()
        with pytest.raises(MobileApiError) as error:
            _read_attachment_limited(stream, _UPLOAD_READ_CHUNK_BYTES + 1)
        assert error.value.status_code == 413
        assert stream.read_sizes == [_UPLOAD_READ_CHUNK_BYTES, _UPLOAD_READ_CHUNK_BYTES]

    def test_requires_attachment_scope_before_file_processing(self, client) -> None:
        create_user("james", "pw-123456")
        tokens = paired_login_tokens(client, "james", "pw-123456", scopes=["chat.send"])
        response = _upload(
            client,
            auth_header(tokens["access_token"]),
            str(uuid.uuid4()),
        )
        assert response.status_code == 403

    def test_rejects_repeated_conversation_id_field(self, client) -> None:
        """Repeated multipart fields must not select an arbitrary conversation."""
        _, headers = _authed(client)
        response = client.post(
            "/mobile/attachments",
            headers=headers,
            data={
                "conversation_id": [str(uuid.uuid4()), str(uuid.uuid4())],
                "attachment": (io.BytesIO(b"private note"), "note.txt", "text/plain"),
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "BAD_REQUEST"

    def test_ingests_private_attachment_and_returns_safe_provenance(self, client) -> None:
        _, headers = _authed(client)
        conversation_id = str(uuid.uuid4())
        response = _upload(client, headers, conversation_id, filename=r"C:\private\note.txt")
        assert response.status_code == 201, response.get_json()
        body = response.get_json()
        assert body["status"] == "ready"
        assert body["attachment"] == {
            "attachment_id": body["attachment"]["attachment_id"],
            "conversation_id": conversation_id,
            "filename": "C_private_note.txt",
            "media_type": "text/plain",
            "size_bytes": 12,
        }
        assert "path" not in repr(body).lower()

    def test_declared_mime_and_extension_do_not_grant_type_authority(self, client) -> None:
        _, headers = _authed(client)
        response = _upload(
            client, headers, str(uuid.uuid4()), data=b"GIF89a" + b"x" * 10, filename="safe.pdf"
        )
        assert response.status_code == 415
        assert response.get_json()["error"]["code"] == "INVALID_MEDIA"

    def test_attachment_reference_is_bound_to_owner_device_and_conversation(
        self, client, fake_chat_service
    ) -> None:
        _, headers_a = _authed(client, "james", "pw-123456")
        _, headers_b = _authed(client, "cole", "pw-abcdef")
        conversation_id = str(uuid.uuid4())
        uploaded = _upload(client, headers_a, conversation_id).get_json()["attachment"]

        valid = chat_payload(
            conversation_id=conversation_id,
            attachment_ids=[uploaded["attachment_id"]],
        )
        response = client.post("/mobile/chat", headers=headers_a, json=valid)
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["attachments"] == [uploaded]
        assert fake_chat_service.calls[-1][0] == valid["message"]

        wrong_user = client.post("/mobile/chat", headers=headers_b, json=valid)
        assert wrong_user.status_code == 403
        wrong_conversation = client.post(
            "/mobile/chat",
            headers=headers_a,
            json=chat_payload(attachment_ids=[uploaded["attachment_id"]]),
        )
        assert wrong_conversation.status_code == 403

    def test_attachment_count_and_temp_expiry_cleanup(self, client, services) -> None:
        _, headers = _authed(client)
        conversation_id = str(uuid.uuid4())
        services.config.max_attachments_per_conversation = 1
        first = _upload(client, headers, conversation_id)
        assert first.status_code == 201
        assert _upload(client, headers, conversation_id).status_code == 413
        attachment_id = first.get_json()["attachment"]["attachment_id"]
        storage = services.attachment_store.storage_root / f"{attachment_id}.bin"
        assert storage.exists()
        services.attachment_store.retention_seconds = 1
        services.attachment_store.create(
            user_id="unused",
            device_id="unused",
            conversation_id=str(uuid.uuid4()),
            filename="new.txt",
            data=b"new",
            max_per_conversation=5,
        )
        # Force expiry without exposing content or relying on client fields.
        from rex.mobile_api.db import connect

        conn = connect(services.db_path)
        try:
            conn.execute(
                "UPDATE mobile_conversation_attachments SET expires_at = ? WHERE attachment_id = ?",
                (
                    (services.attachment_store._now() - timedelta(seconds=1)).isoformat(),
                    attachment_id,
                ),
            )
        finally:
            conn.close()
        services.attachment_store.validate_references(
            user_id="unused",
            device_id="unused",
            conversation_id=str(uuid.uuid4()),
            attachment_ids=(),
        )
        assert not os.path.exists(storage)

    def test_metadata_persistence_failure_removes_created_attachment_file(
        self, services, monkeypatch
    ) -> None:
        real_connect = attachment_module.connect

        class InsertFailingConnection:
            def __init__(self, connection) -> None:
                self.connection = connection

            def execute(self, sql, *args):
                if sql.lstrip().startswith("INSERT INTO mobile_conversation_attachments"):
                    raise sqlite3.DatabaseError("forced metadata failure")
                return self.connection.execute(sql, *args)

            def close(self) -> None:
                self.connection.close()

        monkeypatch.setattr(
            attachment_module,
            "connect",
            lambda path: InsertFailingConnection(real_connect(path)),
        )
        with pytest.raises(sqlite3.DatabaseError, match="forced metadata failure"):
            services.attachment_store.create(
                user_id="james",
                device_id="device",
                conversation_id=str(uuid.uuid4()),
                filename="note.txt",
                data=b"private note",
                max_per_conversation=5,
            )
        assert list(services.attachment_store.storage_root.iterdir()) == []
