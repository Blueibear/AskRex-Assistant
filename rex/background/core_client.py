"""Client and Assistant-compatible proxy for the local Rex Core protocol."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from rex.background.core_server import CoreEndpoint
from rex.identity import validate_user_id
from rex.runtime.invocation import current_turn_invocation
from rex.runtime.turn import IdentityResolution

_MAX_RESPONSE_BYTES = 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 60.0


class CoreProtocolError(RuntimeError):
    """Raised when Rex Core returns or emits an invalid protocol response."""


class CoreClient:
    """Make bounded authenticated requests to one local Rex Core instance."""

    def __init__(
        self, endpoint: CoreEndpoint, *, timeout: float = _REQUEST_TIMEOUT_SECONDS
    ) -> None:
        self._endpoint = endpoint
        self._timeout = float(timeout)
        if self._timeout <= 0:
            raise ValueError("Core client timeout must be positive")

    @property
    def endpoint(self) -> CoreEndpoint:
        return self._endpoint

    @classmethod
    def from_endpoint_file(
        cls,
        path: str | Path,
        *,
        timeout: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> CoreClient:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(CoreEndpoint.from_dict(payload), timeout=timeout)

    async def request(self, payload: dict[str, object]) -> dict[str, object]:
        request = dict(payload)
        request["token"] = self._endpoint.token
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                self._endpoint.host,
                self._endpoint.port,
                limit=_MAX_RESPONSE_BYTES + 1,
            ),
            timeout=self._timeout,
        )
        try:
            writer.write(_encode_request(request))
            await asyncio.wait_for(writer.drain(), timeout=self._timeout)
            response = await _read_response(reader, timeout=self._timeout)
            return response
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, RuntimeError):
                pass

    async def health(self) -> dict[str, object]:
        return await self.request({"type": "health"})

    async def turn(
        self,
        transcript: str,
        *,
        voice_mode: bool,
        active_user_id: str,
        origin_device_id: str | None = None,
        identity_resolution: IdentityResolution = IdentityResolution.UNKNOWN,
    ) -> str:
        response = await self.request(
            {
                "type": "turn",
                "transcript": transcript,
                "voice_mode": voice_mode,
                "active_user_id": active_user_id,
                "origin_device_id": origin_device_id,
                "identity_resolution": identity_resolution.value,
            }
        )
        if response.get("ok") is not True or not isinstance(response.get("reply"), str):
            raise CoreProtocolError(_response_error(response))
        reply = response["reply"]
        if not isinstance(reply, str):  # pragma: no cover - narrowed above
            raise CoreProtocolError("invalid_response")
        return reply

    async def stream_turn(
        self,
        transcript: str,
        *,
        voice_mode: bool,
        active_user_id: str,
        origin_device_id: str | None = None,
        identity_resolution: IdentityResolution = IdentityResolution.UNKNOWN,
    ) -> AsyncIterator[str]:
        payload: dict[str, object] = {
            "type": "stream_turn",
            "transcript": transcript,
            "voice_mode": voice_mode,
            "active_user_id": active_user_id,
            "origin_device_id": origin_device_id,
            "identity_resolution": identity_resolution.value,
            "token": self._endpoint.token,
        }
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                self._endpoint.host,
                self._endpoint.port,
                limit=_MAX_RESPONSE_BYTES + 1,
            ),
            timeout=self._timeout,
        )
        try:
            writer.write(_encode_request(payload))
            await asyncio.wait_for(writer.drain(), timeout=self._timeout)
            while True:
                response = await _read_response(reader, timeout=self._timeout)
                if response.get("ok") is not True:
                    raise CoreProtocolError(_response_error(response))
                response_type = response.get("type")
                if response_type == "done":
                    return
                if response_type != "chunk" or not isinstance(response.get("text"), str):
                    raise CoreProtocolError("invalid_stream_response")
                text = response["text"]
                if not isinstance(text, str):  # pragma: no cover - narrowed above
                    raise CoreProtocolError("invalid_stream_response")
                yield text
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, RuntimeError):
                pass

    async def shutdown(self) -> dict[str, object]:
        return await self.request({"type": "shutdown"})


class CoreAssistantProxy:
    """Assistant-shaped adapter used by the local Voice Agent."""

    def __init__(
        self,
        *,
        client: CoreClient,
        user_id: str,
        user_resolver: Callable[[], str | None] | None = None,
        origin_device_id: str | None = None,
    ) -> None:
        self._client = client
        self._fallback_user_id = validate_user_id(user_id)
        self._user_resolver = user_resolver
        self._origin_device_id = origin_device_id

    async def generate_reply(
        self,
        transcript: str,
        *,
        voice_mode: bool = False,
        **_kwargs: object,
    ) -> str:
        user_id = self._resolve_user_id()
        invocation = current_turn_invocation()
        device_id = invocation.device_id or self._origin_device_id
        return await self._client.turn(
            transcript,
            voice_mode=voice_mode,
            active_user_id=user_id,
            origin_device_id=device_id,
            identity_resolution=invocation.identity_resolution,
        )

    async def stream_reply(
        self,
        transcript: str,
        *,
        voice_mode: bool = False,
        **_kwargs: object,
    ) -> AsyncIterator[str]:
        user_id = self._resolve_user_id()
        invocation = current_turn_invocation()
        device_id = invocation.device_id or self._origin_device_id
        async for chunk in self._client.stream_turn(
            transcript,
            voice_mode=voice_mode,
            active_user_id=user_id,
            origin_device_id=device_id,
            identity_resolution=invocation.identity_resolution,
        ):
            yield chunk

    def _resolve_user_id(self) -> str:
        if self._user_resolver is None:
            return self._fallback_user_id
        resolved = self._user_resolver()
        if resolved is None or resolved == "":
            return self._fallback_user_id
        return validate_user_id(resolved)


async def _read_response(
    reader: asyncio.StreamReader,
    *,
    timeout: float,
) -> dict[str, object]:
    try:
        line = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=timeout)
    except asyncio.LimitOverrunError as exc:
        raise CoreProtocolError("response_too_large") from exc
    except (asyncio.IncompleteReadError, TimeoutError) as exc:
        raise CoreProtocolError("core_unavailable") from exc
    if len(line) - 1 > _MAX_RESPONSE_BYTES:
        raise CoreProtocolError("response_too_large")
    try:
        payload = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoreProtocolError("invalid_response") from exc
    if not isinstance(payload, dict):
        raise CoreProtocolError("invalid_response")
    return payload


def _encode_request(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"


def _response_error(response: dict[str, object]) -> str:
    error = response.get("error")
    return error if isinstance(error, str) and error else "core_error"
