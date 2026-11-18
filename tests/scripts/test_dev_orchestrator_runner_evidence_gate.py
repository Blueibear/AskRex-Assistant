from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import runner
from scripts.dev_orchestrator.evidence import EvidenceError, ReviewEvidenceBundle
from scripts.dev_orchestrator.runner import AgentInvocationError, CliAgentInvoker
from scripts.dev_orchestrator.types import TaskItem, WorkerState


def _reviewer(tmp_path):
    invoker = object.__new__(CliAgentInvoker)
    invoker.config = SimpleNamespace(
        backend_root=tmp_path,
        mobile_root=tmp_path,
        frozen_worktree=tmp_path / "frozen",
        coordination_root=tmp_path / "coordination",
    )
    return invoker


def test_codex_review_rejects_missing_required_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    invoker = _reviewer(tmp_path)
    task = TaskItem("REVIEW-1", "review work")
    state = WorkerState(
        role="backend",
        task=task,
        task_base_head="1" * 40,
    )

    def fail_evidence(*args, **kwargs):
        raise EvidenceError("missing coordination receipt")

    monkeypatch.setattr(runner, "build_review_evidence", fail_evidence)
    with pytest.raises(AgentInvocationError, match="missing coordination receipt") as exc:
        invoker.review("backend", state, task, "context", "test-model")
    assert exc.value.kind == "incomplete_evidence"


def test_codex_review_rejects_truncated_critical_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    invoker = _reviewer(tmp_path)
    task = TaskItem("REVIEW-2", "review work")
    state = WorkerState(role="backend", task=task, task_base_head="1" * 40)
    monkeypatch.setattr(
        runner,
        "build_review_evidence",
        lambda *args, **kwargs: ReviewEvidenceBundle(
            text="incomplete",
            truncated=True,
            truncation_reasons=("coordination_checks",),
            base_head="1" * 40,
            head="2" * 40,
        ),
    )
    with pytest.raises(AgentInvocationError, match="coordination_checks"):
        invoker.review("backend", state, task, "context", "test-model")


def test_codex_review_uses_complete_bounded_evidence_without_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from scripts.dev_orchestrator.types import AgentResult

    invoker = _reviewer(tmp_path)
    task = TaskItem("REVIEW-OK", "review work")
    state = WorkerState(role="backend", task=task, task_base_head="1" * 40)
    monkeypatch.setattr(
        runner,
        "build_review_evidence",
        lambda *args, **kwargs: ReviewEvidenceBundle(
            text="EXACT-VALIDATED-BUNDLE",
            truncated=False,
            truncation_reasons=(),
            base_head="1" * 40,
            head="2" * 40,
        ),
    )
    dispatched = []

    def fake_execute(command, *args, **kwargs):
        dispatched.append(command)
        return AgentResult("pass", "test result", "")

    invoker._execute_agent = fake_execute
    invoker._finish = lambda provider, outcome: outcome
    reviewed = invoker.review("backend", state, task, "raw context", "gpt-5.6-terra")
    assert reviewed.outcome == "pass"
    assert len(dispatched) == 1
    assert "EXACT-VALIDATED-BUNDLE" in dispatched[0][-1]
    assert "raw context" not in dispatched[0][-1]


def test_codex_review_requires_base_head(tmp_path) -> None:
    invoker = _reviewer(tmp_path)
    task = TaskItem("REVIEW-3", "review work")
    state = WorkerState(role="backend", task=task)
    with pytest.raises(AgentInvocationError, match="captured base revision"):
        invoker.review("backend", state, task, "context", "test-model")
