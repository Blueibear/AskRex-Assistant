from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import coordination_checks
from scripts.dev_orchestrator.cli import (
    resume_confirmed_human_blocker,
    save_config,
    set_observe_only,
)
from scripts.dev_orchestrator.handoff import acknowledge_handoff, advance_handoff
from scripts.dev_orchestrator.lifecycle import write_heartbeat
from scripts.dev_orchestrator.model_blockers import capture_supplied_input
from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import AgentResult, TaskItem, WorkerState, WorkerStatus
from scripts.dev_orchestrator.validation import run_iteration_validation
from tests.scripts.test_dev_orchestrator_completion import _config


@pytest.fixture
def reviewed(tmp_path, monkeypatch, request):
    role = getattr(request, "param", "backend")
    config = _config(tmp_path)
    task = TaskItem("publish-reviewed-checkpoint", "Implement the assigned bounded change.")
    gate = {"name": "real fixture check", "command": [sys.executable, "-c", "print('checked')"]}
    config = replace(
        config,
        metadata={
            "host_publication": {"enabled": True},
            "iteration_validation": {
                role: {"overrides": [{"task_prefix": task.task_id, "gates": [gate]}]}
            },
        },
    )
    save_config(config)
    root = config.coordination_root
    write_heartbeat(root / "supervisor-heartbeat.json")
    repo = config.backend_root if role == "backend" else config.mobile_root
    (root / "scripts").mkdir()
    (root / "scripts/check-coordination.ps1").write_text("fixture coordination script\n")
    monkeypatch.setattr(
        coordination_checks,
        "_check_command",
        lambda *_: [sys.executable, "-c", "print('actual host capture')"],
    )
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    coordination_checks.begin_checks(config, role, task, head, head)
    report = run_iteration_validation(config, role, task.task_id, base_head=head, head=head)
    assert report.passed and report.receipt is not None
    coordination_checks.finish_checks(config, role, task, head, head)
    invoker = object.__new__(CliAgentInvoker)
    invoker.config = config
    invocation = str(uuid.uuid4())

    def execute(arguments, repo, role, provider, invocation_id, **kwargs):
        nonlocal invocation
        invocation = invocation_id
        provenance = capture_supplied_input(
            config,
            role,
            "review",
            task.task_id,
            invocation_id,
            head,
            kwargs["input_context"],
            arguments[-1],
            input_kind="cli_prompt_utf8",
        )
        assert provenance is not None
        result = AgentResult(
            "pass",
            "Independent review passed.",
            "",
            role=role,
            task_id=task.task_id,
            invocation_id=invocation_id,
        )
        advance_handoff(
            config,
            role,
            pre_head=head,
            post_head=head,
            invocation_id=invocation_id,
            provider="codex",
            pending_result={
                "phase": "review",
                "role": role,
                "task_id": task.task_id,
                "invocation_id": invocation_id,
                "pre_head": head,
                "post_head": head,
                "result": asdict(result),
                "input_provenance": provenance,
            },
        )
        return ProcessResult(0, json.dumps(asdict(result)), "")

    monkeypatch.setattr(invoker, "_execute_agent", execute)
    supervisor = Supervisor(config, invoker)
    supervisor.save_state(WorkerState(role, WorkerStatus.REVIEWING, task, head))
    supervisor.run_role(role)
    assert supervisor.load_state(role).status == WorkerStatus.IDLE
    ledger = supervisor.completed_tasks.records(role)
    assert ledger[-1]["source"] == "independent_review"
    assert ledger[-1]["invocation_id"] == invocation
    checkpoint = SimpleNamespace(
        config=config,
        task=task,
        head=head,
        invocation=invocation,
        repo=repo,
        role=role,
        supervisor=supervisor,
        ledger=ledger,
    )

    def review_again(*, changed_head=False):
        nonlocal task, head
        if changed_head:
            implementation_id = str(uuid.uuid4())
            (repo / "README.md").write_text("reviewed next change\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
            subprocess.run(
                [
                    "git",
                    "commit",
                    "-qm",
                    "fixture next change\n\nAskRex-Orchestrator-Invocation: " + implementation_id,
                ],
                cwd=repo,
                check=True,
            )
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True
            ).strip()
            # This fixture models a newly acknowledged external checkpoint.
            # Its subsequent host gates and independent review are still real.
            acknowledge_handoff(config, role, source="test")
        task = TaskItem(task.task_id + "-again", "Review another authorized checkpoint.")
        coordination_checks.begin_checks(config, role, task, head, head)
        report = run_iteration_validation(config, role, task.task_id, base_head=head, head=head)
        assert report.passed and report.receipt is not None
        coordination_checks.finish_checks(config, role, task, head, head)
        supervisor.save_state(WorkerState(role, WorkerStatus.REVIEWING, task, head))
        supervisor.run_role(role)
        assert supervisor.load_state(role).status == WorkerStatus.IDLE
        checkpoint.task, checkpoint.head, checkpoint.invocation = task, head, invocation

    checkpoint.review_again = review_again
    return checkpoint


class Transport:
    """Only the fixed network boundary is mocked; all Git/proof checks are real."""

    def __init__(self, checkpoint):
        self.checkpoint = checkpoint
        self.remote_head = "a" * 40
        self.prs = []
        self.calls = []
        self.callback = None
        self.query_error = None
        self.push_error = None
        self.create_error = None
        self.accept_push = True
        self.accept_create = True
        self.body = ""
        self.repository = (
            "Blueibear/AskRex-Assistant" if checkpoint.role == "backend" else "Blueibear/AskRex"
        )

    def pr(self, head=None, **overrides):
        value = {
            "number": 7,
            "url": "https://github.com/" + self.repository + "/pull/7",
            "state": "OPEN",
            "isDraft": True,
            "headRefName": "worker/" + self.checkpoint.role,
            "headRefOid": head or self.checkpoint.head,
            "baseRefName": "master" if self.checkpoint.role == "backend" else "main",
            "isCrossRepository": False,
            "headRepository": {"name": self.repository.split("/")[1]},
            "headRepositoryOwner": {"login": "Blueibear"},
        }
        value.update(overrides)
        return value

    def __call__(self, args, *, cwd):
        self.calls.append(list(args))
        if self.callback:
            self.callback(list(args))
        if args[:3] == ["gh", "pr", "list"]:
            assert args[args.index("--repo") + 1] == "github.com/" + self.repository
            if self.query_error:
                return subprocess.CompletedProcess(args, *self.query_error)
            return subprocess.CompletedProcess(args, 0, json.dumps(self.prs), "")
        if "ls-remote" in args:
            text = (
                f"{self.remote_head}\trefs/heads/worker/{self.checkpoint.role}\n"
                if self.remote_head
                else ""
            )
            return subprocess.CompletedProcess(args, 0, text, "")
        if "push" in args:
            assert "--force" not in args and not any(a.startswith("+") for a in args)
            assert args[-1] == self.checkpoint.head + ":refs/heads/worker/" + self.checkpoint.role
            if self.accept_push:
                self.remote_head = self.checkpoint.head
                for item in self.prs:
                    item["headRefOid"] = self.remote_head
            if isinstance(self.push_error, BaseException):
                raise self.push_error
            return subprocess.CompletedProcess(args, *(self.push_error or (0, "pushed\n", "")))
        if args[:3] == ["gh", "pr", "create"]:
            assert "--draft" in args and "--body-file" in args
            self.body = Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8")
            if self.accept_create:
                self.prs = [self.pr()]
            if isinstance(self.create_error, BaseException):
                raise self.create_error
            return subprocess.CompletedProcess(
                args, *(self.create_error or (0, self.pr()["url"], ""))
            )
        raise AssertionError(f"Unexpected transport command: {args}")

    @property
    def mutations(self):
        return [args for args in self.calls if "push" in args or args[:3] == ["gh", "pr", "create"]]


def publish(checkpoint, transport, **kwargs):
    from scripts.dev_orchestrator.github_publication import publish_reviewed_draft

    return publish_reviewed_draft(checkpoint.config, checkpoint.role, runner=transport, **kwargs)


def test_reviewed_checkpoint_publishes_only_truthful_draft(reviewed):
    transport = Transport(reviewed)
    before = reviewed.supervisor.load_state("backend").to_dict()
    result = publish(
        reviewed, transport, unresolved_acceptance=("Physical device evidence pending.",)
    )
    assert result.status == "published"
    assert len(transport.mutations) == 2
    assert reviewed.head in transport.body and reviewed.task.task_id in transport.body
    assert "Physical device evidence pending." in transport.body
    assert "No merge, release, or physical acceptance is claimed" in transport.body
    assert reviewed.supervisor.load_state("backend").to_dict() == before
    again = publish(reviewed, transport)
    assert again.status == "already_published"
    assert len(transport.mutations) == 2


def test_absent_pr_is_not_inferred_from_query_error(reviewed):
    transport = Transport(reviewed)
    transport.query_error = (1, "", "network unavailable")
    result = publish(reviewed, transport)
    assert result.status == "system_error"
    assert not transport.mutations


def test_pause_after_query_prevents_push_admission(reviewed):
    transport = Transport(reviewed)

    def pause(args):
        if "ls-remote" in args:
            set_observe_only(reviewed.config.coordination_root, True)

    transport.callback = pause
    assert publish(reviewed, transport).status == "waiting"
    assert not transport.mutations


def test_ambiguous_create_reconciles_without_duplicate(reviewed):
    transport = Transport(reviewed)
    transport.create_error = subprocess.TimeoutExpired("gh", 60)
    publish(reviewed, transport)
    assert len(transport.mutations) == 2
    result = publish(reviewed, transport)
    assert result.status == "already_published"
    assert len(transport.mutations) == 2


@pytest.mark.parametrize("flag", [False, None, "true", 1])
def test_policy_is_explicit_literal_true_and_off_by_default(reviewed, flag):
    store = AtomicJsonStore(reviewed.config.coordination_root / "orchestrator-config.json")
    raw = store.read()
    raw["metadata"]["host_publication"] = {} if flag is None else {"enabled": flag}
    store.write(raw)
    transport = Transport(reviewed)
    assert publish(reviewed, transport).status == "waiting"
    assert transport.calls == []


@pytest.mark.parametrize(
    "damage",
    [
        "ledger",
        "old_head",
        "adjudication",
        "receipt",
        "capture",
        "script",
        "review_input",
        "active",
        "pending",
        "dirty",
    ],
)
def test_incomplete_or_changed_review_proof_never_reaches_network(reviewed, damage):
    root = reviewed.config.coordination_root
    ledger = root / "completed-tasks/backend.json"
    receipt = root / "validation" / f"backend-{reviewed.task.task_id}.json"
    checks = root / "evidence/supervisor-checks" / f"backend-{reviewed.task.task_id}.json"
    if damage == "ledger":
        ledger.unlink()
    elif damage in {"old_head", "adjudication"}:
        raw = AtomicJsonStore(ledger).read()
        raw[-1]["completed_head" if damage == "old_head" else "source"] = (
            "a" * 40 if damage == "old_head" else "coordination_adjudication"
        )
        AtomicJsonStore(ledger).write(raw)
    elif damage == "receipt":
        raw = AtomicJsonStore(receipt).read()
        raw["gates"][0]["stdout"] += "tampered"
        AtomicJsonStore(receipt).write(raw)
    elif damage == "capture":
        raw = AtomicJsonStore(checks).read()
        raw["final"] = None
        AtomicJsonStore(checks).write(raw)
    elif damage == "script":
        (root / "scripts/check-coordination.ps1").write_text("changed")
    elif damage == "review_input":
        digest = hashlib.sha256(reviewed.invocation.encode()).hexdigest()
        (root / "model-inputs" / f"backend-{digest}" / "input.txt").write_text("different")
    elif damage == "active":
        (root / "active-agents").mkdir(exist_ok=True)
        (root / "active-agents/backend.json").write_text("{}")
    elif damage == "pending":
        lease = AtomicJsonStore(root / "handoff/backend.json")
        raw = lease.read()
        raw["pending_result"] = {"invocation_id": str(uuid.uuid4())}
        lease.write(raw)
    else:
        (reviewed.repo / "unreviewed.txt").write_text("not accepted")
    transport = Transport(reviewed)
    assert publish(reviewed, transport).status == "system_error"
    assert transport.calls == []


@pytest.mark.parametrize(
    "urls",
    [
        ["https://github.com/someone/else.git"],
        [
            "https://github.com/Blueibear/AskRex-Assistant.git",
            "https://github.com/someone/else.git",
        ],
        ["https://github.com/Blueibear/AskRex.git"],
        [
            "https://user:secret@github.com/Blueibear/AskRex-Assistant.git"  # pragma: allowlist secret
        ],
    ],
)
def test_every_effective_push_url_must_match_the_role(reviewed, urls):
    for url in urls:
        subprocess.run(
            ["git", "config", "--add", "remote.origin.pushurl", url], cwd=reviewed.repo, check=True
        )
    transport = Transport(reviewed)
    assert publish(reviewed, transport).status == "system_error"
    assert not transport.calls


@pytest.mark.parametrize(
    "error,status",
    [
        ((4, "", "authentication required"), "auth_required"),
        ((1, "", "HTTP 500"), "system_error"),
        ((0, "null", ""), "system_error"),
        ((0, '{"not":"an array"}', ""), "system_error"),
    ],
)
def test_query_failures_are_typed_and_never_absence(reviewed, error, status):
    transport = Transport(reviewed)
    transport.query_error = error
    assert publish(reviewed, transport).status == status
    assert not transport.mutations


@pytest.mark.parametrize(
    "change",
    [
        {"baseRefName": "other"},
        {"isCrossRepository": True},
        {"headRepositoryOwner": {"login": "other"}},
        {"url": "https://other.invalid/pull/7"},
    ],
)
def test_contradictory_pr_identity_blocks_all_mutation(reviewed, change):
    transport = Transport(reviewed)
    transport.prs = [transport.pr(**change)]
    assert publish(reviewed, transport).status == "system_error"
    assert not transport.mutations


def test_existing_non_draft_pr_is_never_edited_or_made_ready(reviewed):
    transport = Transport(reviewed)
    transport.prs = [transport.pr(head=transport.remote_head, isDraft=False)]
    result = publish(reviewed, transport)
    assert result.status == "already_published"
    assert len(transport.mutations) == 1 and "push" in transport.mutations[0]
    assert transport.prs[0]["isDraft"] is False


def test_closed_pr_does_not_authorize_another_create(reviewed):
    transport = Transport(reviewed)
    transport.prs = [transport.pr(state="CLOSED")]
    assert publish(reviewed, transport).status == "waiting"
    assert not transport.mutations


def test_pause_after_admitted_push_drains_it_but_stops_create(reviewed):
    transport = Transport(reviewed)

    def pause(args):
        if "push" in args:
            set_observe_only(reviewed.config.coordination_root, True)

    transport.callback = pause
    result = publish(reviewed, transport)
    assert result.status == "waiting"
    assert len(transport.mutations) == 1
    assert transport.remote_head == reviewed.head
    journals = list((reviewed.config.coordination_root / "github-publication").glob("*/*.json"))
    push = next(
        AtomicJsonStore(p).read()["push"] for p in journals if p.name == reviewed.head + ".json"
    )
    assert push["outcome"]["exit_code"] == 0
    assert push["pid"] > 0 and push["process_started_filetime"] > 0


def test_global_control_lock_is_not_held_across_network_calls(reviewed):
    transport = Transport(reviewed)
    root = reviewed.config.coordination_root

    def inspect(args):
        if "push" not in args and args[:3] != ["gh", "pr", "create"]:
            return
        # A different process must be able to acquire the lock during each mutation.
        code = (
            "from pathlib import Path; from scripts.dev_orchestrator.lifecycle import ControlPlaneLock; "
            f"p=Path({str(root / 'control-plane.lock')!r}); "
            "lock=ControlPlaneLock(p); lock.__enter__(); lock.__exit__(None,None,None)"
        )
        subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).parents[2],
            check=True,
            capture_output=True,
            timeout=10,
        )

    transport.callback = inspect
    assert publish(reviewed, transport).status == "published"


def test_lease_drift_between_query_and_mutation_is_rejected(reviewed):
    transport = Transport(reviewed)

    def drift(args):
        if "ls-remote" in args:
            store = AtomicJsonStore(reviewed.config.coordination_root / "handoff/backend.json")
            raw = store.read()
            raw["lease_id"] = str(uuid.uuid4())
            store.write(raw)

    transport.callback = drift
    assert publish(reviewed, transport).status == "system_error"
    assert not transport.mutations


def test_ambiguous_push_reconciles_actual_success_before_new_create(reviewed):
    transport = Transport(reviewed)
    transport.push_error = subprocess.TimeoutExpired("git", 120)
    assert publish(reviewed, transport).status == "waiting"
    assert len(transport.mutations) == 1
    transport.push_error = None
    assert publish(reviewed, transport).status == "published"
    assert len(transport.mutations) == 2


def test_unconfirmed_push_never_repeats_or_creates(reviewed):
    transport = Transport(reviewed)
    transport.accept_push = False
    transport.push_error = (1, "", "non-fast-forward rejected")
    assert publish(reviewed, transport).status == "system_error"
    assert publish(reviewed, transport).status == "waiting"
    assert len(transport.mutations) == 1


def test_unconfirmed_create_never_repeats_even_after_clean_absence_query(reviewed):
    transport = Transport(reviewed)
    transport.create_error = subprocess.TimeoutExpired("gh", 120)
    transport.accept_create = False
    assert publish(reviewed, transport).status == "waiting"
    create_path = next(
        (reviewed.config.coordination_root / "github-publication").glob("*/create.json")
    )
    original = create_path.read_bytes()
    assert publish(reviewed, transport).status == "waiting"
    assert create_path.read_bytes() == original
    assert len(transport.mutations) == 2


@pytest.mark.parametrize("action", ["push", "create"])
def test_crash_after_admission_does_not_reuse_journal_as_permission(reviewed, monkeypatch, action):
    from scripts.dev_orchestrator import github_publication as publication

    transport = Transport(reviewed)
    real_act = publication._act

    class Crash(BaseException):
        pass

    def crash(config, path, journal, selected, runner, repo):
        if selected == action:
            raise Crash()
        return real_act(config, path, journal, selected, runner, repo)

    monkeypatch.setattr(publication, "_act", crash)
    with pytest.raises(Crash):
        publish(reviewed, transport)
    count = len(transport.mutations)
    monkeypatch.setattr(publication, "_act", real_act)
    assert publish(reviewed, transport).status == "waiting"
    assert len(transport.mutations) == count


@pytest.mark.parametrize("reviewed", ["mobile"], indirect=True)
def test_mobile_publication_uses_only_its_repository_and_base(reviewed):
    transport = Transport(reviewed)
    result = publish(
        reviewed, transport, unresolved_acceptance=("Device and backend proof pending.",)
    )
    assert result.status == "published"
    create = transport.mutations[-1]
    assert create[create.index("--base") + 1] == "main"
    assert create[create.index("--repo") + 1] == "github.com/Blueibear/AskRex"


@pytest.mark.parametrize("damage", ["foreign_pid", "wrong_creation", "stale", "future"])
def test_only_current_fresh_supervisor_can_admit_publication(reviewed, damage):
    path = reviewed.config.coordination_root / "supervisor-heartbeat.json"
    heartbeat = AtomicJsonStore(path).read()
    if damage == "foreign_pid":
        heartbeat["pid"] += 100000
    elif damage == "wrong_creation":
        heartbeat["process_started_filetime"] += 1
    else:
        heartbeat["timestamp"] = (
            datetime.now(UTC) + timedelta(seconds=300 if damage == "future" else -300)
        ).isoformat()
    AtomicJsonStore(path).write(heartbeat)
    transport = Transport(reviewed)
    assert publish(reviewed, transport).status == "system_error"
    assert not transport.mutations


def test_definitive_workflow_denial_can_retry_after_authorization_resolution(reviewed):
    transport = Transport(reviewed)
    transport.accept_push = False
    transport.push_error = (
        1,
        "To https://github.com/Blueibear/AskRex-Assistant.git\n"
        f"!\t{reviewed.head}:refs/heads/worker/backend\t[remote rejected] "
        "(refusing to allow an OAuth App to create or update workflow "
        "'.github/workflows/ci.yml' without 'workflow' scope)\nDone\n",
        "error: failed to push some refs\n",
    )
    first = publish(reviewed, transport)
    assert first.status == "auth_required"
    assert "workflow" in first.reason.lower()
    reviewed.supervisor.save_state(
        replace(
            reviewed.supervisor.load_state("backend"),
            status=WorkerStatus.BLOCKED_USER,
            blocker_kind="github_auth",
            blocked_reason=first.reason,
            resume_status=WorkerStatus.IDLE,
        )
    )
    count = len(transport.mutations)
    assert publish(reviewed, transport).status == "auth_required"
    assert len(transport.mutations) == count
    # This is the existing operator path after credentials are corrected.
    resume_confirmed_human_blocker(reviewed.config.coordination_root, "backend", "github_auth")
    transport.accept_push = True
    transport.push_error = None
    consume_publication_resume(reviewed)
    assert publish(reviewed, transport, resume_denied=True).status == "published"
    assert len(transport.mutations) == 3
    path = Path(first.journal_path)
    journal = AtomicJsonStore(path).read()
    assert len(journal["push_history"]) == 1
    denied = journal["push_history"][0]["outcome"]
    assert denied["exit_code"] == 1 and "workflow" in denied["diagnostic"].lower()


def test_another_accepted_task_at_same_head_reconciles_original_publication(reviewed):
    transport = Transport(reviewed)
    first = publish(reviewed, transport)
    assert first.status == "published"
    original = AtomicJsonStore(Path(first.journal_path)).read()["proof"]
    reviewed.review_again()
    assert reviewed.task.task_id != original["task_id"]
    assert publish(reviewed, transport).status == "already_published"
    assert len(transport.mutations) == 2
    assert AtomicJsonStore(Path(first.journal_path)).read()["proof"] == original


@pytest.mark.parametrize("action", ["push", "create"])
def test_new_reviewed_head_does_not_bypass_old_uncertain_action(reviewed, action):
    transport = Transport(reviewed)
    setattr(transport, "accept_" + action, False)
    setattr(transport, action + "_error", subprocess.TimeoutExpired(action, 120))
    assert publish(reviewed, transport).status == "waiting"
    count = len(transport.mutations)
    reviewed.review_again(changed_head=True)
    assert publish(reviewed, transport).status == "waiting"
    assert len(transport.mutations) == count


def test_publication_junction_is_rejected_before_outside_directory_creation(reviewed, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = reviewed.config.coordination_root / "github-publication"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    transport = Transport(reviewed)
    assert publish(reviewed, transport).status == "system_error"
    assert list(outside.iterdir()) == []
    assert not transport.mutations


def test_failure_diagnostic_is_actionable_and_secret_redacted(reviewed):
    transport = Transport(reviewed)
    transport.accept_push = False
    credential = "ghp_" + "A" * 36  # pragma: allowlist secret
    transport.push_error = (
        1,
        "",
        "remote: repository rule violation\nAuthorization: Bearer " + credential,
    )
    result = publish(reviewed, transport)
    assert result.status == "system_error"
    journal = AtomicJsonStore(Path(result.journal_path)).read()
    outcome = journal["push"]["outcome"]
    assert "repository rule violation" in outcome["diagnostic"]
    assert credential not in json.dumps(journal) and credential not in result.reason
    assert len(outcome["diagnostic"]) <= 1500


def test_credential_words_without_exact_porcelain_rejection_stay_unknown(reviewed):
    transport = Transport(reviewed)
    transport.accept_push = False
    transport.push_error = (128, "", "fatal: workflow upload failed without workflow scope")
    assert publish(reviewed, transport).status == "auth_required"
    assert publish(reviewed, transport).status == "waiting"
    assert len(transport.mutations) == 1


def test_corrupt_creation_body_blocks_reconciliation_without_another_action(reviewed):
    transport = Transport(reviewed)
    transport.accept_create = False
    transport.create_error = subprocess.TimeoutExpired("gh", 120)
    assert publish(reviewed, transport).status == "waiting"
    path = next((reviewed.config.coordination_root / "github-publication").glob("*/create.json"))
    create = AtomicJsonStore(path).read()
    Path(create["body"]["path"]).write_text("not the reviewed draft body")
    assert publish(reviewed, transport).status == "system_error"
    assert len(transport.mutations) == 2


def test_remote_create_success_survives_local_outcome_write_interruption(reviewed, monkeypatch):
    transport = Transport(reviewed)
    original_write = AtomicJsonStore.write

    def fail_after_create(store, value):
        if store.path.name == "create.json" and (value.get("create") or {}).get("outcome"):
            raise OSError("injected local disk write failure")
        return original_write(store, value)

    monkeypatch.setattr(AtomicJsonStore, "write", fail_after_create)
    assert publish(reviewed, transport).status == "system_error"
    assert len(transport.mutations) == 2
    monkeypatch.setattr(AtomicJsonStore, "write", original_write)
    assert publish(reviewed, transport).status == "already_published"
    assert len(transport.mutations) == 2


def consume_publication_resume(checkpoint):
    store = AtomicJsonStore(
        checkpoint.config.coordination_root / "state" / f"{checkpoint.role}.json"
    )
    state = store.read()
    state.update(
        status="blocked_system",
        blocker_kind="host_publication",
        completion_host_action="publication",
        resume_status=None,
        task=TaskItem(f"FINAL-VERIFY-{checkpoint.role}", "Verify completion.").to_dict(),
        task_base_head=checkpoint.head,
    )
    store.write(state)


def workflow_denial(checkpoint):
    return (
        1,
        "To https://github.com/Blueibear/AskRex-Assistant.git\n"
        f"!\t{checkpoint.head}:refs/heads/worker/backend\t[remote rejected] "
        "(refusing to allow an OAuth App to update workflow without workflow scope)\nDone\n",
        "error: failed to push some refs\n",
    )


def test_denial_before_caller_saves_human_hold_does_not_auto_retry(reviewed):
    transport = Transport(reviewed)
    transport.accept_push = False
    transport.push_error = workflow_denial(reviewed)
    assert publish(reviewed, transport).status == "auth_required"
    # Simulate caller interruption before its BLOCKED_USER/github_auth save.
    assert publish(reviewed, transport).status == "auth_required"
    assert len(transport.mutations) == 1


def test_new_head_can_follow_definitive_denial_after_explicit_auth_resume(reviewed):
    transport = Transport(reviewed)
    transport.accept_push = False
    transport.push_error = workflow_denial(reviewed)
    first = publish(reviewed, transport)
    assert first.status == "auth_required"
    reviewed.supervisor.save_state(
        replace(
            reviewed.supervisor.load_state("backend"),
            status=WorkerStatus.BLOCKED_USER,
            blocker_kind="github_auth",
            resume_status=WorkerStatus.IDLE,
        )
    )
    resume_confirmed_human_blocker(reviewed.config.coordination_root, "backend", "github_auth")
    reviewed.review_again(changed_head=True)
    transport.push_error = None
    transport.accept_push = True
    consume_publication_resume(reviewed)
    assert publish(reviewed, transport, resume_denied=True).status == "published"
    assert len(transport.mutations) == 3
    assert AtomicJsonStore(Path(first.journal_path)).read()["push"]["outcome"]["exit_code"] == 1


def test_pause_before_create_preserves_old_body_and_allows_current_truth(reviewed):
    transport = Transport(reviewed)

    def pause(args):
        if "push" in args:
            set_observe_only(reviewed.config.coordination_root, True)

    transport.callback = pause
    assert (
        publish(reviewed, transport, unresolved_acceptance=("Device pending.",)).status == "waiting"
    )
    old_bodies = {
        p: p.read_bytes()
        for p in (reviewed.config.coordination_root / "github-publication").glob("*/*-body.md")
    }
    assert old_bodies
    transport.callback = None
    set_observe_only(reviewed.config.coordination_root, False)
    assert (
        publish(reviewed, transport, unresolved_acceptance=("Backend evidence pending.",)).status
        == "published"
    )
    assert "Backend evidence pending." in transport.body
    assert all(p.read_bytes() == value for p, value in old_bodies.items())


def test_second_denial_before_caller_reblocks_needs_another_explicit_resume(reviewed):
    transport = Transport(reviewed)
    transport.accept_push = False
    transport.push_error = workflow_denial(reviewed)
    assert publish(reviewed, transport).status == "auth_required"
    consume_publication_resume(reviewed)
    assert publish(reviewed, transport, resume_denied=True).status == "auth_required"
    assert len(transport.mutations) == 2
    # No one-shot permission survives the call or interruption.
    assert publish(reviewed, transport).status == "auth_required"
    assert len(transport.mutations) == 2


def test_resume_keyword_cannot_bypass_unconsumed_human_hold(reviewed):
    transport = Transport(reviewed)
    transport.accept_push = False
    transport.push_error = workflow_denial(reviewed)
    assert publish(reviewed, transport).status == "auth_required"
    reviewed.supervisor.save_state(
        replace(
            reviewed.supervisor.load_state("backend"),
            status=WorkerStatus.BLOCKED_USER,
            blocker_kind="github_auth",
            resume_status=WorkerStatus.IDLE,
        )
    )
    assert publish(reviewed, transport, resume_denied=True).status == "auth_required"
    assert len(transport.mutations) == 1


@pytest.mark.parametrize("existing_pr", [False, True])
def test_explicit_resume_reconciles_remote_head_without_another_push(reviewed, existing_pr):
    transport = Transport(reviewed)
    transport.accept_push = False
    transport.push_error = workflow_denial(reviewed)
    first = publish(reviewed, transport)
    assert first.status == "auth_required"
    path = Path(first.journal_path)
    denied = AtomicJsonStore(path).read()["push"]
    # Another authorized actor completed the push while the operator fixed access.
    transport.remote_head = reviewed.head
    transport.push_error = None
    if existing_pr:
        transport.prs = [transport.pr()]
    assert publish(reviewed, transport).status == "auth_required"
    assert len(transport.mutations) == 1
    before = path.read_bytes()
    assert publish(reviewed, transport, resume_denied=True).status == "system_error"
    assert path.read_bytes() == before and len(transport.mutations) == 1
    consume_publication_resume(reviewed)
    resumed = publish(reviewed, transport, resume_denied=True)
    assert resumed.status == ("already_published" if existing_pr else "published")
    expected_mutations = 1 if existing_pr else 2
    assert len(transport.mutations) == expected_mutations
    # Successful explicit reconciliation must not reinstate the old auth hold.
    assert publish(reviewed, transport).status == "already_published"
    assert len(transport.mutations) == expected_mutations
    current = AtomicJsonStore(path).read()["push"]
    assert current["outcome"] == denied["outcome"]
    assert current["admission_id"] == denied["admission_id"]
    resolution = current["resolution"]
    assert resolution["remote_head"] == reviewed.head
    assert resolution["denied_admission_id"] == denied["admission_id"]
