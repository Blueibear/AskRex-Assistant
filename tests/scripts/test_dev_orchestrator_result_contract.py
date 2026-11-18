from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import coordination, coordination_checks, runner
from scripts.dev_orchestrator import supervisor as supervisor_module
from scripts.dev_orchestrator.cli import save_config
from scripts.dev_orchestrator.handoff import read_pending_result, repo_snapshot
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import (
    AgentResult,
    CoordinationMessage,
    IssueUpdate,
    TaskItem,
    WorkerState,
    WorkerStatus,
)
from scripts.dev_orchestrator.validation import run_iteration_validation
from tests.scripts.test_dev_orchestrator_final_review import _claim
from tests.scripts.test_dev_orchestrator_final_review import final_checkpoint as final_checkpoint
from tests.scripts.test_dev_orchestrator_routing import _safe_config


@pytest.fixture
def contract_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ASKREX_PROVIDER_POLICY", "subscription_only")
    config, repo, _ = _safe_config(tmp_path)
    task = TaskItem(
        "backend-security-exact-head-review-aa1f43f7",
        "Independently review SECURITY-REPO-20261111. If the review passes, "
        "request fixed-needs-retest only where the protocol permits, and send testing "
        "and supervisor bounded evidence. Do not expand authority or scope.",
    )
    gate = [sys.executable, "-c", "print('CANONICAL-PASS')"]
    config = replace(
        config,
        observe_only=False,
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "overrides": [
                        {
                            "task_prefix": task.task_id,
                            "gates": [{"name": "canonical", "command": gate, "cwd": "."}],
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
    (root / "issues").mkdir(exist_ok=True)
    issue = root / "issues/SECURITY-REPO-20261111.md"
    issue.write_text("Owner: backend\nStatus: open\nSecurity review scope.\n", encoding="utf-8")
    monkeypatch.setattr(
        coordination_checks,
        "_check_command",
        lambda _config, role: [sys.executable, "-c", "import sys; print(sys.argv[1])", role],
    )
    head, dirty = repo_snapshot(repo)
    assert not dirty
    coordination_checks.begin_checks(config, "backend", task, base_head=head, initial_head=head)
    report = run_iteration_validation(config, "backend", task.task_id, base_head=head, head=head)
    assert report.passed, report.feedback
    coordination_checks.finish_checks(config, "backend", task, base_head=head, head=head)
    receipt = root / "validation" / f"backend-{task.task_id}.json"
    observed = SimpleNamespace(
        config=config,
        repo=repo,
        task=task,
        head=head,
        prompts=[],
        results=[],
        schemas=[],
        reject=True,
        bad_outcome=False,
        plan_outcome="pass",
        issue_id="SECURITY-REPO-20261111",
        after_model=None,
        receipt=receipt,
        receipt_bytes=receipt.read_bytes(),
        issue=issue,
        issue_bytes=issue.read_bytes(),
    )
    real_run = runner.run_command

    def model(command, cwd, timeout_seconds=1800, **kwargs):
        assert Path(command[0]).stem.casefold() == "codex"
        prompt = kwargs["stdin_text"]
        reviewing = prompt.startswith("Independently review")
        assert reviewing or prompt.startswith("Act as the read-only lead engineer")
        observed.prompts.append(prompt)
        schema = json.loads(
            Path(command[command.index("--output-schema") + 1]).read_text(encoding="utf-8")
        )
        observed.schemas.append(schema)
        invocation = re.search(r"AskRex-Orchestrator-Invocation-ID: ([0-9a-f-]+)", prompt).group(1)
        result = AgentResult(
            (
                ("ready_for_review" if observed.bad_outcome else "pass")
                if reviewing
                else observed.plan_outcome
            ),
            "Real fixture review passed",
            "Report the bounded security review",
            role="backend",
            task_id=observed.task.task_id if reviewing else "",
            invocation_id=invocation,
            issue_updates=(
                (IssueUpdate(observed.issue_id, "fixed-needs-retest", "Review passed"),)
                if observed.reject and reviewing
                else ()
            ),
            coordination_messages=(
                (
                    CoordinationMessage(
                        "supervisor",
                        "normal",
                        observed.task.task_id,
                        False,
                        "REJECTED-PUBLICATION" if observed.reject else "CORRECTED-PUBLICATION",
                    ),
                )
                if reviewing
                else ()
            ),
        )
        observed.results.append(result)
        process_result = real_run(
            [sys.executable, "-c", "print(" + repr(json.dumps(asdict(result))) + ")"],
            cwd,
            timeout_seconds,
            **kwargs,
        )
        if observed.after_model is not None:
            observed.after_model()
        return process_result

    monkeypatch.setattr(runner, "run_command", model)
    observed.invoker = runner.CliAgentInvoker(config)
    observed.supervisor = Supervisor(config, observed.invoker)
    observed.supervisor.save_state(
        WorkerState(
            "backend",
            status=WorkerStatus.REVIEWING,
            task=task,
            task_base_head=head,
            iteration=452,
            review_failures=27,
        )
    )
    return observed


def _rejections(checkpoint):
    return sorted((checkpoint.config.coordination_root / "rejected-results").glob("*.json"))


def _messages(checkpoint):
    return sorted((checkpoint.config.coordination_root / "mailbox").rglob("*.md"))


def _assert_unpublished(checkpoint):
    assert not _messages(checkpoint)
    assert checkpoint.issue.read_bytes() == checkpoint.issue_bytes
    assert checkpoint.receipt.read_bytes() == checkpoint.receipt_bytes
    assert repo_snapshot(checkpoint.repo) == (checkpoint.head, "")


def test_rejected_security_pass_retries_with_current_contract_and_new_review(contract_checkpoint):
    checkpoint = contract_checkpoint
    checkpoint.supervisor.run_role("backend")
    state = checkpoint.supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.blocker_kind == "result_contract", state.blocked_reason
    assert state.resume_status is WorkerStatus.REVIEWING
    assert state.result_contract_phase == "review"
    assert "SECURITY-REPO-20261111" in state.result_contract_feedback
    assert "issue_updates" in state.result_contract_feedback
    assert "TEST-" in state.result_contract_feedback
    assert len(state.result_contract_feedback) <= 2500
    assert state.task == checkpoint.task
    assert (state.iteration, state.review_failures) == (452, 27)
    assert read_pending_result(checkpoint.config, "backend") is None
    _assert_unpublished(checkpoint)
    records = _rejections(checkpoint)
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    expected = json.loads(json.dumps(asdict(checkpoint.results[0])))
    assert record["result"] == expected
    assert record["pending_result"]["result"] == expected
    assert record["phase"] == "review"
    original = records[0].read_bytes()
    checkpoint.reject = False
    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed.run_role("backend")
    after = resumed.load_state("backend")
    assert after.status is WorkerStatus.IDLE
    assert not after.result_contract_feedback
    assert not after.result_contract_phase
    assert len(checkpoint.prompts) == 2
    assert state.result_contract_feedback in checkpoint.prompts[1]
    assert "Current supervisor result-contract correction" in checkpoint.prompts[1]
    assert checkpoint.schemas[1]["properties"]["issue_updates"]["maxItems"] == 0
    assert records[0].read_bytes() == original
    assert read_pending_result(checkpoint.config, "backend") is None
    assert checkpoint.receipt.read_bytes() == checkpoint.receipt_bytes
    messages = _messages(checkpoint)
    assert len(messages) == 1
    assert "CORRECTED-PUBLICATION" in messages[0].read_text(encoding="utf-8")
    assert "REJECTED-PUBLICATION" not in messages[0].read_text(encoding="utf-8")


class SimulatedInterruption(BaseException):
    pass


@pytest.mark.parametrize("boundary", ["before_state", "after_state"])
def test_rejection_restart_preserves_result_and_feedback_once(
    contract_checkpoint, monkeypatch, boundary
):
    checkpoint = contract_checkpoint
    if boundary == "before_state":
        original_write = AtomicJsonStore.write
        interrupted = False

        def write(store, value):
            nonlocal interrupted
            if (
                store.path.name == "backend.json"
                and store.path.parent.name == "state"
                and value.get("blocker_kind") == "result_contract"
                and not interrupted
            ):
                interrupted = True
                raise SimulatedInterruption()
            return original_write(store, value)

        monkeypatch.setattr(AtomicJsonStore, "write", write)
    else:
        original_clear = supervisor_module.clear_pending_result
        interrupted = False

        def clear(*args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise SimulatedInterruption()
            return original_clear(*args, **kwargs)

        monkeypatch.setattr(supervisor_module, "clear_pending_result", clear)
    with pytest.raises(SimulatedInterruption):
        checkpoint.supervisor.run_role("backend")
    assert len(checkpoint.prompts) == 1
    assert read_pending_result(checkpoint.config, "backend") is not None
    records = _rejections(checkpoint)
    assert len(records) == 1
    original = records[0].read_bytes()
    _assert_unpublished(checkpoint)
    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    if boundary == "before_state":
        resumed.run_role("backend")
        assert len(checkpoint.prompts) == 1
        state = resumed.load_state("backend")
        assert state.blocker_kind == "result_contract"
        assert (state.iteration, state.review_failures) == (452, 27)
    checkpoint.reject = False
    resumed.run_role("backend")
    assert resumed.load_state("backend").status is WorkerStatus.IDLE
    assert len(checkpoint.prompts) == 2
    assert records[0].read_bytes() == original
    assert read_pending_result(checkpoint.config, "backend") is None
    assert len(_messages(checkpoint)) == 1


def test_publication_io_failure_replays_pending_without_new_review(
    contract_checkpoint, monkeypatch
):
    checkpoint = contract_checkpoint
    checkpoint.reject = False
    real_write = coordination.atomic_write_text
    attempts = 0

    def write(path, text):
        nonlocal attempts
        attempts += 1
        real_write(path, text)
        if attempts == 1:
            raise OSError("fixture publication failure after first file")

    monkeypatch.setattr(coordination, "atomic_write_text", write)
    checkpoint.supervisor.run_role("backend")
    pending = read_pending_result(checkpoint.config, "backend")
    assert pending is not None
    assert not _rejections(checkpoint)
    _assert_unpublished(checkpoint)
    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed.run_role("backend")
    assert resumed.load_state("backend").status is WorkerStatus.IDLE
    assert len(checkpoint.prompts) == 1
    assert len(_messages(checkpoint)) == 1
    assert read_pending_result(checkpoint.config, "backend") is None


def test_existing_consumed_issue_rejection_resumes_without_reconstructing_result(
    contract_checkpoint,
):
    checkpoint = contract_checkpoint
    original_id = "028c7ecb-d258-4528-82a3-a63d37cabd4f"
    checkpoint.supervisor.save_state(
        replace(
            checkpoint.supervisor.load_state("backend"),
            status=WorkerStatus.BLOCKED_SYSTEM,
            blocker_kind="system",
            blocked_reason="agent result rejected by supervisor safety gate: invalid live-test issue id: SECURITY-REPO-20261111",
            resume_status=WorkerStatus.REVIEWING,
            last_result_invocation_id=original_id,
        )
    )
    checkpoint.reject = False
    checkpoint.supervisor.run_role("backend")
    assert checkpoint.supervisor.load_state("backend").status is WorkerStatus.IDLE
    assert len(checkpoint.prompts) == 1
    assert "prior host rejection" in checkpoint.prompts[0]
    assert "SECURITY-REPO-20261111" in checkpoint.prompts[0]
    assert "Current supervisor result-contract correction" in checkpoint.prompts[0]
    assert read_pending_result(checkpoint.config, "backend") is None
    assert not _rejections(checkpoint)


def test_repeated_contract_rejections_stop_without_resetting_existing_counters(contract_checkpoint):
    checkpoint = contract_checkpoint
    for count in range(1, 4):
        Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
        state = checkpoint.supervisor.load_state("backend")
        assert state.result_contract_failures == count
        assert (state.iteration, state.review_failures, state.implementation_failures) == (
            452,
            27,
            0,
        )
        assert state.task == checkpoint.task
        assert state.last_result_invocation_id == checkpoint.results[-1].invocation_id
        assert len(_rejections(checkpoint)) == count
        _assert_unpublished(checkpoint)
    assert state.resume_status is None
    assert "automatic correction exhausted" in state.blocked_reason
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 3
    assert checkpoint.supervisor.load_state("backend") == state


def test_wrong_phase_output_is_rejected_and_corrected_in_review(contract_checkpoint):
    checkpoint = contract_checkpoint
    checkpoint.bad_outcome = True
    checkpoint.reject = False
    checkpoint.supervisor.run_role("backend")
    state = checkpoint.supervisor.load_state("backend")
    assert state.blocker_kind == "result_contract"
    assert state.result_contract_phase == "review"
    assert "invalid for phase" in state.result_contract_feedback
    assert state.resume_status is WorkerStatus.REVIEWING
    _assert_unpublished(checkpoint)
    checkpoint.bad_outcome = False
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert checkpoint.supervisor.load_state("backend").status is WorkerStatus.IDLE
    assert len(checkpoint.prompts) == 2
    assert state.result_contract_feedback in checkpoint.prompts[-1]


def test_archive_io_failure_retains_original_journal_before_consumption(
    contract_checkpoint, monkeypatch
):
    checkpoint = contract_checkpoint
    original = AtomicJsonStore.write
    failures = 0

    def write(store, value):
        nonlocal failures
        if store.path.parent.name == "rejected-results" and failures == 0:
            failures += 1
            raise OSError("rejection evidence unavailable")
        return original(store, value)

    monkeypatch.setattr(AtomicJsonStore, "write", write)
    checkpoint.supervisor.run_role("backend")
    pending = read_pending_result(checkpoint.config, "backend")
    assert pending is not None
    assert pending["result"]["issue_updates"][0]["issue_id"] == "SECURITY-REPO-20261111"
    assert not _rejections(checkpoint)
    _assert_unpublished(checkpoint)
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 1
    state = checkpoint.supervisor.load_state("backend")
    assert state.result_contract_failures == 1
    assert state.blocker_kind == "result_contract"
    assert len(_rejections(checkpoint)) == 1
    assert read_pending_result(checkpoint.config, "backend") is None


def _test_issue_task(checkpoint, *, bound=True, owner="backend", status="open", exists=True):
    task = replace(
        checkpoint.task,
        task_id=checkpoint.task.task_id + ("-TEST-007" if bound else ""),
        prompt=checkpoint.task.prompt + " Related live-test issue: TEST-007.",
    )
    checkpoint.task = task
    checkpoint.issue_id = "TEST-007"
    if exists:
        (checkpoint.config.coordination_root / "issues/TEST-007.md").write_text(
            f"Owner: {owner}\nStatus: {status}\n", encoding="utf-8"
        )
    coordination_checks.begin_checks(
        checkpoint.config, "backend", task, base_head=checkpoint.head, initial_head=checkpoint.head
    )
    report = run_iteration_validation(
        checkpoint.config, "backend", task.task_id, base_head=checkpoint.head, head=checkpoint.head
    )
    assert report.passed
    coordination_checks.finish_checks(
        checkpoint.config, "backend", task, base_head=checkpoint.head, head=checkpoint.head
    )
    checkpoint.receipt = (
        checkpoint.config.coordination_root / "validation" / f"backend-{task.task_id}.json"
    )
    checkpoint.receipt_bytes = checkpoint.receipt.read_bytes()
    checkpoint.supervisor.save_state(
        replace(checkpoint.supervisor.load_state("backend"), task=task)
    )


@pytest.mark.parametrize(
    "options, reason",
    [
        ({"bound": False}, "does not match active task"),
        ({"owner": "mobile"}, "does not own TEST-007"),
        ({"status": "verified"}, "must be open or fixed-needs-retest"),
        ({"exists": False}, "unknown live-test issue"),
    ],
    ids=["prompt-only", "foreign-owner", "closed", "missing"],
)
def test_task_binding_ownership_and_status_stay_authoritative(contract_checkpoint, options, reason):
    checkpoint = contract_checkpoint
    _test_issue_task(checkpoint, **options)
    checkpoint.supervisor.run_role("backend")
    state = checkpoint.supervisor.load_state("backend")
    assert state.blocker_kind == "result_contract"
    assert reason in state.result_contract_feedback
    assert len(_rejections(checkpoint)) == 1
    _assert_unpublished(checkpoint)


def test_authorized_test_retest_request_still_publishes_after_independent_pass(contract_checkpoint):
    checkpoint = contract_checkpoint
    _test_issue_task(checkpoint)
    checkpoint.supervisor.run_role("backend")
    assert checkpoint.supervisor.load_state("backend").status is WorkerStatus.IDLE
    assert not _rejections(checkpoint)
    messages = _messages(checkpoint)
    assert len(messages) == 2
    assert any(path.name.startswith("RETEST-") for path in messages)
    assert all(
        "Invocation: " + checkpoint.results[0].invocation_id in path.read_text(encoding="utf-8")
        for path in messages
    )
    assert checkpoint.schemas[0]["properties"]["issue_updates"]["items"]["properties"]["issue_id"][
        "enum"
    ] == ["TEST-007"]
    assert "Status: open" in (checkpoint.config.coordination_root / "issues/TEST-007.md").read_text(
        encoding="utf-8"
    )


def test_final_review_contract_retry_reuses_genuine_gates_and_requires_fresh_pass(final_checkpoint):
    checkpoint = final_checkpoint
    checkpoint.outcome = "ready_for_review"
    _claim(checkpoint)
    state = checkpoint.supervisor.load_state("backend")
    assert state.blocker_kind == "result_contract"
    assert state.task.task_id == "FINAL-VERIFY-backend"
    assert state.result_contract_phase == "review"
    assert state.resume_status is WorkerStatus.REVIEWING
    assert checkpoint.local_runs == 1
    checkpoint.outcome = "pass"
    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed.run_role("backend")
    assert resumed.load_state("backend").status is WorkerStatus.DONE
    assert checkpoint.local_runs == 1
    assert len(checkpoint.prompts) == 2
    assert state.result_contract_feedback in checkpoint.prompts[-1]
    assert not resumed.load_state("backend").result_contract_feedback


def test_queued_work_clears_only_correction_for_rejected_planning_phase(tmp_path, monkeypatch):
    from scripts.dev_orchestrator.result_contract import prompt_correction
    from tests.scripts.test_dev_orchestrator_supervisor import FakeInvoker, make_config

    invoker = FakeInvoker()
    invoker.add("backend", "lead", AgentResult("pass", "invalid planner outcome", "retry"))
    config = make_config(tmp_path, observe_only=False)
    supervisor = Supervisor(config, invoker)
    supervisor._plan("backend", WorkerState("backend"), "planning context")
    rejected = supervisor.load_state("backend")
    assert rejected.result_contract_phase == "plan"
    assert rejected.result_contract_failures == 1
    assert rejected.task is None
    seen = []

    def implement(role, state, task, context, model):
        seen.append(state)
        assert prompt_correction(state, "implement") == ""
        assert state.result_contract_failures == 0
        return AgentResult("continue", "Authorized queued work", "Continue implementation")

    monkeypatch.setattr(invoker, "implement", implement)
    queued = TaskItem("B-QUEUED", "Implement the explicitly queued bounded fix")
    supervisor.enqueue("backend", queued)
    supervisor.run_role("backend")
    assert len(seen) == 1
    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task == queued
    assert not state.result_contract_feedback
    assert not state.result_contract_phase
    assert state.review_failures == rejected.review_failures


@pytest.mark.parametrize("tamper_archive", [False, True], ids=["matching", "mismatched"])
def test_exhausted_rejection_cleans_only_verified_consumed_journal(
    contract_checkpoint, monkeypatch, tamper_archive
):
    checkpoint = contract_checkpoint
    checkpoint.supervisor.run_role("backend")
    checkpoint.supervisor.run_role("backend")
    original_clear = supervisor_module.clear_pending_result

    def interrupt(*args, **kwargs):
        raise SimulatedInterruption()

    monkeypatch.setattr(supervisor_module, "clear_pending_result", interrupt)
    with pytest.raises(SimulatedInterruption):
        checkpoint.supervisor.run_role("backend")
    exhausted = checkpoint.supervisor.load_state("backend")
    assert exhausted.result_contract_failures == 3
    assert exhausted.resume_status is None
    pending = read_pending_result(checkpoint.config, "backend")
    assert pending["invocation_id"] == exhausted.last_result_invocation_id
    record_path = next(
        path
        for path in _rejections(checkpoint)
        if json.loads(path.read_text(encoding="utf-8"))["invocation_id"]
        == exhausted.last_result_invocation_id
    )
    if tamper_archive:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["pending_result"]["result"]["summary"] = "tampered archive"
        record_path.write_text(json.dumps(record), encoding="utf-8")
    record_bytes = record_path.read_bytes()
    monkeypatch.setattr(supervisor_module, "clear_pending_result", original_clear)
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert checkpoint.supervisor.load_state("backend") == exhausted
    assert len(checkpoint.prompts) == 3
    assert record_path.read_bytes() == record_bytes
    assert read_pending_result(checkpoint.config, "backend") == (
        pending if tamper_archive else None
    )
    _assert_unpublished(checkpoint)


def test_host_issue_decoding_error_preserves_pending_without_model_rejection(contract_checkpoint):
    checkpoint = contract_checkpoint
    _test_issue_task(checkpoint)
    issue = checkpoint.config.coordination_root / "issues/TEST-007.md"
    original = issue.read_bytes()
    checkpoint.after_model = lambda: issue.write_bytes(b"Owner: backend\nStatus: open\n\xff")
    checkpoint.supervisor.run_role("backend")
    state = checkpoint.supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.blocker_kind == "system"
    assert "coordination validation unavailable" in state.blocked_reason
    assert state.result_contract_failures == 0
    assert not _rejections(checkpoint)
    assert read_pending_result(checkpoint.config, "backend") is not None
    _assert_unpublished(checkpoint)
    issue.write_bytes(original)
    checkpoint.after_model = None
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert checkpoint.supervisor.load_state("backend").status is WorkerStatus.IDLE
    assert len(checkpoint.prompts) == 1
    assert len(_messages(checkpoint)) == 2
    assert read_pending_result(checkpoint.config, "backend") is None


def test_archived_rejection_cannot_become_accepted_after_owner_change(
    contract_checkpoint, monkeypatch
):
    checkpoint = contract_checkpoint
    _test_issue_task(checkpoint, owner="mobile")
    original_write = AtomicJsonStore.write
    interrupted = False

    def write(store, value):
        nonlocal interrupted
        if (
            store.path.parent.name == "state"
            and value.get("blocker_kind") == "result_contract"
            and not interrupted
        ):
            interrupted = True
            raise SimulatedInterruption()
        return original_write(store, value)

    monkeypatch.setattr(AtomicJsonStore, "write", write)
    with pytest.raises(SimulatedInterruption):
        checkpoint.supervisor.run_role("backend")
    record_path = _rejections(checkpoint)[0]
    original_record = record_path.read_bytes()
    assert read_pending_result(checkpoint.config, "backend") is not None
    (checkpoint.config.coordination_root / "issues/TEST-007.md").write_text(
        "Owner: backend\nStatus: open\n", encoding="utf-8"
    )
    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed.run_role("backend")
    state = resumed.load_state("backend")
    assert state.blocker_kind == "result_contract"
    assert state.result_contract_failures == 1
    assert "does not own TEST-007" in state.result_contract_feedback
    assert len(checkpoint.prompts) == 1
    assert record_path.read_bytes() == original_record
    assert read_pending_result(checkpoint.config, "backend") is None
    _assert_unpublished(checkpoint)
    checkpoint.reject = False
    resumed.run_role("backend")
    assert resumed.load_state("backend").status is WorkerStatus.IDLE
    assert len(checkpoint.prompts) == 2
    assert len(_messages(checkpoint)) == 1
    assert checkpoint.results[0].invocation_id not in _messages(checkpoint)[0].read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("outcome", ["pass", "done"], ids=["archived-rejection", "valid-done"])
def test_pending_planner_result_is_consumed_before_explicit_queue(
    contract_checkpoint, monkeypatch, outcome
):
    checkpoint = contract_checkpoint
    checkpoint.plan_outcome = outcome
    planner_state = WorkerState("backend", status=WorkerStatus.PLANNING)
    checkpoint.supervisor.save_state(planner_state)
    if outcome == "pass":
        original_write = AtomicJsonStore.write
        interrupted = False

        def write(store, value):
            nonlocal interrupted
            if (
                store.path.parent.name == "state"
                and value.get("blocker_kind") == "result_contract"
                and not interrupted
            ):
                interrupted = True
                raise SimulatedInterruption()
            return original_write(store, value)

        monkeypatch.setattr(AtomicJsonStore, "write", write)
        with pytest.raises(SimulatedInterruption):
            checkpoint.supervisor.run_role("backend")
        assert len(_rejections(checkpoint)) == 1
    else:
        checkpoint.invoker.lead("backend", planner_state, "Plan bounded work")
    pending = read_pending_result(checkpoint.config, "backend")
    assert pending is not None and pending["phase"] == "plan"
    assert checkpoint.supervisor.load_state("backend").task is None
    queued = TaskItem("B-EXPLICIT-QUEUE", "Implement this explicitly queued bounded task")
    checkpoint.supervisor.enqueue("backend", queued)
    queue_before = checkpoint.supervisor._queue_store("backend").read()
    seen = []

    def implement(role, state, task, context, model):
        seen.append(task)
        assert read_pending_result(checkpoint.config, role) is None
        assert not state.result_contract_feedback
        assert not state.result_contract_phase
        return AgentResult("continue", "Queued work started", "Continue bounded work")

    monkeypatch.setattr(checkpoint.invoker, "implement", implement)
    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    monkeypatch.setattr(
        resumed,
        "_done_claim",
        lambda *_a, **_k: pytest.fail(
            "an old planner done claim must yield to eligible queued work"
        ),
    )
    resumed.run_role("backend")
    assert resumed._queue_store("backend").read() == queue_before
    assert not seen
    assert read_pending_result(checkpoint.config, "backend") is None
    consumed = resumed.load_state("backend")
    assert consumed.last_result_invocation_id == pending["invocation_id"]
    assert consumed.status is (
        WorkerStatus.BLOCKED_SYSTEM if outcome == "pass" else WorkerStatus.IDLE
    )
    resumed.run_role("backend")
    assert seen == [queued]
    assert resumed._queue_store("backend").read() == []
    assert resumed.load_state("backend").task == queued
    assert resumed.load_state("backend").status is WorkerStatus.IMPLEMENTING
    assert len(checkpoint.prompts) == 1
