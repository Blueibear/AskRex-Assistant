"""Interactive-session Voice Agent for the persistent Rex background runtime."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rex.assistant_errors import AudioDeviceError, TextToSpeechError, WakeWordError
from rex.background.core_client import CoreAssistantProxy, CoreClient
from rex.background.paths import BackgroundPaths
from rex.background.types import ComponentHealth, HealthState
from rex.identity import resolve_active_user, validate_user_id
from rex.voice_loop import build_voice_loop

_VOICE_HEALTH_HEARTBEAT_SECONDS = 1.0


class _CoreUnavailable(RuntimeError):
    """Internal marker for unreadable or invalid local Core endpoint metadata."""


@dataclass(frozen=True, slots=True)
class VoiceAgentRuntime:
    """Constructed Voice Agent components before the audio loop is run."""

    proxy: CoreAssistantProxy
    loop: Any


def build_voice_agent(
    user_id: str,
    paths: BackgroundPaths,
    *,
    activation_mode: str = "wake-word",
    origin_device_id: str | None = None,
) -> VoiceAgentRuntime:
    """Build the canonical voice loop with Core as the Assistant implementation."""

    fallback_user_id = validate_user_id(user_id)
    if activation_mode not in {"hold-to-talk", "wake-word"}:
        raise ValueError(f"Unsupported voice activation mode: {activation_mode!r}")

    try:
        client = CoreClient.from_endpoint_file(paths.core_endpoint_file)
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError) as exc:
        raise _CoreUnavailable from exc

    proxy = CoreAssistantProxy(
        client=client,
        user_id=fallback_user_id,
        user_resolver=resolve_active_user,
        origin_device_id=origin_device_id,
    )
    loop = build_voice_loop(proxy, activation_mode=activation_mode)
    return VoiceAgentRuntime(proxy=proxy, loop=loop)


async def run_voice_agent(
    user_id: str,
    paths: BackgroundPaths,
    *,
    activation_mode: str = "wake-word",
    origin_device_id: str | None = None,
) -> ComponentHealth:
    """Run one Voice Agent lifecycle and return content-free terminal health."""

    # Validate caller configuration before mapping runtime failures so a bad
    # configured profile never masquerades as a transient Core/audio outage.
    validate_user_id(user_id)
    if activation_mode not in {"hold-to-talk", "wake-word"}:
        raise ValueError(f"Unsupported voice activation mode: {activation_mode!r}")

    try:
        runtime = build_voice_agent(
            user_id,
            paths,
            activation_mode=activation_mode,
            origin_device_id=origin_device_id,
        )
    except _CoreUnavailable:
        return _publish_health(paths, HealthState.DEGRADED, "core_unavailable")
    except AudioDeviceError:
        return _publish_health(paths, HealthState.UNAVAILABLE, "microphone_unavailable")
    except TextToSpeechError:
        return _publish_health(paths, HealthState.UNAVAILABLE, "speaker_unavailable")
    except WakeWordError:
        return _publish_health(paths, HealthState.UNAVAILABLE, "wakeword_unavailable")

    _publish_health(paths, HealthState.READY, None)
    await _run_loop_with_health_heartbeat(runtime.loop, paths)
    return _publish_health(paths, HealthState.STOPPED, None)


async def _run_loop_with_health_heartbeat(loop: Any, paths: BackgroundPaths) -> None:
    loop_task = asyncio.create_task(loop.run())
    heartbeat_task = asyncio.create_task(_ready_health_heartbeat(paths))
    stop_task = asyncio.create_task(_wait_for_stop_request(paths))
    try:
        done, _pending = await asyncio.wait(
            {loop_task, heartbeat_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done:
            loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await loop_task
            return
        if heartbeat_task in done:
            heartbeat_error = heartbeat_task.exception()
            if heartbeat_error is not None:
                loop_task.cancel()
                with suppress(asyncio.CancelledError):
                    await loop_task
                raise heartbeat_error
        await loop_task
    finally:
        for task in (heartbeat_task, stop_task):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def _wait_for_stop_request(paths: BackgroundPaths) -> None:
    while not paths.stop_file.exists():
        await asyncio.sleep(0.05)


async def _ready_health_heartbeat(paths: BackgroundPaths) -> None:
    while True:
        await asyncio.sleep(_VOICE_HEALTH_HEARTBEAT_SECONDS)
        _publish_health(paths, HealthState.READY, None)


def _health(state: HealthState, detail_code: str | None) -> ComponentHealth:
    return ComponentHealth(
        component="voice_agent",
        state=state,
        detail_code=detail_code,
        observed_at=time.time(),
        pid=os.getpid(),
    )


def _publish_health(
    paths: BackgroundPaths, state: HealthState, detail_code: str | None
) -> ComponentHealth:
    health = _health(state, detail_code)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=paths.state_dir,
            prefix=".voice-agent-health.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(health.to_dict(), handle, separators=(",", ":"), ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, paths.voice_agent_health_file)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return health
