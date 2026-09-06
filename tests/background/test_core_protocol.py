"""Protocol tests for the Electron-independent Rex Core process."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rex.background.core_client import CoreAssistantProxy, CoreClient
from rex.background.core_server import CoreEndpoint, CoreServer
from rex.background.paths import BackgroundPaths
from rex.runtime.invocation import current_turn_invocation, turn_invocation
from rex.runtime.turn import IdentityResolution, TurnSource


class _FakeAssistant:
    def __init__(self) -> None:
        self.generate_calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []

    async def generate_reply(
        self,
        transcript: str,
        *,
        voice_mode: bool = False,
        active_user_id: str | None = None,
        **_kwargs: object,
    ) -> str:
        invocation = current_turn_invocation()
        self.generate_calls.append(
            {
                "transcript": transcript,
                "voice_mode": voice_mode,
                "active_user_id": active_user_id,
                "source": invocation.source,
                "device_id": invocation.device_id,
                "identity_resolution": invocation.identity_resolution,
            }
        )
        return "Core reply"

    async def stream_reply(
        self,
        transcript: str,
        *,
        voice_mode: bool = False,
        active_user_id: str | None = None,
        **_kwargs: object,
    ):
        invocation = current_turn_invocation()
        self.stream_calls.append(
            {
                "transcript": transcript,
                "voice_mode": voice_mode,
                "active_user_id": active_user_id,
                "source": invocation.source,
                "device_id": invocation.device_id,
                "identity_resolution": invocation.identity_resolution,
            }
        )
        yield "First sentence."
        yield "Second sentence."


async def _start_server(
    tmp_path: Path,
) -> tuple[CoreServer, CoreClient, _FakeAssistant, CoreEndpoint]:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    assistant = _FakeAssistant()
    server = CoreServer(assistant_factory=lambda: assistant, paths=paths)
    endpoint = await server.start()
    client = CoreClient(endpoint)
    return server, client, assistant, endpoint


def test_invalid_token_is_rejected_without_assistant_call(tmp_path: Path) -> None:
    async def _run() -> None:
        server, _client, assistant, endpoint = await _start_server(tmp_path)
        try:
            bad = CoreClient(
                CoreEndpoint(
                    host=endpoint.host,
                    port=endpoint.port,
                    token="w" * 32,
                    pid=endpoint.pid,
                )
            )
            response = await bad.health()
            assert response == {"ok": False, "error": "unauthorized"}
            assert assistant.generate_calls == []
            assert assistant.stream_calls == []
        finally:
            await server.close()

    asyncio.run(_run())


def test_malformed_and_oversized_requests_fail_boundedly(tmp_path: Path) -> None:
    async def _send(endpoint: CoreEndpoint, payload: bytes) -> dict[str, object]:
        reader, writer = await asyncio.open_connection(endpoint.host, endpoint.port)
        writer.write(payload + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=2.0)
        writer.close()
        await writer.wait_closed()
        return json.loads(line)

    async def _run() -> None:
        server, _client, assistant, endpoint = await _start_server(tmp_path)
        try:
            malformed = await _send(endpoint, b"not-json")
            oversized = await _send(endpoint, b"{" + b"x" * (1024 * 1024 + 1) + b"}")
            assert malformed == {"ok": False, "error": "invalid_request"}
            assert oversized == {"ok": False, "error": "request_too_large"}
            assert assistant.generate_calls == []
        finally:
            await server.close()

    asyncio.run(_run())


def test_health_is_content_free_and_endpoint_contains_no_identity_or_transcript(
    tmp_path: Path,
) -> None:
    async def _run() -> None:
        server, client, _assistant, endpoint = await _start_server(tmp_path)
        try:
            response = await client.health()
            assert response["ok"] is True
            assert response["state"] == "ready"
            assert set(response) <= {"ok", "state", "pid"}

            stored = json.loads(
                BackgroundPaths.from_runtime_root(tmp_path).core_endpoint_file.read_text(
                    encoding="utf-8"
                )
            )
            assert stored == endpoint.to_dict()
            assert set(stored) == {"host", "port", "token", "pid"}
            assert "user_id" not in stored
            assert "transcript" not in stored
        finally:
            await server.close()

    asyncio.run(_run())


def test_turn_uses_canonical_voice_invocation_and_request_scoped_identity(tmp_path: Path) -> None:
    async def _run() -> None:
        server, client, assistant, _endpoint = await _start_server(tmp_path)
        try:
            reply = await client.turn(
                "turn the light off",
                voice_mode=True,
                active_user_id="james",
                origin_device_id="bedroom-rex",
                identity_resolution=IdentityResolution.VOICE_RECOGNIZED,
            )
            assert reply == "Core reply"
            assert assistant.generate_calls == [
                {
                    "transcript": "turn the light off",
                    "voice_mode": True,
                    "active_user_id": "james",
                    "source": TurnSource.VOICE,
                    "device_id": "bedroom-rex",
                    "identity_resolution": IdentityResolution.VOICE_RECOGNIZED,
                }
            ]
        finally:
            await server.close()

    asyncio.run(_run())


def test_invalid_request_identity_fails_before_assistant_call(tmp_path: Path) -> None:
    async def _run() -> None:
        server, client, assistant, _endpoint = await _start_server(tmp_path)
        try:
            response = await client.request(
                {
                    "type": "turn",
                    "transcript": "private request",
                    "voice_mode": True,
                    "active_user_id": "../other-user",
                    "identity_resolution": IdentityResolution.VOICE_RECOGNIZED.value,
                }
            )
            assert response == {"ok": False, "error": "invalid_request"}
            assert assistant.generate_calls == []
        finally:
            await server.close()

    asyncio.run(_run())


def test_proxy_forwards_current_voice_provenance_and_resolves_user_each_turn(
    tmp_path: Path,
) -> None:
    async def _run() -> None:
        server, client, assistant, _endpoint = await _start_server(tmp_path)
        active_user = "james"
        proxy = CoreAssistantProxy(
            client=client,
            user_id="fallback-user",
            user_resolver=lambda: active_user,
            origin_device_id="kitchen-rex",
        )
        try:
            with turn_invocation(
                TurnSource.VOICE,
                identity_resolution=IdentityResolution.VOICE_RECOGNIZED,
            ):
                assert await proxy.generate_reply("hello", voice_mode=True) == "Core reply"
            active_user = "cole"
            with turn_invocation(
                TurnSource.VOICE,
                identity_resolution=IdentityResolution.VOICE_REVIEW,
            ):
                assert await proxy.generate_reply("hello again", voice_mode=True) == "Core reply"

            assert [call["active_user_id"] for call in assistant.generate_calls] == [
                "james",
                "cole",
            ]
            assert [call["identity_resolution"] for call in assistant.generate_calls] == [
                IdentityResolution.VOICE_RECOGNIZED,
                IdentityResolution.VOICE_REVIEW,
            ]
        finally:
            await server.close()

    asyncio.run(_run())


def test_proxy_preserves_streaming_reply_path(tmp_path: Path) -> None:
    async def _run() -> None:
        server, client, assistant, _endpoint = await _start_server(tmp_path)
        proxy = CoreAssistantProxy(client=client, user_id="james", origin_device_id="office-rex")
        try:
            with turn_invocation(
                TurnSource.VOICE,
                identity_resolution=IdentityResolution.VOICE_RECOGNIZED,
            ):
                chunks = [
                    chunk
                    async for chunk in proxy.stream_reply("tell me something", voice_mode=True)
                ]
            assert chunks == ["First sentence.", "Second sentence."]
            assert len(assistant.stream_calls) == 1
            assert assistant.stream_calls[0]["source"] is TurnSource.VOICE
            assert assistant.stream_calls[0]["active_user_id"] == "james"
            assert assistant.stream_calls[0]["device_id"] == "office-rex"
        finally:
            await server.close()

    asyncio.run(_run())


def test_endpoint_file_loader_and_shutdown_are_authenticated(tmp_path: Path) -> None:
    async def _run() -> None:
        server, _client, _assistant, _endpoint = await _start_server(tmp_path)
        paths = BackgroundPaths.from_runtime_root(tmp_path)
        client = CoreClient.from_endpoint_file(paths.core_endpoint_file)
        assert (await client.health())["ok"] is True
        assert await client.shutdown() == {"ok": True}
        await asyncio.wait_for(server.wait_closed(), timeout=2.0)
        assert not paths.core_endpoint_file.exists()

    asyncio.run(_run())


def test_core_client_has_no_silent_except_pass_cleanup() -> None:
    import ast

    source_path = Path(__file__).parents[2] / "rex" / "background" / "core_client.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for name in ("request", "stream_turn"):
        handlers = [
            node for node in ast.walk(functions[name]) if isinstance(node, ast.ExceptHandler)
        ]
        assert not any(
            len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass) for handler in handlers
        ), name
