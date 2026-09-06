"""Content-free lifecycle types for the persistent Rex background runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HealthState(StrEnum):
    """Bounded component health states exposed by the background runtime."""

    STARTING = "starting"
    READY = "ready"
    PAUSED = "paused"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    """Public lifecycle state for one background component.

    This shape is intentionally metadata-only. Private conversation, identity,
    audio, memory, credential, and tool payloads must never be added here.
    """

    component: str
    state: HealthState
    detail_code: str | None
    observed_at: float
    pid: int | None

    def to_dict(self) -> dict[str, object]:
        """Return the stable wire representation used by status surfaces."""

        return {
            "component": self.component,
            "state": self.state.value,
            "detail_code": self.detail_code,
            "observed_at": self.observed_at,
            "pid": self.pid,
        }


@dataclass(frozen=True, slots=True)
class RuntimeHealth:
    """Aggregate content-free health for Core, Voice Agent, and supervisor."""

    core: ComponentHealth
    voice_agent: ComponentHealth
    supervisor_pid: int
    observed_at: float

    def to_dict(self) -> dict[str, object]:
        """Return the stable aggregate wire representation."""

        return {
            "core": self.core.to_dict(),
            "voice_agent": self.voice_agent.to_dict(),
            "supervisor_pid": self.supervisor_pid,
            "observed_at": self.observed_at,
        }
