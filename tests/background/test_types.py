from __future__ import annotations

from pathlib import Path

from rex.background.paths import BackgroundPaths
from rex.background.types import ComponentHealth, HealthState, RuntimeHealth


def test_component_health_wire_payload_is_content_free() -> None:
    health = ComponentHealth(
        component="voice_agent",
        state=HealthState.DEGRADED,
        detail_code="microphone_unavailable",
        observed_at=10.0,
        pid=321,
    )

    assert health.to_dict() == {
        "component": "voice_agent",
        "state": "degraded",
        "detail_code": "microphone_unavailable",
        "observed_at": 10.0,
        "pid": 321,
    }
    payload_text = repr(health.to_dict())
    assert "transcript" not in payload_text
    assert "user_id" not in payload_text
    assert "prompt" not in payload_text


def test_runtime_health_serializes_only_component_state() -> None:
    core = ComponentHealth(
        component="core",
        state=HealthState.READY,
        detail_code=None,
        observed_at=11.0,
        pid=400,
    )
    voice = ComponentHealth(
        component="voice_agent",
        state=HealthState.PAUSED,
        detail_code="listening_paused",
        observed_at=12.0,
        pid=401,
    )
    health = RuntimeHealth(
        core=core,
        voice_agent=voice,
        supervisor_pid=399,
        observed_at=13.0,
    )

    assert health.to_dict() == {
        "core": core.to_dict(),
        "voice_agent": voice.to_dict(),
        "supervisor_pid": 399,
        "observed_at": 13.0,
    }


def test_health_state_contains_required_us124_states() -> None:
    assert {state.value for state in HealthState} >= {
        "starting",
        "ready",
        "paused",
        "degraded",
        "unavailable",
        "failed",
        "stopped",
    }


def test_background_paths_are_bounded_to_runtime_root(tmp_path: Path) -> None:
    root = tmp_path / "AskRex Data"
    paths = BackgroundPaths.from_runtime_root(root)

    assert paths.runtime_root == root.resolve()
    assert paths.state_dir == root.resolve() / "background"
    assert paths.core_endpoint_file == paths.state_dir / "core-endpoint.json"
    assert paths.health_file == paths.state_dir / "health.json"
    assert paths.stop_file == paths.state_dir / "stop.request"
    assert paths.supervisor_lock == paths.state_dir / "supervisor.lock"
    assert not paths.state_dir.exists(), "path resolution must not create runtime state"


def test_background_paths_include_voice_agent_health_file(tmp_path: Path) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    assert paths.voice_agent_health_file == paths.state_dir / "voice-agent-health.json"
