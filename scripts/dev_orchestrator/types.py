from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any


class WorkerStatus(StrEnum):
    IDLE = "idle"
    PLANNING = "planning"
    IMPLEMENTING = "implementing"
    REVIEWING = "reviewing"
    BLOCKED_USER = "blocked_user"
    BLOCKED_SYSTEM = "blocked_system"
    DONE = "done"


@dataclass(frozen=True)
class CoordinationMessage:
    to: str
    priority: str
    related: str
    needs_response: bool
    body: str


@dataclass(frozen=True)
class IssueUpdate:
    issue_id: str
    status: str
    note: str


@dataclass(frozen=True)
class AgentResult:
    outcome: str
    summary: str
    next_action: str
    needs_user: bool = False
    blocker_reason: str = ""
    task_id: str = ""
    task_prompt: str = ""
    role: str = ""
    invocation_id: str = ""
    coordination_messages: tuple[CoordinationMessage, ...] = ()
    issue_updates: tuple[IssueUpdate, ...] = ()


@dataclass(frozen=True)
class TaskItem:
    task_id: str
    prompt: str
    feedback: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkerState:
    role: str
    status: WorkerStatus = WorkerStatus.IDLE
    task: TaskItem | None = None
    task_base_head: str = ""
    iteration: int = 0
    implementation_failures: int = 0
    review_failures: int = 0
    blocked_reason: str = ""
    blocker_kind: str = ""
    resume_status: WorkerStatus | None = None
    claude_session_id: str = ""
    codex_session_id: str = ""
    last_result_invocation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["resume_status"] = self.resume_status.value if self.resume_status else None
        return data


@dataclass(frozen=True)
class OrchestratorConfig:
    coordination_root: Path
    backend_root: Path | None = None
    mobile_root: Path | None = None
    frozen_worktree: Path | None = None
    observe_only: bool = True
    banked_resets_remaining: int = 3
    reserve_last_reset: bool = True
    poll_seconds: int = 60
    implementation_escalation_after: int = 2
    review_escalation_after: int = 2
    astra_adjudication_after: int = 4
    openai_worker_enabled: bool = False
    openai_review_model: str = "gpt-5.6-terra"
    openai_escalation_model: str = "gpt-5.6-sol"
    openai_planning_model: str = "gpt-5.6-sol"
    openai_astra_model: str = "gpt-6-astra"
    openai_astra_enabled: bool = True
    openai_monthly_budget_usd: Decimal = Decimal("30.00")
    openai_project_id: str = ""
    openai_project_hard_limit_confirmed: bool = False
    openai_timeout_seconds: int = 120
    openai_max_input_chars: int = 120_000
    openai_max_output_tokens: int = 4_000
    openai_max_calls_per_cycle: int = 4
    openai_max_astra_calls_per_escalation: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def default(cls, coordination_root: Path) -> OrchestratorConfig:
        return cls(coordination_root=coordination_root)

    def with_worker_root(
        self, role: str, root: Path, *, frozen_worktree: Path | None = None
    ) -> OrchestratorConfig:
        frozen = frozen_worktree or self.frozen_worktree
        if frozen and root.resolve() == frozen.resolve():
            raise ValueError("frozen worktree cannot be a development worker root")
        if role == "backend":
            return replace(self, backend_root=root, frozen_worktree=frozen)
        if role == "mobile":
            return replace(self, mobile_root=root, frozen_worktree=frozen)
        raise ValueError(f"unsupported worker role: {role}")
