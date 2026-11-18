from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.dev_orchestrator.cli import save_config
from scripts.dev_orchestrator.coordination_checks import (
    CoordinationCheckError,
    begin_checks,
    finish_checks,
    read_check_summary,
)
from scripts.dev_orchestrator.handoff import acknowledge_handoff, role_lease_lock_path
from scripts.dev_orchestrator.lifecycle import SupervisorLock
from scripts.dev_orchestrator.types import OrchestratorConfig, TaskItem
from scripts.dev_orchestrator.validation import run_iteration_validation
from tests.scripts.test_dev_orchestrator_safety import _git_repo


@pytest.fixture
def ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "coordination"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "check-coordination.ps1").write_text(
        "param([string]$Role)\nWrite-Output $Role\n", encoding="utf-8"
    )
    repo = tmp_path / "backend"
    _git_repo(repo)
    config = OrchestratorConfig(
        root,
        repo,
        tmp_path / "mobile",
        tmp_path / "frozen",
        observe_only=False,
        metadata={
            "iteration_validation": {
                "backend": {
                    "gates": [
                        {
                            "name": "fixture validation",
                            "command": [sys.executable, "-c", "print('PASS')"],
                        }
                    ]
                }
            }
        },
    )
    save_config(config)
    acknowledge_handoff(config, "backend", source="test")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] == "powershell.exe":
            return subprocess.CompletedProcess(
                command, 0, b"AskRex coordination check for: backend\nISSUE-EVIDENCE\n", b""
            )
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    assert run_iteration_validation(config, "backend", "B-CHECK", base_head=head, head=head).passed
    return config, TaskItem("B-CHECK", "run checks"), head


def test_check_packet_contains_actual_output_and_lease_proof(ready) -> None:
    config, task, head = ready
    begin_checks(config, "backend", task, head, head)
    finish_checks(config, "backend", task, head, head)
    packet = read_check_summary(config, "backend", task, head, head, strict=True)
    assert "ISSUE-EVIDENCE" in packet
    assert packet.count("no registered competing writer") == 2
    assert "lease_id=" in packet
    assert "Unregistered external writers are outside this observation" in packet
    manifest = json.loads(
        (config.coordination_root / "evidence/supervisor-checks/backend-B-CHECK.json").read_text()
    )
    assert manifest["initial"]["lease"]["lease_id"] == manifest["final"]["lease"]["lease_id"]


def test_dirty_checkout_cannot_produce_coordination_success(ready) -> None:
    config, task, head = ready
    (config.backend_root / "README.md").write_text("external mutation\n", encoding="utf-8")
    with pytest.raises(CoordinationCheckError, match="clean|changed"):
        begin_checks(config, "backend", task, head, head)


def test_active_writer_marker_blocks_check(ready) -> None:
    config, task, head = ready
    marker = config.coordination_root / "active-agents/backend.json"
    marker.parent.mkdir()
    marker.write_text("{}", encoding="utf-8")
    with pytest.raises(CoordinationCheckError, match="writer|activity"):
        begin_checks(config, "backend", task, head, head)


def test_unresolved_cloud_session_blocks_check(ready) -> None:
    config, task, head = ready
    marker = config.coordination_root / "cloud-sessions/backend.json"
    marker.parent.mkdir()
    marker.write_text('{"status":"running"}', encoding="utf-8")
    with pytest.raises(CoordinationCheckError, match="cloud|writer"):
        begin_checks(config, "backend", task, head, head)


def test_another_lease_lock_owner_blocks_check(ready) -> None:
    config, task, head = ready
    with SupervisorLock(role_lease_lock_path(config, "backend")):
        with pytest.raises(CoordinationCheckError, match="lock|running|lease"):
            begin_checks(config, "backend", task, head, head)


def test_changed_expected_revision_rejected(ready) -> None:
    config, task, head = ready
    with pytest.raises(CoordinationCheckError, match="revision|HEAD"):
        begin_checks(config, "backend", task, head, "f" * 40)


def test_tampered_capture_is_rejected(ready) -> None:
    config, task, head = ready
    begin_checks(config, "backend", task, head, head)
    finish_checks(config, "backend", task, head, head)
    path = next((config.coordination_root / "evidence/supervisor-checks").glob("*.stdout.txt"))
    path.write_bytes(b"tampered")
    with pytest.raises(CoordinationCheckError, match="hash mismatch"):
        read_check_summary(config, "backend", task, head, head, strict=True)


def test_missing_script_with_active_lease_fails_closed(ready) -> None:
    config, task, head = ready
    (config.coordination_root / "scripts/check-coordination.ps1").unlink()
    with pytest.raises(CoordinationCheckError, match="script"):
        begin_checks(config, "backend", task, head, head)


def test_new_attempt_invalidates_prior_final(ready) -> None:
    config, task, head = ready
    begin_checks(config, "backend", task, head, head)
    finish_checks(config, "backend", task, head, head)
    begin_checks(config, "backend", task, head, head)
    with pytest.raises(CoordinationCheckError, match="final"):
        read_check_summary(config, "backend", task, head, head, strict=True)


def test_recovery_captures_checks_without_reinvoking_completed_implementation(ready) -> None:
    from scripts.dev_orchestrator.storage import AtomicJsonStore
    from scripts.dev_orchestrator.supervisor import Supervisor
    from scripts.dev_orchestrator.types import WorkerState, WorkerStatus
    from tests.scripts.test_dev_orchestrator_supervisor import FakeInvoker

    config, task, head = ready
    invoker = FakeInvoker()
    supervisor = Supervisor(config, invoker)
    supervisor.save_state(
        WorkerState(
            role="backend", status=WorkerStatus.IMPLEMENTING, task=task, task_base_head=head
        )
    )
    store = AtomicJsonStore(config.coordination_root / "handoff/backend.json")
    lease = store.read()
    lease["pending_result"] = {
        "phase": "implement",
        "role": "backend",
        "task_id": task.task_id,
        "invocation_id": "recovered-1",
        "pre_head": head,
        "post_head": head,
        "result": {
            "outcome": "ready_for_review",
            "summary": "already completed",
            "next_action": "review",
            "needs_user": False,
            "blocker_reason": "",
            "task_id": task.task_id,
            "task_prompt": "",
            "role": "backend",
            "invocation_id": "recovered-1",
            "coordination_messages": [],
            "issue_updates": [],
        },
    }
    store.write(lease)
    supervisor._run_role("backend")
    assert supervisor.load_state("backend").status is WorkerStatus.REVIEWING
    assert invoker.calls == []
    assert "pending_result" not in store.read()
    packet = read_check_summary(config, "backend", task, head, head, strict=True)
    assert "recovered-checkpoint-validation" in packet
    assert "initial: exit=0" in packet and "final: exit=0" in packet


def test_validation_receipt_replacement_invalidates_packet(ready) -> None:
    config, task, head = ready
    begin_checks(config, "backend", task, head, head)
    finish_checks(config, "backend", task, head, head)
    receipt = config.coordination_root / "validation/backend-B-CHECK.json"
    receipt.write_bytes(receipt.read_bytes() + b"\n")
    with pytest.raises(CoordinationCheckError, match="validation receipt hash mismatch"):
        read_check_summary(config, "backend", task, head, head, strict=True)


def test_recovery_check_failure_preserves_pending_implementation(ready, monkeypatch) -> None:
    from scripts.dev_orchestrator.storage import AtomicJsonStore
    from scripts.dev_orchestrator.supervisor import Supervisor
    from scripts.dev_orchestrator.types import WorkerState, WorkerStatus
    from tests.scripts.test_dev_orchestrator_supervisor import FakeInvoker

    config, task, head = ready
    invoker = FakeInvoker()
    supervisor = Supervisor(config, invoker)
    state = WorkerState(
        role="backend", status=WorkerStatus.IMPLEMENTING, task=task, task_base_head=head
    )
    supervisor.save_state(state)
    store = AtomicJsonStore(config.coordination_root / "handoff/backend.json")
    lease = store.read()
    lease["pending_result"] = {
        "phase": "implement",
        "role": "backend",
        "task_id": task.task_id,
        "invocation_id": "recovered-2",
        "pre_head": head,
        "post_head": head,
        "result": {
            "outcome": "ready_for_review",
            "summary": "already completed",
            "next_action": "review",
            "needs_user": False,
            "blocker_reason": "",
            "task_id": task.task_id,
            "task_prompt": "",
            "role": "backend",
            "invocation_id": "recovered-2",
            "coordination_messages": [],
            "issue_updates": [],
        },
    }
    store.write(lease)

    def fail_final(*args, **kwargs):
        raise CoordinationCheckError("final check unavailable")

    monkeypatch.setattr("scripts.dev_orchestrator.supervisor.finish_checks", fail_final)
    supervisor._run_role("backend")
    assert supervisor.load_state("backend").status is WorkerStatus.BLOCKED_SYSTEM
    assert store.read()["pending_result"]["invocation_id"] == "recovered-2"
    assert invoker.calls == []


def test_failed_check_preserves_diagnostics_without_success(ready, monkeypatch) -> None:
    config, task, head = ready
    previous = subprocess.run

    def fail_check(command, **kwargs):
        if command[0] == "powershell.exe":
            return subprocess.CompletedProcess(command, 2, b"partial output", b"check failed")
        return previous(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_check)
    with pytest.raises(CoordinationCheckError, match="exit code 2"):
        begin_checks(config, "backend", task, head, head)
    captures = list((config.coordination_root / "evidence/supervisor-checks").glob("*.stderr.txt"))
    assert len(captures) == 1
    assert captures[0].read_text() == "check failed"
    assert not (
        config.coordination_root / "evidence/supervisor-checks/backend-B-CHECK.json"
    ).exists()


def test_mutation_during_check_fails_closed(ready, monkeypatch) -> None:
    config, task, head = ready
    previous = subprocess.run

    def mutate_during_check(command, **kwargs):
        result = previous(command, **kwargs)
        if command[0] == "powershell.exe":
            (config.backend_root / "README.md").write_text("competing mutation\n", encoding="utf-8")
        return result

    monkeypatch.setattr(subprocess, "run", mutate_during_check)
    with pytest.raises(CoordinationCheckError, match="changed|clean"):
        begin_checks(config, "backend", task, head, head)


@pytest.mark.parametrize("adopted", [False, True])
def test_existing_cloud_work_is_polled_before_post_adoption_checks(ready, adopted) -> None:
    from scripts.dev_orchestrator.supervisor import Supervisor
    from scripts.dev_orchestrator.types import AgentResult, WorkerState, WorkerStatus

    config, task, head = ready
    cloud = config.coordination_root / "cloud-sessions/backend.json"
    cloud.parent.mkdir()
    cloud.write_text('{"status":"running"}', encoding="utf-8")

    class CloudInvoker:
        polls = 0

        def implement(self, *args, **kwargs):
            raise AssertionError("must not start a local model while cloud work is registered")

        def poll_existing_cloud(self, role, current_task, context):
            assert role == "backend" and current_task == task and cloud.exists()
            self.polls += 1
            if adopted:
                cloud.unlink()
            return AgentResult(
                "ready_for_review" if adopted else "continue", "cloud state", "continue"
            )

    invoker = CloudInvoker()
    supervisor = Supervisor(config, invoker)
    supervisor.save_state(
        WorkerState(
            role="backend", status=WorkerStatus.IMPLEMENTING, task=task, task_base_head=head
        )
    )
    supervisor._run_role("backend")
    assert invoker.polls == 1
    state = supervisor.load_state("backend")
    if adopted:
        assert state.status is WorkerStatus.REVIEWING
        packet = read_check_summary(config, "backend", task, head, head, strict=True)
        assert "recovered-checkpoint-validation" in packet
    else:
        assert state.status is WorkerStatus.IMPLEMENTING
        assert cloud.exists()
        assert not (
            config.coordination_root / "evidence/supervisor-checks/backend-B-CHECK.json"
        ).exists()
