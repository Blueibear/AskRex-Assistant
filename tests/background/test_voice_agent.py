"""Unit tests for the background Voice Agent process adapter."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from rex.assistant_errors import AudioDeviceError, TextToSpeechError, WakeWordError
from rex.background.core_server import CoreEndpoint
from rex.background.paths import BackgroundPaths
from rex.background.types import HealthState
from rex.background.voice_agent import build_voice_agent, run_voice_agent


class _FakeLoop:
    def __init__(self) -> None:
        self.run_calls = 0

    async def run(self) -> None:
        self.run_calls += 1


class _FakeClient:
    pass


def _patch_core(monkeypatch: pytest.MonkeyPatch, expected_path: Path) -> _FakeClient:
    client = _FakeClient()

    def _load(path: Path):
        assert Path(path) == expected_path
        return client

    monkeypatch.setattr(
        "rex.background.voice_agent.CoreClient.from_endpoint_file",
        _load,
    )
    return client


def test_voice_agent_builds_canonical_loop_with_core_proxy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    client = _patch_core(monkeypatch, paths.core_endpoint_file)
    captured: dict[str, object] = {}
    loop = _FakeLoop()

    def _build(assistant, **kwargs):
        captured["assistant"] = assistant
        captured.update(kwargs)
        return loop

    monkeypatch.setattr("rex.background.voice_agent.build_voice_loop", _build)
    monkeypatch.setattr("rex.background.voice_agent.resolve_active_user", lambda: "cole")

    runtime = build_voice_agent(
        "james",
        paths,
        activation_mode="wake-word",
        origin_device_id="office-rex",
    )

    proxy = captured["assistant"]
    assert runtime.loop is loop
    assert runtime.proxy is proxy
    assert proxy._client is client
    assert proxy._fallback_user_id == "james"
    assert proxy._origin_device_id == "office-rex"
    assert proxy._resolve_user_id() == "cole"
    assert captured["activation_mode"] == "wake-word"


def test_voice_agent_defaults_to_wake_word_without_loading_local_assistant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    _patch_core(monkeypatch, paths.core_endpoint_file)
    captured: dict[str, object] = {}

    def _build(assistant, **kwargs):
        captured["assistant_type"] = type(assistant).__name__
        captured.update(kwargs)
        return _FakeLoop()

    monkeypatch.setattr("rex.background.voice_agent.build_voice_loop", _build)
    runtime = build_voice_agent("james", paths)

    assert runtime.loop is not None
    assert captured["assistant_type"] == "CoreAssistantProxy"
    assert captured["activation_mode"] == "wake-word"


def test_core_unavailable_maps_to_content_free_degraded_health(tmp_path: Path) -> None:
    async def _run() -> None:
        paths = BackgroundPaths.from_runtime_root(tmp_path)
        health = await run_voice_agent("james", paths)
        assert health.component == "voice_agent"
        assert health.state is HealthState.DEGRADED
        assert health.detail_code == "core_unavailable"
        assert health.pid is not None
        assert set(health.to_dict()) == {
            "component",
            "state",
            "detail_code",
            "observed_at",
            "pid",
        }

    asyncio.run(_run())


@pytest.mark.parametrize(
    ("error", "detail_code"),
    [
        (AudioDeviceError("private microphone path"), "microphone_unavailable"),
        (TextToSpeechError("private speaker name"), "speaker_unavailable"),
        (WakeWordError("private wake model path"), "wakeword_unavailable"),
    ],
)
def test_audio_startup_failures_are_content_free_and_do_not_mutate_core_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: Exception,
    detail_code: str,
) -> None:
    async def _run() -> None:
        paths = BackgroundPaths.from_runtime_root(tmp_path)
        paths.state_dir.mkdir(parents=True)
        endpoint = CoreEndpoint(
            host="127.0.0.1",
            port=49152,
            token="t" * 32,
            pid=1234,
        )
        original = endpoint.to_dict()
        paths.core_endpoint_file.write_text(
            '{"host":"127.0.0.1","port":49152,"token":"' + "t" * 32 + '","pid":1234}\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "rex.background.voice_agent.CoreClient.from_endpoint_file",
            lambda _path: _FakeClient(),
        )

        def _fail_build(*_args, **_kwargs):
            raise error

        monkeypatch.setattr("rex.background.voice_agent.build_voice_loop", _fail_build)
        health = await run_voice_agent("james", paths)

        assert health.state is HealthState.UNAVAILABLE
        assert health.detail_code == detail_code
        assert "private" not in str(health.to_dict())
        assert paths.core_endpoint_file.exists()
        stored = json.loads(paths.core_endpoint_file.read_text(encoding="utf-8"))
        assert stored == original

    asyncio.run(_run())


def test_voice_agent_runs_canonical_loop_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _run() -> None:
        paths = BackgroundPaths.from_runtime_root(tmp_path)
        _patch_core(monkeypatch, paths.core_endpoint_file)
        loop = _FakeLoop()
        monkeypatch.setattr(
            "rex.background.voice_agent.build_voice_loop",
            lambda *_args, **_kwargs: loop,
        )

        health = await run_voice_agent("james", paths, activation_mode="hold-to-talk")

        assert loop.run_calls == 1
        assert health.state is HealthState.STOPPED
        assert health.detail_code is None

    asyncio.run(_run())


class _BlockingLoop:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self) -> None:
        self.started.set()
        await self.release.wait()


def test_voice_agent_publishes_ready_health_before_listening_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _run() -> None:
        paths = BackgroundPaths.from_runtime_root(tmp_path)
        _patch_core(monkeypatch, paths.core_endpoint_file)
        loop = _BlockingLoop()
        monkeypatch.setattr(
            "rex.background.voice_agent.build_voice_loop",
            lambda *_args, **_kwargs: loop,
        )
        task = asyncio.create_task(run_voice_agent("james", paths))
        await asyncio.wait_for(loop.started.wait(), timeout=1.0)

        payload = json.loads(paths.voice_agent_health_file.read_text(encoding="utf-8"))
        assert payload["component"] == "voice_agent"
        assert payload["state"] == "ready"
        assert payload["detail_code"] is None
        assert isinstance(payload["pid"], int)

        loop.release.set()
        health = await asyncio.wait_for(task, timeout=1.0)
        assert health.state is HealthState.STOPPED
        terminal = json.loads(paths.voice_agent_health_file.read_text(encoding="utf-8"))
        assert terminal["state"] == "stopped"

    asyncio.run(_run())


def test_voice_agent_publishes_content_free_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _run() -> None:
        paths = BackgroundPaths.from_runtime_root(tmp_path)
        _patch_core(monkeypatch, paths.core_endpoint_file)

        def _fail_build(*_args, **_kwargs):
            raise AudioDeviceError("private microphone path")

        monkeypatch.setattr("rex.background.voice_agent.build_voice_loop", _fail_build)
        health = await run_voice_agent("james", paths)

        assert health.state is HealthState.UNAVAILABLE
        payload = json.loads(paths.voice_agent_health_file.read_text(encoding="utf-8"))
        assert payload["state"] == "unavailable"
        assert payload["detail_code"] == "microphone_unavailable"
        assert "private microphone path" not in json.dumps(payload)

    asyncio.run(_run())


def test_voice_agent_refreshes_ready_health_while_loop_is_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _run() -> None:
        paths = BackgroundPaths.from_runtime_root(tmp_path)
        _patch_core(monkeypatch, paths.core_endpoint_file)
        loop = _BlockingLoop()
        monkeypatch.setattr("rex.background.voice_agent.build_voice_loop", lambda *_a, **_k: loop)
        monkeypatch.setattr(
            "rex.background.voice_agent._VOICE_HEALTH_HEARTBEAT_SECONDS",
            0.01,
            raising=False,
        )
        task = asyncio.create_task(run_voice_agent("james", paths))
        await asyncio.wait_for(loop.started.wait(), timeout=1.0)
        first = json.loads(paths.voice_agent_health_file.read_text(encoding="utf-8"))["observed_at"]
        await asyncio.sleep(0.04)
        second = json.loads(paths.voice_agent_health_file.read_text(encoding="utf-8"))[
            "observed_at"
        ]
        assert second > first
        loop.release.set()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(_run())


def test_voice_agent_exits_cleanly_when_supervisor_stop_file_is_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _run() -> None:
        paths = BackgroundPaths.from_runtime_root(tmp_path)
        _patch_core(monkeypatch, paths.core_endpoint_file)
        loop = _BlockingLoop()
        monkeypatch.setattr(
            "rex.background.voice_agent.build_voice_loop",
            lambda *_args, **_kwargs: loop,
        )
        task = asyncio.create_task(run_voice_agent("james", paths))
        await asyncio.wait_for(loop.started.wait(), timeout=1.0)

        paths.stop_file.touch()
        health = await asyncio.wait_for(task, timeout=0.5)

        assert health.state is HealthState.STOPPED
        assert health.detail_code is None

    asyncio.run(_run())
