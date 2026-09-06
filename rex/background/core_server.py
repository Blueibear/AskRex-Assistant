"""Authenticated loopback protocol for the persistent Rex Core process."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rex.background.paths import BackgroundPaths
from rex.identity import validate_user_id
from rex.runtime.invocation import turn_invocation
from rex.runtime.turn import IdentityResolution, TurnSource

_LOOPBACK_HOST = "127.0.0.1"
_MAX_REQUEST_BYTES = 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 30.0
_MAX_TRANSCRIPT_CHARS = 64 * 1024
_MAX_DEVICE_ID_CHARS = 256


@dataclass(frozen=True, slots=True)
class CoreEndpoint:
    """Connection metadata for one local Rex Core instance."""

    host: str
    port: int
    token: str
    pid: int

    def __post_init__(self) -> None:
        if self.host != _LOOPBACK_HOST:
            raise ValueError("Rex Core endpoint must use IPv4 loopback")
        if not isinstance(self.port, int) or not 0 < self.port <= 65535:
            raise ValueError("Rex Core endpoint port is invalid")
        if not isinstance(self.token, str) or len(self.token) < 32:
            raise ValueError("Rex Core endpoint token is invalid")
        if not isinstance(self.pid, int) or self.pid <= 0:
            raise ValueError("Rex Core endpoint pid is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "token": self.token,
            "pid": self.pid,
        }

    @classmethod
    def from_dict(cls, payload: object) -> CoreEndpoint:
        if not isinstance(payload, dict):
            raise ValueError("Rex Core endpoint payload must be an object")
        try:
            return cls(
                host=payload["host"],
                port=payload["port"],
                token=payload["token"],
                pid=payload["pid"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Rex Core endpoint payload is invalid") from exc


class CoreServer:
    """Serve one canonical Assistant over authenticated local-only JSONL IPC."""

    def __init__(
        self,
        *,
        assistant_factory: Callable[[], Any],
        paths: BackgroundPaths,
        host: str = _LOOPBACK_HOST,
    ) -> None:
        if host != _LOOPBACK_HOST:
            raise ValueError("Rex Core may only bind to IPv4 loopback")
        self._assistant_factory = assistant_factory
        self._paths = paths
        self._host = host
        self._assistant: Any | None = None
        self._server: asyncio.AbstractServer | None = None
        self._endpoint: CoreEndpoint | None = None
        self._closed = asyncio.Event()
        self._close_lock = asyncio.Lock()

    @property
    def endpoint(self) -> CoreEndpoint | None:
        return self._endpoint

    async def start(self) -> CoreEndpoint:
        if self._server is not None:
            if self._endpoint is None:  # pragma: no cover - defensive invariant
                raise RuntimeError("Rex Core server has no endpoint")
            return self._endpoint

        self._assistant = self._assistant_factory()
        server = await asyncio.start_server(
            self._handle_client,
            host=self._host,
            port=0,
            limit=_MAX_REQUEST_BYTES + 1,
        )
        sockets: tuple[Any, ...] = server.sockets or ()
        if len(sockets) != 1:  # pragma: no cover - platform invariant
            server.close()
            await server.wait_closed()
            raise RuntimeError("Rex Core did not receive exactly one listening socket")
        port = int(sockets[0].getsockname()[1])
        endpoint = CoreEndpoint(
            host=self._host,
            port=port,
            token=secrets.token_urlsafe(32),
            pid=os.getpid(),
        )
        self._server = server
        self._endpoint = endpoint
        self._paths.state_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self._paths.core_endpoint_file, endpoint.to_dict())
        _restrict_owner_access(self._paths.core_endpoint_file)
        return endpoint

    async def close(self) -> None:
        async with self._close_lock:
            server = self._server
            if server is None:
                self._closed.set()
                return
            self._server = None
            server.close()
            await server.wait_closed()
            self._remove_owned_endpoint_file()
            self._closed.set()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        shutdown_requested = False
        try:
            payload = await _read_request_payload(reader, writer)
            if payload is None:
                return

            endpoint = self._authorized_endpoint(payload)
            if endpoint is None:
                await _send_json(writer, {"ok": False, "error": "unauthorized"})
                return

            shutdown_requested = await self._dispatch_request(payload, endpoint, writer)
        except Exception:
            # Keep transport errors content-free. Detailed diagnostics belong in
            # process-local logs owned by the Core runtime, not on IPC responses.
            await _send_core_error(writer)
        finally:
            await _close_writer(writer)
            if shutdown_requested:
                await self.close()

    def _authorized_endpoint(self, payload: dict[str, object]) -> CoreEndpoint | None:
        endpoint = self._endpoint
        token = payload.get("token")
        if (
            endpoint is None
            or not isinstance(token, str)
            or not hmac.compare_digest(token, endpoint.token)
        ):
            return None
        return endpoint

    async def _dispatch_request(
        self,
        payload: dict[str, object],
        endpoint: CoreEndpoint,
        writer: asyncio.StreamWriter,
    ) -> bool:
        request_type = payload.get("type")
        if request_type == "health":
            await _send_json(
                writer,
                {"ok": True, "state": "ready", "pid": endpoint.pid},
            )
            return False
        if request_type == "shutdown":
            await _send_json(writer, {"ok": True})
            return True
        if request_type in {"turn", "stream_turn"}:
            request = _parse_turn_request(payload)
            if request is None:
                await _send_json(writer, {"ok": False, "error": "invalid_request"})
                return False
            if request_type == "turn":
                reply = await self._run_turn(request)
                await _send_json(writer, {"ok": True, "reply": reply})
            else:
                await self._stream_turn(request, writer)
            return False

        await _send_json(writer, {"ok": False, "error": "invalid_request"})
        return False

    async def _run_turn(self, request: _TurnRequest) -> str:
        assistant = self._require_assistant()
        with turn_invocation(
            TurnSource.VOICE,
            device_id=request.origin_device_id,
            identity_resolution=request.identity_resolution,
        ):
            reply = await assistant.generate_reply(
                request.transcript,
                voice_mode=request.voice_mode,
                active_user_id=request.active_user_id,
            )
        if not isinstance(reply, str):
            raise TypeError("Assistant reply must be text")
        return reply

    async def _stream_turn(
        self,
        request: _TurnRequest,
        writer: asyncio.StreamWriter,
    ) -> None:
        assistant = self._require_assistant()
        with turn_invocation(
            TurnSource.VOICE,
            device_id=request.origin_device_id,
            identity_resolution=request.identity_resolution,
        ):
            async for chunk in assistant.stream_reply(
                request.transcript,
                voice_mode=request.voice_mode,
                active_user_id=request.active_user_id,
            ):
                if not isinstance(chunk, str):
                    raise TypeError("Assistant stream chunk must be text")
                await _send_json(writer, {"ok": True, "type": "chunk", "text": chunk})
        await _send_json(writer, {"ok": True, "type": "done"})

    def _require_assistant(self) -> Any:
        if self._assistant is None:  # pragma: no cover - start() invariant
            raise RuntimeError("Rex Core server is not started")
        return self._assistant

    def _remove_owned_endpoint_file(self) -> None:
        endpoint = self._endpoint
        path = self._paths.core_endpoint_file
        if endpoint is None or not path.exists():
            return
        try:
            stored = CoreEndpoint.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError):
            return
        if stored.pid == endpoint.pid and hmac.compare_digest(stored.token, endpoint.token):
            path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class _TurnRequest:
    transcript: str
    voice_mode: bool
    active_user_id: str
    origin_device_id: str | None
    identity_resolution: IdentityResolution


async def _read_request_payload(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> dict[str, object] | None:
    try:
        line = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=_REQUEST_TIMEOUT_SECONDS)
    except asyncio.LimitOverrunError:
        await _send_json(writer, {"ok": False, "error": "request_too_large"})
        return None
    except (asyncio.IncompleteReadError, TimeoutError):
        await _send_json(writer, {"ok": False, "error": "invalid_request"})
        return None

    if len(line) - 1 > _MAX_REQUEST_BYTES:
        await _send_json(writer, {"ok": False, "error": "request_too_large"})
        return None
    try:
        payload = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        await _send_json(writer, {"ok": False, "error": "invalid_request"})
        return None
    if not isinstance(payload, dict):
        await _send_json(writer, {"ok": False, "error": "invalid_request"})
        return None
    return payload


def _parse_turn_request(payload: dict[str, object]) -> _TurnRequest | None:
    transcript = payload.get("transcript")
    voice_mode = payload.get("voice_mode", True)
    active_user_id = payload.get("active_user_id")
    origin_device_id = payload.get("origin_device_id")
    identity_value = payload.get("identity_resolution", IdentityResolution.UNKNOWN.value)

    if (
        not isinstance(transcript, str)
        or not transcript.strip()
        or len(transcript) > _MAX_TRANSCRIPT_CHARS
        or not isinstance(voice_mode, bool)
        or not isinstance(active_user_id, str)
        or not isinstance(identity_value, str)
    ):
        return None
    try:
        active_user_id = validate_user_id(active_user_id)
        identity_resolution = IdentityResolution(identity_value)
    except (TypeError, ValueError):
        return None
    if origin_device_id is not None:
        if (
            not isinstance(origin_device_id, str)
            or not origin_device_id.strip()
            or len(origin_device_id) > _MAX_DEVICE_ID_CHARS
        ):
            return None
    return _TurnRequest(
        transcript=transcript,
        voice_mode=voice_mode,
        active_user_id=active_user_id,
        origin_device_id=origin_device_id,
        identity_resolution=identity_resolution,
    )


async def _send_json(writer: asyncio.StreamWriter, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
    writer.write(encoded)
    await writer.drain()


async def _send_core_error(writer: asyncio.StreamWriter) -> None:
    if writer.is_closing():
        return
    try:
        await _send_json(writer, {"ok": False, "error": "core_error"})
    except (ConnectionError, OSError, RuntimeError):
        return


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError, RuntimeError):
        return


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _restrict_owner_access(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        # Windows ACL hardening belongs to the install/runtime task. The token
        # remains mandatory even when chmod semantics are unavailable.
        return
