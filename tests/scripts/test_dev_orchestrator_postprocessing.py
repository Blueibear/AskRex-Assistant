from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import coordination_checks, postprocessing, runner
from scripts.dev_orchestrator import supervisor as supervisor_module
from scripts.dev_orchestrator.cli import save_config
from scripts.dev_orchestrator.handoff import read_pending_result, repo_snapshot
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import (
    AgentResult,
    CoordinationMessage,
    TaskItem,
    WorkerState,
    WorkerStatus,
)
from tests.scripts.test_dev_orchestrator_routing import _safe_config


@pytest.fixture
def postprocess_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ASKREX_PROVIDER_POLICY", "subscription_only")
    config, repo, _ = _safe_config(tmp_path)
    task = TaskItem(
        "B-AUDIT",
        "Perform a read-only contract-readiness audit. Do not modify repository files. "
        "Report exact fields and source paths through structured coordination.",
        "Provide a bounded audit artifact identifying exact interfaces and data flow.",
    )
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
                            "gates": [
                                {
                                    "name": "fixture canonical",
                                    "command": [sys.executable, "-c", "print('CANONICAL-PASS')"],
                                    "cwd": ".",
                                }
                            ],
                        }
                    ]
                },
            }
        },
    )
    save_config(config)
    scripts = config.coordination_root / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "check-coordination.ps1").write_text(
        "param([string]$Role)\nWrite-Output $Role\n", encoding="utf-8"
    )
    # Exercise the real capture producer on every platform with a portable child.
    # The mandatory script artifact, ownership observations and hashes remain real.
    monkeypatch.setattr(
        coordination_checks,
        "_check_command",
        lambda _config, role: [sys.executable, "-c", "import sys; print(sys.argv[1])", role],
    )
    head, dirty = repo_snapshot(repo)
    assert not dirty
    observed = SimpleNamespace(
        config=config,
        repo=repo,
        task=task,
        head=head,
        calls=[],
        draft="Field: value  \nPath: service.ts  \n",
        write_draft=True,
        results=[],
        commands=[],
    )
    real_run = runner.run_command

    def local_model(command, cwd, timeout_seconds=1800, **kwargs):
        assert Path(command[0]).stem.casefold() == "codex"
        observed.commands.append(command)
        prompt = kwargs["stdin_text"]
        invocation = re.search(r"AskRex-Orchestrator-Invocation-ID: ([0-9a-f-]+)", prompt).group(1)
        observed.calls.append(prompt)
        reviewing = prompt.startswith("Independently review")
        result = AgentResult(
            "pass" if reviewing else "ready_for_review",
            "Audit complete",
            "Independent review",
            role="backend",
            task_id=task.task_id,
            invocation_id=invocation,
            coordination_messages=(
                ()
                if reviewing
                else (
                    CoordinationMessage(
                        "supervisor",
                        "normal",
                        task.task_id,
                        False,
                        "Exact interface field: value; source: service.ts -> renderer.ts.",
                    ),
                )
            ),
        )
        observed.results.append(result)
        code = "from pathlib import Path\n"
        if observed.write_draft and not reviewing:
            code += "Path('audit.md').write_text(" + repr(observed.draft) + ", encoding='utf-8')\n"
        code += "print(" + repr(json.dumps(asdict(result))) + ")\n"
        return real_run([sys.executable, "-c", code], cwd, timeout_seconds, **kwargs)

    monkeypatch.setattr(runner, "run_command", local_model)
    invoker = runner.CliAgentInvoker(config)
    supervisor = Supervisor(config, invoker)
    supervisor.save_state(
        WorkerState("backend", status=WorkerStatus.IMPLEMENTING, task=task, task_base_head=head)
    )
    observed.invoker = invoker
    observed.supervisor = supervisor
    return observed


def test_prepublication_failure_becomes_retry_feedback_without_accepting_model_result(
    postprocess_checkpoint,
):
    checkpoint = postprocess_checkpoint
    checkpoint.supervisor.run_role("backend")
    state = checkpoint.supervisor.load_state("backend")
    assert state.status is WorkerStatus.IMPLEMENTING, state.blocked_reason
    assert "trailing whitespace" in state.task.feedback
    assert checkpoint.task.feedback in state.task.feedback
    assert checkpoint.task.prompt == state.task.prompt
    assert "rejected" in state.task.feedback.lower()
    assert state.iteration == 1 and state.implementation_failures == 1
    assert repo_snapshot(checkpoint.repo) == (checkpoint.head, "")
    assert not (checkpoint.config.coordination_root / "active-agents/backend.json").exists()
    assert read_pending_result(checkpoint.config, "backend") is None
    assert not list((checkpoint.config.coordination_root / "mailbox/supervisor").glob("*.md"))
    assert not list((checkpoint.config.coordination_root / "validation").glob("*.json"))
    assert len(checkpoint.calls) == 1


def test_explicit_readonly_task_does_not_receive_mutation_instructions(postprocess_checkpoint):
    checkpoint = postprocess_checkpoint
    prompt = runner._codex_task_prompt(
        "backend", checkpoint.task, checkpoint.config.coordination_root, "ctx", "inv-1"
    )
    assert "then edit the implementation" not in prompt
    assert "inspect and edit files" not in prompt
    assert "5,500" in prompt
    assert "original task" in prompt.lower()
    assert (
        "Do not run tests, builds, formatters, dependency installs, or check-coordination.ps1"
        in prompt
    )
    assert "The supervisor will create the Git checkpoint" not in prompt
    assert "no repository checkpoint to publish" in prompt


def _journal(checkpoint):
    state = checkpoint.supervisor.load_state("backend")
    result = checkpoint.invoker.implement("backend", state, state.task, "bounded context", "sonnet")
    assert result.outcome == "ready_for_review"
    pending = read_pending_result(checkpoint.config, "backend")
    assert pending and "postprocessing_failure" in pending
    return pending


def _failure_manifest(checkpoint, pending):
    ref = pending["postprocessing_failure"]
    path = checkpoint.config.coordination_root / ref["manifest_path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == ref["manifest_sha256"]
    return path, json.loads(path.read_bytes())


def test_corrected_readonly_retry_reaches_real_validation_and_review(postprocess_checkpoint):
    checkpoint = postprocess_checkpoint
    checkpoint.supervisor.run_role("backend")
    failed = checkpoint.supervisor.load_state("backend")
    assert failed.status is WorkerStatus.IMPLEMENTING, failed.blocked_reason
    assert "+Field: value" in failed.task.feedback
    assert "5,500" in failed.task.feedback
    checkpoint.write_draft = False

    checkpoint.supervisor.run_role("backend")
    ready = checkpoint.supervisor.load_state("backend")
    assert ready.status is WorkerStatus.REVIEWING, ready.blocked_reason
    assert ready.task.feedback == ""
    assert repo_snapshot(checkpoint.repo) == (checkpoint.head, "")
    assert (checkpoint.config.coordination_root / "validation/backend-B-AUDIT.json").exists()
    messages = list((checkpoint.config.coordination_root / "mailbox/supervisor").glob("*.md"))
    assert len(messages) == 1
    assert "Exact interface field" in messages[0].read_text()
    for command in checkpoint.commands:
        assert command[command.index("-s") + 1] == "read-only"

    checkpoint.supervisor.run_role("backend")
    assert checkpoint.supervisor.load_state("backend").status is WorkerStatus.IDLE
    assert len(checkpoint.calls) == 3
    assert read_pending_result(checkpoint.config, "backend") is None


@pytest.mark.parametrize("boundary", ["journal", "state_saved"])
def test_postprocessing_interruption_replays_failure_once(
    postprocess_checkpoint, monkeypatch, boundary
):
    checkpoint = postprocess_checkpoint

    class Interrupted(BaseException):
        pass

    if boundary == "journal":
        original = runner.journal_prepublication_failure

        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise Interrupted()

        monkeypatch.setattr(runner, "journal_prepublication_failure", interrupted)
    else:
        original_write = checkpoint.supervisor._state_store("backend").write
        original_store = checkpoint.supervisor._state_store

        class InterruptedStore:
            def write(self, value):
                original_write(value)
                if value["last_result_invocation_id"]:
                    raise Interrupted()

            def read(self, default=None):
                return original_store("backend").read(default)

        monkeypatch.setattr(checkpoint.supervisor, "_state_store", lambda _role: InterruptedStore())
    with pytest.raises(Interrupted):
        checkpoint.supervisor.run_role("backend")
    pending = read_pending_result(checkpoint.config, "backend")
    assert pending and "postprocessing_failure" in pending
    if boundary == "journal":
        assert (checkpoint.config.coordination_root / "active-agents/backend.json").exists()
    else:
        assert (
            checkpoint.supervisor.load_state("backend").last_result_invocation_id
            == pending["invocation_id"]
        )

    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed.run_role("backend")
    state = resumed.load_state("backend")
    assert state.status is WorkerStatus.IMPLEMENTING, state.blocked_reason
    assert state.iteration == 1 and state.implementation_failures == 1
    assert checkpoint.task.feedback in state.task.feedback
    assert len(checkpoint.calls) == 1
    assert read_pending_result(checkpoint.config, "backend") is None
    assert not (checkpoint.config.coordination_root / "active-agents/backend.json").exists()


def test_pending_clear_error_does_not_repark_committed_retry(postprocess_checkpoint, monkeypatch):
    checkpoint = postprocess_checkpoint
    _journal(checkpoint)
    with monkeypatch.context() as patch:
        patch.setattr(
            supervisor_module,
            "clear_pending_result",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("clear interrupted")),
        )
        checkpoint.supervisor.recover_postprocessing_failure("backend")
    state = checkpoint.supervisor.load_state("backend")
    original_feedback = state.task.feedback
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.iteration == 1 and state.implementation_failures == 1
    assert read_pending_result(checkpoint.config, "backend") is not None

    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    assert resumed.recover_postprocessing_failure("backend")
    state = resumed.load_state("backend")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task.feedback == original_feedback
    assert state.iteration == 1 and state.implementation_failures == 1
    assert read_pending_result(checkpoint.config, "backend") is None


@pytest.mark.parametrize(
    "damage", ["active_marker", "unknown_marker", "owner", "index", "patch", "lease", "unjournaled"]
)
def test_unproven_postprocessing_ownership_remains_blocked(postprocess_checkpoint, damage):
    checkpoint = postprocess_checkpoint
    pending = _journal(checkpoint)
    root = checkpoint.config.coordination_root
    marker_path = root / "active-agents/backend.json"
    marker = json.loads(marker_path.read_bytes())
    scratch = Path(marker["scratch_path"])
    path, manifest = _failure_manifest(checkpoint, pending)
    if damage in {"active_marker", "unknown_marker"}:
        marker["status" if damage == "active_marker" else "invocation_id"] = (
            "active" if damage == "active_marker" else "unknown-invocation"
        )
        marker_path.write_text(json.dumps(marker))
    elif damage == "owner":
        (scratch.parent / ".askrex-scratch-owner.json").write_text("{}")
    elif damage == "index":
        index = scratch / ".git/index"
        index.write_bytes(index.read_bytes() + b"changed")
    elif damage == "patch":
        (root / manifest["patch"]["path"]).write_bytes(b"changed patch")
    elif damage == "lease":
        handoff = root / "handoff/backend.json"
        value = json.loads(handoff.read_bytes())
        value["lease_id"] = "different-lease"
        AtomicJsonStore(handoff).write(value)
    else:
        handoff = root / "handoff/backend.json"
        value = json.loads(handoff.read_bytes())
        value.pop("pending_result")
        AtomicJsonStore(handoff).write(value)
    marker_bytes = marker_path.read_bytes()
    source_head = repo_snapshot(checkpoint.repo)[0]

    checkpoint.supervisor.run_role("backend")

    state = checkpoint.supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.iteration == 0 and state.implementation_failures == 0
    assert marker_path.read_bytes() == marker_bytes
    assert repo_snapshot(checkpoint.repo)[0] == source_head
    assert len(checkpoint.calls) == 1
    assert not list((root / "validation").glob("*.json"))


def test_live_process_marker_cannot_authorize_failure_journal(postprocess_checkpoint, monkeypatch):
    checkpoint = postprocess_checkpoint
    original = runner.capture_completed_result

    def live_marker(*args, **kwargs):
        path = checkpoint.config.coordination_root / "active-agents/backend.json"
        marker = json.loads(path.read_bytes())
        marker["pid"] = os.getpid()
        path.write_text(json.dumps(marker))
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, "capture_completed_result", live_marker)
    checkpoint.supervisor.run_role("backend")
    assert checkpoint.supervisor.load_state("backend").status is WorkerStatus.BLOCKED_SYSTEM
    assert read_pending_result(checkpoint.config, "backend") is None
    assert (checkpoint.config.coordination_root / "active-agents/backend.json").exists()
    assert len(checkpoint.calls) == 1


def test_clean_whitespace_still_cannot_publish_prohibited_repository_change(postprocess_checkpoint):
    checkpoint = postprocess_checkpoint
    checkpoint.draft = "Field: value\nPath: service.ts\n"
    pending = _journal(checkpoint)
    _, manifest = _failure_manifest(checkpoint, pending)
    assert manifest["failure_kind"] == "task_scope"
    assert manifest["exit_code"] == 0
    assert manifest["task_scope_rejected"] is True
    assert repo_snapshot(checkpoint.repo) == (checkpoint.head, "")
    assert pending["result"]["outcome"] == "ready_for_review"

    checkpoint.supervisor.recover_postprocessing_failure("backend")
    state = checkpoint.supervisor.load_state("backend")
    assert "actual exit code: 0" in state.task.feedback
    assert "host policy decision" in state.task.feedback
    assert not list((checkpoint.config.coordination_root / "mailbox/supervisor").glob("*.md"))


def test_journal_storage_failure_retains_original_gate_diagnostic(
    postprocess_checkpoint, monkeypatch
):
    checkpoint = postprocess_checkpoint
    monkeypatch.setattr(
        runner,
        "journal_prepublication_failure",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("journal storage unavailable")),
    )
    checkpoint.supervisor.run_role("backend")
    root = checkpoint.config.coordination_root
    assert read_pending_result(checkpoint.config, "backend") is None
    assert (root / "active-agents/backend.json").exists()
    errors = list((root / "postprocessing-failures").glob("*/first-postprocessing-error.json"))
    assert len(errors) == 1
    before = errors[0].read_bytes()
    assert json.loads(before)["error_type"] == "ScratchGateFailure"
    assert "trailing whitespace" in json.loads(before)["message"]
    assert list((root / "alerts").glob("workstream_failure-*.json"))
    checkpoint.supervisor.run_role("backend")
    assert errors[0].read_bytes() == before
    assert len(checkpoint.calls) == 1


def test_paused_known_marker_blocker_can_consume_failure_without_dispatch(postprocess_checkpoint):
    checkpoint = postprocess_checkpoint
    _journal(checkpoint)
    original = checkpoint.supervisor.load_state("backend")
    checkpoint.supervisor.save_state(
        replace(
            original,
            status=WorkerStatus.BLOCKED_SYSTEM,
            blocker_kind="coordination_evidence",
            blocked_reason="initial coordination evidence unavailable: unresolved active-agents writer activity for backend",
            resume_status=None,
        )
    )
    paused = Supervisor(replace(checkpoint.config, observe_only=True), object())
    assert paused.recover_postprocessing_failure("backend")
    state = paused.load_state("backend")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.iteration == 1 and state.implementation_failures == 1
    assert len(checkpoint.calls) == 1


@pytest.mark.parametrize(
    "status,kind", [(WorkerStatus.BLOCKED_USER, "human"), (WorkerStatus.BLOCKED_SYSTEM, "security")]
)
def test_host_failure_never_overrides_unrelated_blockers(postprocess_checkpoint, status, kind):
    checkpoint = postprocess_checkpoint
    _journal(checkpoint)
    state = replace(
        checkpoint.supervisor.load_state("backend"),
        status=status,
        blocker_kind=kind,
        blocked_reason="owner decision required",
        resume_status=None,
    )
    checkpoint.supervisor.save_state(state)
    assert checkpoint.supervisor.recover_postprocessing_failure("backend")
    assert checkpoint.supervisor.load_state("backend") == state
    assert read_pending_result(checkpoint.config, "backend") is not None
    assert (checkpoint.config.coordination_root / "active-agents/backend.json").exists()


def test_failure_manifest_is_idempotent_after_handoff_write_interruption(
    postprocess_checkpoint, monkeypatch
):
    checkpoint = postprocess_checkpoint
    state = checkpoint.supervisor.load_state("backend")
    with monkeypatch.context() as patch:

        def interrupted(*_args, **_kwargs):
            raise OSError("handoff persistence interrupted")

        patch.setattr(postprocessing, "advance_handoff", interrupted)
        with pytest.raises(OSError, match="handoff persistence interrupted"):
            checkpoint.invoker.implement("backend", state, state.task, "bounded context", "sonnet")

    root = checkpoint.config.coordination_root
    assert read_pending_result(checkpoint.config, "backend") is None
    (manifest_path,) = (root / "postprocessing-failures").glob("*/failure.json")
    before = manifest_path.read_bytes()
    manifest = json.loads(before)
    record = json.loads((root / manifest["completed_result"]["path"]).read_bytes())
    candidate = {
        "phase": "implement",
        "role": "backend",
        "task_id": state.task.task_id,
        "invocation_id": record["invocation_id"],
        "pre_head": checkpoint.head,
        "post_head": checkpoint.head,
        "result": record["result"],
        "postprocessing_failure": {
            "schema_version": 1,
            "kind": "prepublication_gate_failure",
            "manifest_path": manifest_path.relative_to(root).as_posix(),
            "manifest_sha256": hashlib.sha256(before).hexdigest(),
            "manifest_bytes": len(before),
        },
    }
    postprocessing.validate_prepublication_failure(checkpoint.config, "backend", state, candidate)
    failure = postprocessing.ScratchGateFailure(
        manifest["failure_kind"],
        manifest["command"],
        subprocess.CompletedProcess(
            manifest["command"],
            manifest["exit_code"],
            (root / manifest["stdout"]["path"]).read_bytes(),
            (root / manifest["stderr"]["path"]).read_bytes(),
        ),
        Path(manifest["cwd"]),
    )
    pending = postprocessing.journal_prepublication_failure(
        checkpoint.config,
        "backend",
        state.task,
        task_base_head=state.task_base_head,
        completed_result=manifest["completed_result"],
        failure=failure,
        provenance=manifest["gate_provenance"],
    )
    assert manifest_path.read_bytes() == before
    assert read_pending_result(checkpoint.config, "backend") == pending
    assert checkpoint.supervisor.recover_postprocessing_failure("backend")
    restored = checkpoint.supervisor.load_state("backend")
    assert restored.status is WorkerStatus.IMPLEMENTING
    assert restored.iteration == 1 and restored.implementation_failures == 1
    assert len(checkpoint.calls) == 1


def test_capture_without_classified_journal_preserves_evidence_but_cannot_recover(
    postprocess_checkpoint, monkeypatch
):
    checkpoint = postprocess_checkpoint
    original = runner.capture_completed_result

    class Interrupted(BaseException):
        pass

    def capture_then_interrupt(*args, **kwargs):
        original(*args, **kwargs)
        raise Interrupted()

    monkeypatch.setattr(runner, "capture_completed_result", capture_then_interrupt)
    with pytest.raises(Interrupted):
        checkpoint.supervisor.run_role("backend")
    root = checkpoint.config.coordination_root
    (record_path,) = (root / "postprocessing-failures").glob("*/completed-result.json")
    before = record_path.read_bytes()
    assert json.loads(before)["result"]["outcome"] == "ready_for_review"
    assert not list((root / "postprocessing-failures").glob("*/failure.json"))
    assert read_pending_result(checkpoint.config, "backend") is None

    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed.run_role("backend")
    state = resumed.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.blocker_kind == "coordination_evidence"
    assert state.iteration == 0 and state.implementation_failures == 0
    assert record_path.read_bytes() == before
    assert (root / "active-agents/backend.json").exists()
    assert len(checkpoint.calls) == 1


def test_repeated_failure_feedback_remains_bounded_and_preserves_task_scope(postprocess_checkpoint):
    checkpoint = postprocess_checkpoint
    checkpoint.draft = "Field: value  \n" + "Field: value\n" * 1800
    for attempt in range(2):
        checkpoint.supervisor.run_role("backend")
        state = checkpoint.supervisor.load_state("backend")
        assert state.status is WorkerStatus.IMPLEMENTING, state.blocked_reason
        assert state.iteration == attempt + 1
        assert state.implementation_failures == attempt + 1
        assert len(state.task.feedback) <= 18000
        assert checkpoint.task.feedback in state.task.feedback
        assert checkpoint.task.prompt == state.task.prompt
        assert "original task prohibits repository edits" in state.task.feedback
    assert len(checkpoint.calls) == 2


def test_ordinary_implementation_still_publishes_allowed_clean_change(postprocess_checkpoint):
    checkpoint = postprocess_checkpoint
    checkpoint.draft = "Field: value\nPath: service.ts\n"
    state = checkpoint.supervisor.load_state("backend")
    allowed = replace(
        state.task, prompt="Implement the scoped contract field and its audit documentation."
    )
    checkpoint.supervisor.save_state(replace(state, task=allowed))
    checkpoint.supervisor.run_role("backend")
    ready = checkpoint.supervisor.load_state("backend")
    assert ready.status is WorkerStatus.REVIEWING, ready.blocked_reason
    new_head, dirty = repo_snapshot(checkpoint.repo)
    assert new_head != checkpoint.head and not dirty
    assert (checkpoint.repo / "audit.md").read_text() == checkpoint.draft
    assert checkpoint.commands[0][checkpoint.commands[0].index("-s") + 1] == "workspace-write"
    assert not (checkpoint.config.coordination_root / "active-agents/backend.json").exists()


@pytest.mark.parametrize("when", ["before", "after"])
def test_same_invocation_unblock_is_saved_before_pending_cleanup(
    postprocess_checkpoint, monkeypatch, when
):
    checkpoint = postprocess_checkpoint
    pending = _journal(checkpoint)
    with monkeypatch.context() as patch:

        def failed_clear(*_args, **_kwargs):
            raise OSError("cleanup pending")

        patch.setattr(supervisor_module, "clear_pending_result", failed_clear)
        checkpoint.supervisor.recover_postprocessing_failure("backend")
    committed = checkpoint.supervisor.load_state("backend")
    assert committed.last_result_invocation_id == pending["invocation_id"]
    parked = replace(
        committed,
        status=WorkerStatus.BLOCKED_SYSTEM,
        blocker_kind="postprocessing_evidence",
        blocked_reason="cleanup failed",
        resume_status=None,
    )
    checkpoint.supervisor._save_state_preserving_pending_result(parked)
    original = checkpoint.supervisor._save_state_preserving_pending_result

    class Interrupted(BaseException):
        pass

    def interrupt_write(state):
        if when == "after":
            original(state)
        raise Interrupted()

    with monkeypatch.context() as patch:
        patch.setattr(
            checkpoint.supervisor, "_save_state_preserving_pending_result", interrupt_write
        )
        with pytest.raises(Interrupted):
            checkpoint.supervisor.recover_postprocessing_failure("backend")
    assert read_pending_result(checkpoint.config, "backend") is not None
    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    assert resumed.recover_postprocessing_failure("backend")
    result = resumed.load_state("backend")
    assert result.status is WorkerStatus.IMPLEMENTING
    assert result.task == committed.task
    assert result.iteration == 1 and result.implementation_failures == 1
    assert read_pending_result(checkpoint.config, "backend") is None
    assert len(checkpoint.calls) == 1


def test_repeated_feedback_does_not_nest_or_lose_original_reviewer_request(postprocess_checkpoint):
    checkpoint = postprocess_checkpoint
    pending = _journal(checkpoint)
    manifest, record, directory = postprocessing.validate_prepublication_failure(
        checkpoint.config, "backend", checkpoint.supervisor.load_state("backend"), pending
    )
    task = checkpoint.task
    for _ in range(12):
        feedback = postprocessing.failure_feedback(
            checkpoint.config, task, manifest, record, directory
        )
        assert checkpoint.task.feedback in feedback
        assert feedback.count("Original reviewer request:") == 1
        assert len(feedback) <= 18000
        task = replace(task, feedback=feedback)


def test_generic_readonly_prompt_preserves_original_deliverable_and_recipients():
    task = TaskItem("STATUS-1", "No repository changes. Inspect the version and report to backend.")
    prompt = runner._codex_task_prompt("backend", task, Path("coordination"), "context", "inv-1")
    assert "original task recipients" in prompt
    assert "source-to-render" not in prompt
    assert "one supervisor coordination body" not in prompt
    assert "existing backend request" not in prompt
    assert "Inspect the version and report to backend." in prompt


def test_only_explicit_host_provenance_adds_legacy_retry_guidance(
    postprocess_checkpoint, monkeypatch
):
    checkpoint = postprocess_checkpoint
    guide = (
        "Send one corrected supervisor body of at most 5,500 characters, Related to B-AUDIT, "
        "with exact interfaces/fields and source-to-render paths. Refer to the existing backend "
        "request instead of duplicating it. Do not modify repository files."
    )
    original = runner.capture_completed_result

    def capture_with_legacy_guidance(*args, **kwargs):
        kwargs["provenance"] = {
            "source": "guarded_legacy_recovery_fixture",
            "retry_guidance": guide,
        }
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, "capture_completed_result", capture_with_legacy_guidance)
    pending = _journal(checkpoint)
    manifest, record, directory = postprocessing.validate_prepublication_failure(
        checkpoint.config, "backend", checkpoint.supervisor.load_state("backend"), pending
    )
    without_guide = {**record, "provenance": {"source": "runner_stdout_utf8_text"}}
    ordinary = postprocessing.failure_feedback(
        checkpoint.config, checkpoint.task, manifest, without_guide, directory
    )
    assert "source-to-render paths" not in ordinary
    assert "one corrected supervisor body" not in ordinary
    assert checkpoint.supervisor.recover_postprocessing_failure("backend")
    state = checkpoint.supervisor.load_state("backend")
    assert guide in state.task.feedback
    assert "cannot expand the original task authority" in state.task.feedback
    assert state.task.prompt == checkpoint.task.prompt
    assert len(state.task.feedback) <= 18000


@pytest.mark.parametrize(
    "failure_kind", ["git_config_128", "exit2_empty", "exit2_error", "exit2_mixed"]
)
def test_operational_diff_check_failure_stays_unclassified_and_parked(
    postprocess_checkpoint, monkeypatch, failure_kind
):
    checkpoint = postprocess_checkpoint
    real_run = subprocess.run
    outcomes = []

    def git_failure(command, *args, **kwargs):
        if command != ["git", "diff", "--cached", "--check"]:
            return real_run(command, *args, **kwargs)
        if failure_kind == "git_config_128":
            env = {
                **os.environ,
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.whitespace",
                "GIT_CONFIG_VALUE_0": "tab-in-indent,indent-with-non-tab",
            }
            result = real_run(command, *args, **{**kwargs, "env": env})
        else:
            out = b""
            err = b""
            if failure_kind == "exit2_error":
                err = b"error: unable to read object\n"
            elif failure_kind == "exit2_mixed":
                out = real_run(command, *args, **kwargs).stdout + b"fatal: incomplete object read\n"
            code = f"import sys; sys.stdout.buffer.write({out!r}); sys.stderr.buffer.write({err!r}); sys.exit(2)"
            result = real_run([sys.executable, "-c", code], *args, **kwargs)
        outcomes.append(result)
        return result

    monkeypatch.setattr(subprocess, "run", git_failure)
    checkpoint.supervisor.run_role("backend")
    state = checkpoint.supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.blocker_kind == "postprocessing_evidence"
    assert state.resume_status is None
    assert state.iteration == 0 and state.implementation_failures == 0
    assert state.task == checkpoint.task
    assert repo_snapshot(checkpoint.repo) == (checkpoint.head, "")
    root = checkpoint.config.coordination_root
    assert read_pending_result(checkpoint.config, "backend") is None
    assert (root / "active-agents/backend.json").exists()
    assert not list((root / "postprocessing-failures").glob("*/failure.json"))
    (result_path,) = (root / "postprocessing-failures").glob("*/completed-result.json")
    assert json.loads(result_path.read_bytes())["result"]["outcome"] == "ready_for_review"
    (error_path,) = (root / "postprocessing-failures").glob("*/first-postprocessing-error.json")
    error_bytes = error_path.read_bytes()
    error = json.loads(error_bytes)
    assert error["gate"]["exit_code"] == outcomes[0].returncode
    for stream in ("stdout", "stderr"):
        assert (root / error["gate"][stream]["path"]).read_bytes() == getattr(outcomes[0], stream)
    assert not list((root / "mailbox/supervisor").glob("*.md"))
    assert not list((root / "validation").glob("*.json"))
    checkpoint.supervisor.run_role("backend")
    assert checkpoint.supervisor.load_state("backend") == state
    assert error_path.read_bytes() == error_bytes
    assert len(checkpoint.calls) == 1


@pytest.mark.parametrize(
    "exit_code,stdout,stderr",
    [
        (128, b"", b"fatal: cannot read object\n"),
        (2, b"", b""),
        (2, b"", b"error: cannot read object\n"),
    ],
)
def test_existing_operational_failure_envelope_cannot_be_consumed(
    postprocess_checkpoint, exit_code, stdout, stderr
):
    checkpoint = postprocess_checkpoint
    pending = _journal(checkpoint)
    root = checkpoint.config.coordination_root
    path, manifest = _failure_manifest(checkpoint, pending)
    manifest["exit_code"] = exit_code
    for stream, data in (("stdout", stdout), ("stderr", stderr)):
        target = root / manifest[stream]["path"]
        target.write_bytes(data)
        manifest[stream]["bytes"] = len(data)
        manifest[stream]["sha256"] = hashlib.sha256(data).hexdigest()
    data = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(data)
    pending["postprocessing_failure"]["manifest_bytes"] = len(data)
    pending["postprocessing_failure"]["manifest_sha256"] = hashlib.sha256(data).hexdigest()
    handoff = AtomicJsonStore(root / "handoff/backend.json")
    lease = handoff.read()
    lease["pending_result"] = pending
    handoff.write(lease)
    marker_path = root / "active-agents/backend.json"
    marker_before = marker_path.read_bytes()

    checkpoint.supervisor.run_role("backend")
    state = checkpoint.supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.blocker_kind == "postprocessing_evidence" and state.resume_status is None
    assert state.iteration == 0 and state.implementation_failures == 0
    assert state.task == checkpoint.task
    assert marker_path.read_bytes() == marker_before
    assert read_pending_result(checkpoint.config, "backend") == pending
    assert path.read_bytes() == data
    assert len(checkpoint.calls) == 1


@pytest.mark.parametrize(
    "draft,whitespace,diagnostic",
    [
        ("text  \n", "blank-at-eol,blank-at-eof,space-before-tab", "trailing whitespace."),
        (" \ttext\n", "blank-at-eol,blank-at-eof,space-before-tab", "space before tab in indent."),
        ("text\n\n", "blank-at-eol,blank-at-eof,space-before-tab", "new blank line at EOF."),
        (
            "<<<<<<< ours\nfirst\n=======\nsecond\n>>>>>>> theirs\n",
            "blank-at-eol,blank-at-eof,space-before-tab",
            "leftover conflict marker",
        ),
        (
            " \ttext  \n",
            "blank-at-eol,blank-at-eof,space-before-tab",
            "trailing whitespace, space before tab in indent.",
        ),
        ("\ttext\n", "tab-in-indent", "tab in indent."),
        ("        text\n", "indent-with-non-tab", "indent with spaces."),
    ],
)
def test_supported_real_git_content_diagnostics_remain_recoverable(
    postprocess_checkpoint, monkeypatch, draft, whitespace, diagnostic
):
    checkpoint = postprocess_checkpoint
    checkpoint.draft = draft
    real_run = subprocess.run

    def configured_git(command, *args, **kwargs):
        if command == ["git", "diff", "--cached", "--check"]:
            env = {
                **os.environ,
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.whitespace",
                "GIT_CONFIG_VALUE_0": whitespace,
            }
            kwargs = {**kwargs, "env": env}
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", configured_git)
    pending = _journal(checkpoint)
    _, manifest = _failure_manifest(checkpoint, pending)
    root = checkpoint.config.coordination_root
    assert manifest["exit_code"] == 2
    assert (root / manifest["stderr"]["path"]).read_bytes() == b""
    assert diagnostic in (root / manifest["stdout"]["path"]).read_text(encoding="utf-8")
    assert checkpoint.supervisor.recover_postprocessing_failure("backend")
    state = checkpoint.supervisor.load_state("backend")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.iteration == 1 and state.implementation_failures == 1
    assert diagnostic in state.task.feedback
    assert repo_snapshot(checkpoint.repo) == (checkpoint.head, "")
    assert len(checkpoint.calls) == 1


@pytest.mark.parametrize(
    "exit_code,stdout,stderr",
    [
        (1, b"a.py:1: trailing whitespace.\n+x  \n", b""),
        (-9, b"a.py:1: trailing whitespace.\n+x  \n", b""),
        (128, b"a.py:1: trailing whitespace.\n+x  \n", b""),
        (129, b"a.py:1: trailing whitespace.\n+x  \n", b""),
        (2, b"\n", b""),
        (2, b"fatal: bad object\n", b""),
        (2, b"a.py:1: trailing whitespace.\n", b""),
        (2, b"+x  \n", b""),
        (2, b"a.py:1: unrelated failure.\n+x\n", b""),
        (2, b"a.py:0: trailing whitespace.\n+x  \n", b""),
        (2, b"a.py:1: trailing whitespace, trailing whitespace.\n+x  \n", b""),
        (2, b"a.py:1: trailing whitespace.\n+x  \n", b"fatal: incomplete\n"),
        (2, b"a.py:1: trailing whitespace.\n+x  \ntruncated", b""),
        (2, b"a.py:1: trailing whitespace.\n+\xff  \n", b""),
        (2, b"a.py:1: trailing whitespace.\n+" + b"x" * 65536 + b"  \n", b""),
    ],
    ids=[
        "exit-one",
        "signal",
        "exit-128",
        "exit-129",
        "empty",
        "operational-text",
        "missing-source",
        "orphan-source",
        "unknown-diagnostic",
        "zero-line",
        "duplicate-diagnostic",
        "stderr-error",
        "mixed-tail",
        "invalid-utf8",
        "over-budget",
    ],
)
def test_unproven_diff_check_shapes_fail_closed(exit_code, stdout, stderr):
    with pytest.raises(postprocessing.UnclassifiedPostprocessingFailure):
        postprocessing._validate_gate(
            "scratch_diff_check",
            ["git", "diff", "--cached", "--check"],
            exit_code,
            {"pre_head": "fixed", "repository_changes_allowed": True},
            stdout=stdout,
            stderr=stderr,
        )
