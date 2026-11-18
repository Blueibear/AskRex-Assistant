from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import coordination_checks, runner, worker_context
from scripts.dev_orchestrator.cli import save_config
from scripts.dev_orchestrator.completed_tasks import CompletedTaskLedger
from scripts.dev_orchestrator.handoff import repo_snapshot
from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import (
    AgentResult,
    CoordinationMessage,
    TaskItem,
    WorkerState,
    WorkerStatus,
)
from tests.scripts.test_dev_orchestrator_routing import _safe_config

PRIOR = "backend-fct-01-contract-review-001"
ACK = (
    "EXACT-PRIOR-ACK: accepts the optional v1 transport shape "
    "claims=[text,kind,source_id?], citations=[source_id,title,locator?]. "
    "Transport acceptance only; no evidence UI commitment or physical verification."
)


@pytest.fixture
def contract_history(tmp_path, monkeypatch):
    monkeypatch.setenv("ASKREX_PROVIDER_POLICY", "subscription_only")
    config, repo, _ = _safe_config(tmp_path)
    config = replace(
        config,
        observe_only=False,
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "gates": [
                        {
                            "name": "canonical fixture",
                            "command": [sys.executable, "-c", "print('CANONICAL-PASS')"],
                            "cwd": ".",
                        }
                    ]
                },
            }
        },
    )
    save_config(config)
    root = config.coordination_root
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts/check-coordination.ps1").write_text("Write-Output 'check'\n", encoding="utf-8")
    monkeypatch.setattr(
        coordination_checks,
        "_check_command",
        lambda _config, role: [sys.executable, "-c", "print('CHECK')"],
    )
    task = TaskItem(PRIOR, "Review the exact optional evidence transport contract.")
    observed = SimpleNamespace(
        config=config,
        repo=repo,
        root=root,
        task=task,
        prompts=[],
        outcome="ready_for_review",
        body=ACK,
        related=PRIOR,
    )
    real_run = runner.run_command

    def model(command, cwd, timeout_seconds=1800, **kwargs):
        assert command[0].lower().startswith("codex")
        prompt = kwargs["stdin_text"]
        observed.prompts.append(prompt)
        invocation = re.search(r"AskRex-Orchestrator-Invocation-ID: ([0-9a-f-]+)", prompt).group(1)
        result = AgentResult(
            observed.outcome,
            "Bounded contract work; preserve the exact stated scope.",
            "Continue only through normal host gates.",
            role="backend",
            task_id=observed.task.task_id,
            invocation_id=invocation,
            blocker_reason=(
                "The named earlier contract correspondence is needed."
                if observed.outcome == "blocked_system"
                else ""
            ),
            coordination_messages=(
                (CoordinationMessage("mobile", "normal", observed.related, False, observed.body),)
                if observed.body
                else ()
            ),
        )
        return real_run(
            [sys.executable, "-c", "print(" + repr(json.dumps(asdict(result))) + ")"],
            cwd,
            timeout_seconds,
            **kwargs,
        )

    monkeypatch.setattr(runner, "run_command", model)
    invoker = runner.CliAgentInvoker(config)
    supervisor = Supervisor(config, invoker)
    head, dirty = repo_snapshot(repo)
    assert not dirty
    supervisor.save_state(
        WorkerState("backend", status=WorkerStatus.IMPLEMENTING, task=task, task_base_head=head)
    )
    supervisor.run_role("backend")
    assert supervisor.load_state("backend").status is WorkerStatus.REVIEWING
    observed.outcome, observed.body = "pass", ""
    supervisor.run_role("backend")
    record = CompletedTaskLedger(root).records("backend")[-1]
    assert record["task_id"] == PRIOR and record["source"] == "independent_review"
    observed.task = TaskItem(
        "backend-fct-01-evidence-client-001",
        f"Use the exact correspondence from completed task {PRIOR}. Preserve its limits.",
    )
    observed.outcome = "blocked_system"
    state = WorkerState(
        "backend", status=WorkerStatus.IMPLEMENTING, task=observed.task, task_base_head=head
    )
    supervisor.save_state(state)
    observed.supervisor, observed.invoker, observed.head = supervisor, invoker, head
    return observed


def test_actual_worker_receives_exact_correspondence_from_referenced_task(contract_history):
    history = contract_history
    history.outcome = "ready_for_review"
    history.supervisor.run_role("backend")
    prompt = history.prompts[-1]
    assert ACK in prompt
    assert "explicitly referenced completed task" in prompt
    assert "No broader agreement or permission is inferred" in prompt
    state = history.supervisor.load_state("backend")
    context = worker_context.build_worker_context(history.config, "backend", state, history.head)
    assert ACK in context.text.critical_text
    assert state.status is WorkerStatus.REVIEWING
    history.outcome = "pass"
    history.supervisor.run_role("backend")
    assert history.prompts[-1].startswith("Independently review AskRex backend")
    assert ACK in history.prompts[-1]
    assert "No broader agreement or permission is inferred" in history.prompts[-1]
    assert (
        CompletedTaskLedger(history.root).records("backend")[-1]["task_id"] == history.task.task_id
    )


@pytest.mark.parametrize("header_mode", ["none", "prior", "invalid"])
def test_current_task_message_cannot_self_wake_by_naming_prior_task(contract_history, header_mode):
    history = contract_history
    before = history.supervisor.load_state("backend")
    context = worker_context.build_worker_context(history.config, "backend", before, history.head)
    baseline = context.fingerprint("backend", "implement", before, history.head)
    history.body = "CURRENT-TASK-REPORT: still evaluating the earlier correspondence."
    history.related = PRIOR
    if header_mode != "none":
        prior_message = next((history.root / "mailbox/mobile").glob("MSG-*.md")).read_text(
            encoding="utf-8"
        )
        prior_invocation = re.search(r"(?m)^Invocation: ([0-9a-f-]+)$", prior_message).group(1)
        history.related += "\nInvocation: " + (
            prior_invocation if header_mode == "prior" else "not-an-invocation"
        )
    history.supervisor.run_role("backend")
    state = history.supervisor.load_state("backend")
    later = worker_context.build_worker_context(history.config, "backend", state, history.head)
    assert later.fingerprint("backend", "implement", state, history.head) == baseline
    count = len(history.prompts)
    Supervisor(history.config, history.invoker).run_role("backend")
    assert len(history.prompts) == count
    assert ACK in later.text


def test_old_missing_history_hold_wakes_once_on_new_bound_history(contract_history, monkeypatch):
    history = contract_history
    # Model the old selector's omission while keeping actual admission/input/result
    # capture and dependency persistence on the real production boundaries.
    with monkeypatch.context() as old:
        old.setattr(worker_context, "_referenced_task_sources", lambda *args: [], raising=False)
        history.supervisor.run_role("backend")
    assert ACK not in history.prompts[-1]
    count = len(history.prompts)
    Supervisor(history.config, history.invoker).run_role("backend")
    assert len(history.prompts) == count + 1
    assert ACK in history.prompts[-1]
    Supervisor(history.config, history.invoker).run_role("backend")
    assert len(history.prompts) == count + 1
