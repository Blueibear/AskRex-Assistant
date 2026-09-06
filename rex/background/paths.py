"""Canonical filesystem paths for the persistent Rex background runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BackgroundPaths:
    """Resolved runtime paths shared by Core, Voice Agent, and supervisor."""

    runtime_root: Path
    state_dir: Path
    core_endpoint_file: Path
    voice_agent_health_file: Path
    health_file: Path
    stop_file: Path
    supervisor_lock: Path

    @classmethod
    def from_runtime_root(cls, runtime_root: Path) -> BackgroundPaths:
        """Resolve canonical paths without creating runtime state as a side effect."""

        root = runtime_root.expanduser().resolve()
        state_dir = root / "background"
        return cls(
            runtime_root=root,
            state_dir=state_dir,
            core_endpoint_file=state_dir / "core-endpoint.json",
            voice_agent_health_file=state_dir / "voice-agent-health.json",
            health_file=state_dir / "health.json",
            stop_file=state_dir / "stop.request",
            supervisor_lock=state_dir / "supervisor.lock",
        )
