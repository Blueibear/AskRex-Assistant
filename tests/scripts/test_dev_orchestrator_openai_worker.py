from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from scripts.dev_orchestrator.handoff import HandoffRequired, read_pending_result
from scripts.dev_orchestrator.openai_budget import (
    BudgetReservation,
    OpenAIBudgetExceeded,
    OpenAIUsage,
)
from scripts.dev_orchestrator.openai_transport import (
    OpenAITransportError,
    OpenAITransportResponse,
    PreparedOpenAIRequest,
)
from scripts.dev_orchestrator.runner import AgentInvocationError
from scripts.dev_orchestrator.types import TaskItem, WorkerState, WorkerStatus
from scripts.dev_orchestrator.validation import run_iteration_validation
from tests.scripts.test_dev_orchestrator_lease import _accept_test_lease, _config


def _leased_review(tmp_path: Path):
    config = _config(tmp_path)
    config = replace(
        config,
        openai_worker_enabled=True,
        openai_project_id="proj-test",
        openai_project_hard_limit_confirmed=True,
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "gates": [
                        {
                            "name": "worker fixture gate",
                            "command": [sys.executable, "-c", "print('OK')"],
                        }
                    ]
                },
            }
        },
    )
    repo = config.backend_root
    assert repo is not None
    _accept_test_lease(config, "backend")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    task = TaskItem("B-OPENAI", "Review the backend change")
    state = WorkerState(
        role="backend",
        status=WorkerStatus.REVIEWING,
        task=task,
        task_base_head=head,
    )
    report = run_iteration_validation(
        config,
        "backend",
        task.task_id,
        base_head=head,
        head=head,
    )
    assert report.passed
    return config, repo, state, task


def _agent_output(
    invocation_id: str,
    *,
    outcome: str = "pass",
    role: str = "backend",
    task_id: str = "B-OPENAI",
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "summary": "reviewed",
        "next_action": "continue",
        "needs_user": False,
        "blocker_reason": "",
        "task_id": task_id,
        "task_prompt": "",
        "role": role,
        "invocation_id": invocation_id,
        "coordination_messages": [],
        "issue_updates": [],
    }


class _FakeBudget:
    def __init__(self, events: list[str], reserve_error: Exception | None = None) -> None:
        self.events = events
        self.reserve_error = reserve_error
        self.uncertain: list[str] = []
        self.reconciled: list[tuple[str, OpenAIUsage]] = []
        self.reservations: list[dict[str, Any]] = []

    def reserve(
        self,
        *,
        model: str,
        input_token_ceiling: int,
        output_token_ceiling: int,
        purpose: str = "",
        episode_key: str = "",
    ) -> BudgetReservation:
        self.events.append("reserve")
        self.reservations.append({"model": model, "purpose": purpose, "episode_key": episode_key})
        if self.reserve_error is not None:
            raise self.reserve_error
        assert input_token_ceiling > 0
        assert output_token_ceiling > 0
        return BudgetReservation("reservation-1", "2026-09", model, Decimal("1.00"))

    def reconcile(self, reservation_id: str, usage: OpenAIUsage) -> Decimal:
        self.events.append("reconcile")
        self.reconciled.append((reservation_id, usage))
        return Decimal("0.01")

    def mark_uncertain(self, reservation_id: str) -> None:
        self.events.append("uncertain")
        self.uncertain.append(reservation_id)


class _FakeTransport:
    def __init__(
        self,
        events: list[str],
        output: dict[str, Any],
        *,
        send_error: OpenAITransportError | None = None,
        on_send: Callable[[], None] | None = None,
    ) -> None:
        self.events = events
        self.output = output
        self.send_error = send_error
        self.on_send = on_send
        self.input_text = ""

    def prepare(self, **kwargs: Any) -> PreparedOpenAIRequest:
        self.events.append("prepare")
        self.input_text = str(kwargs["input_text"])
        return PreparedOpenAIRequest(
            body=b"{}",
            input_token_ceiling=max(1, len(self.input_text)),
            model=str(kwargs["model"]),
            max_output_tokens=int(kwargs["max_output_tokens"]),
        )

    def send(self, prepared: PreparedOpenAIRequest) -> OpenAITransportResponse:
        self.events.append("send")
        if self.on_send is not None:
            self.on_send()
        if self.send_error is not None:
            raise self.send_error
        return OpenAITransportResponse(
            output=self.output,
            usage=OpenAIUsage(input_tokens=100, output_tokens=25),
            request_id="req-1",
        )


def _worker(config, transport: _FakeTransport, budget: _FakeBudget, invocation_id: str):
    from scripts.dev_orchestrator.openai_worker import OpenAIModelWorker

    return OpenAIModelWorker(
        config,
        transport=transport,
        budget=budget,
        invocation_id_factory=lambda: invocation_id,
    )


def test_review_reserves_before_send_reconciles_and_persists_pending_result(
    tmp_path: Path,
) -> None:
    config, _repo, state, task = _leased_review(tmp_path)
    events: list[str] = []
    budget = _FakeBudget(events)
    transport = _FakeTransport(events, _agent_output("inv-1"))
    worker = _worker(config, transport, budget, "inv-1")

    result = worker.review("backend", state, task, "coordination", "gpt-5.6-terra")

    assert events == ["prepare", "reserve", "send", "reconcile"]
    assert result.outcome == "pass"
    assert result.invocation_id == "inv-1"
    pending = read_pending_result(config, "backend")
    assert pending is not None
    assert pending["phase"] == "review"
    assert pending["task_id"] == task.task_id
    assert pending["invocation_id"] == "inv-1"
    assert pending["result"]["outcome"] == "pass"
    assert budget.reconciled[0][0] == "reservation-1"
    assert "## task" in transport.input_text


def test_review_does_not_send_when_budget_reservation_fails(tmp_path: Path) -> None:
    config, _repo, state, task = _leased_review(tmp_path)
    events: list[str] = []
    budget = _FakeBudget(events, OpenAIBudgetExceeded("cap"))
    transport = _FakeTransport(events, _agent_output("inv-budget"))
    worker = _worker(config, transport, budget, "inv-budget")

    with pytest.raises(AgentInvocationError) as caught:
        worker.review("backend", state, task, "coordination", "gpt-5.6-terra")

    assert caught.value.provider == "openai"
    assert caught.value.kind == "budget"
    assert events == ["prepare", "reserve"]
    assert read_pending_result(config, "backend") is None


@pytest.mark.parametrize("category", ["timeout", "transient", "invalid_output"])
def test_review_marks_reserved_spend_uncertain_after_transport_failure(
    tmp_path: Path,
    category: str,
) -> None:
    config, _repo, state, task = _leased_review(tmp_path)
    events: list[str] = []
    budget = _FakeBudget(events)
    transport = _FakeTransport(
        events,
        _agent_output("inv-uncertain"),
        send_error=OpenAITransportError(category),
    )
    worker = _worker(config, transport, budget, "inv-uncertain")

    with pytest.raises(AgentInvocationError) as caught:
        worker.review("backend", state, task, "coordination", "gpt-5.6-terra")

    assert caught.value.kind == category
    assert events == ["prepare", "reserve", "send", "uncertain"]
    assert budget.uncertain == ["reservation-1"]
    assert read_pending_result(config, "backend") is None


def test_review_reconciles_before_rejecting_binding_mismatch(tmp_path: Path) -> None:
    config, _repo, state, task = _leased_review(tmp_path)
    events: list[str] = []
    budget = _FakeBudget(events)
    transport = _FakeTransport(
        events,
        _agent_output("wrong-invocation"),
    )
    worker = _worker(config, transport, budget, "expected-invocation")

    with pytest.raises(AgentInvocationError) as caught:
        worker.review("backend", state, task, "coordination", "gpt-5.6-terra")

    assert caught.value.kind == "invalid_output"
    assert events == ["prepare", "reserve", "send", "reconcile"]
    assert read_pending_result(config, "backend") is None


def test_review_rejects_repository_change_during_read_only_send(tmp_path: Path) -> None:
    config, repo, state, task = _leased_review(tmp_path)
    events: list[str] = []
    budget = _FakeBudget(events)

    def mutate_repo() -> None:
        (repo / "smuggled.txt").write_text("delta\n", encoding="utf-8")
        subprocess.run(["git", "add", "smuggled.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "smuggled delta"], cwd=repo, check=True)

    transport = _FakeTransport(
        events,
        _agent_output("inv-smuggle"),
        on_send=mutate_repo,
    )
    worker = _worker(config, transport, budget, "inv-smuggle")

    with pytest.raises(HandoffRequired, match="read-only OpenAI|HEAD changed"):
        worker.review("backend", state, task, "coordination", "gpt-5.6-terra")

    assert events == ["prepare", "reserve", "send", "reconcile"]
    assert read_pending_result(config, "backend") is None


@pytest.mark.parametrize("outcome", ["pass", "changes_required"])
def test_truncated_review_evidence_requests_complete_fallback_review(
    tmp_path: Path, outcome: str
) -> None:
    config, _repo, state, task = _leased_review(tmp_path)
    task = TaskItem(task.task_id, "Review " + ("x" * 9_000))
    events: list[str] = []
    budget = _FakeBudget(events)
    transport = _FakeTransport(events, _agent_output("inv-truncated", outcome=outcome))
    worker = _worker(config, transport, budget, "inv-truncated")

    with pytest.raises(AgentInvocationError) as caught:
        worker.review(
            "backend",
            state,
            task,
            "coordination",
            "gpt-5.6-terra",
        )

    assert caught.value.provider == "openai"
    assert caught.value.kind == "incomplete_evidence"
    assert "truncated" in caught.value.detail.lower()
    assert events == ["prepare", "reserve", "send", "reconcile"]
    assert read_pending_result(config, "backend") is None


def test_lead_plan_uses_bounded_context_without_review_receipt(tmp_path: Path) -> None:
    config = replace(
        _config(tmp_path),
        openai_worker_enabled=True,
        openai_project_id="proj-test",
        openai_project_hard_limit_confirmed=True,
    )
    _accept_test_lease(config, "backend")
    events: list[str] = []
    budget = _FakeBudget(events)
    transport = _FakeTransport(
        events,
        _agent_output("inv-plan", outcome="assign", task_id=""),
    )
    worker = _worker(config, transport, budget, "inv-plan")
    state = WorkerState(role="backend", status=WorkerStatus.PLANNING)

    result = worker.lead(
        "backend",
        state,
        None,
        "coordination context",
        phase="plan",
        model="gpt-5.6-sol",
    )

    assert result.outcome == "assign"
    assert events == ["prepare", "reserve", "send", "reconcile"]
    assert "coordination context" in transport.input_text


def test_review_evidence_does_not_disclose_live_worktree_path(tmp_path: Path) -> None:
    config, repo, state, task = _leased_review(tmp_path)
    events: list[str] = []
    budget = _FakeBudget(events)
    transport = _FakeTransport(events, _agent_output("inv-private-path"))
    worker = _worker(config, transport, budget, "inv-private-path")

    worker.review("backend", state, task, "coordination", "gpt-5.6-terra")

    assert str(repo) not in transport.input_text


def test_astra_lead_tags_actual_budget_reservation_with_episode(tmp_path: Path) -> None:
    config = replace(
        _config(tmp_path),
        openai_worker_enabled=True,
        openai_project_id="proj-test",
        openai_project_hard_limit_confirmed=True,
    )
    _accept_test_lease(config, "backend")
    events: list[str] = []
    budget = _FakeBudget(events)
    transport = _FakeTransport(
        events,
        _agent_output("inv-astra", outcome="assign", task_id="B-1"),
    )
    worker = _worker(config, transport, budget, "inv-astra")
    task = TaskItem("B-1", "Adjudicate")
    state = WorkerState(role="backend", task=task, task_base_head="base", implementation_failures=4)

    worker.lead(
        "backend",
        state,
        task,
        "coordination",
        phase="adjudicate",
        model="gpt-6-astra",
        budget_purpose="astra_adjudication",
        episode_key="episode-1",
    )

    assert budget.reservations == [
        {"model": "gpt-6-astra", "purpose": "astra_adjudication", "episode_key": "episode-1"}
    ]
