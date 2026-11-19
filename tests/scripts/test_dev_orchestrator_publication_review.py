from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from scripts.dev_orchestrator import coordination_checks, publication_review
from scripts.dev_orchestrator.evidence import EvidenceError, build_review_evidence
from scripts.dev_orchestrator.handoff import read_pending_result
from scripts.dev_orchestrator.runner import AgentInvocationError
from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import WorkerState, WorkerStatus
from scripts.dev_orchestrator.validation import run_iteration_validation
from scripts.dev_orchestrator.worker_context import SuppliedContext
from tests.scripts.test_dev_orchestrator_github_publication import (
    Transport,
)
from tests.scripts.test_dev_orchestrator_github_publication import (
    reviewed as reviewed,
)


def test_interrupted_immutable_publication_does_not_strand_partial_authority(tmp_path, monkeypatch):
    destination = tmp_path / "scope.json"
    data = b'{"complete":"publication proof"}\n'
    original_open = Path.open

    class PartialWrite:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, value):
            self.handle.write(value[:7])
            self.handle.flush()
            raise OSError("injected partial write interruption")

    def interrupted(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return PartialWrite(handle) if path.parent == tmp_path and mode == "xb" else handle

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", interrupted)
        with pytest.raises(OSError, match="partial write"):
            publication_review._save(tmp_path, destination, data)
    assert not destination.exists()
    publication_review._save(tmp_path, destination, data)
    assert destination.read_bytes() == data
    publication_review._save(tmp_path, destination, data)
    with pytest.raises(ValueError, match="conflicting immutable"):
        publication_review._save(tmp_path, destination, data + b"changed")
    assert destination.read_bytes() == data


def _assign(checkpoint):
    task, base = publication_review.designate_next_review(
        checkpoint.config, checkpoint.role, runner=Transport(checkpoint)
    )
    state = WorkerState(checkpoint.role, WorkerStatus.IMPLEMENTING, task, base)
    checkpoint.supervisor.save_state(state)
    return state


@pytest.mark.parametrize(
    "reviewed",
    [{"role": "backend", "publication_review": False, "publication_content_chars": 150_000}],
    indirect=True,
)
def test_part_sizing_reserves_the_actual_review_uuid_width(reviewed):
    state = _assign(reviewed)
    config, role, head = reviewed.config, reviewed.role, reviewed.head
    coordination_checks.begin_checks(
        config, role, state.task, state.task_base_head, head, recovered=True
    )
    report = run_iteration_validation(
        config, role, state.task.task_id, base_head=state.task_base_head, head=head
    )
    assert report.passed
    coordination_checks.finish_checks(config, role, state.task, state.task_base_head, head)
    state = replace(state, status=WorkerStatus.REVIEWING)
    reviewed.supervisor.save_state(state)
    _scope, text, descriptor, first = publication_review.validate_review_task(
        config, role, state.task, state.task_base_head, head
    )
    candidate = publication_review.select_part(text, first, 228_000)
    actual_id = "12345678-1234-4234-8234-123456789abc"
    probe = build_review_evidence(
        config,
        role,
        state,
        state.task,
        SuppliedContext("", ""),
        actual_id,
        _publication_candidate=candidate,
    )
    padding = probe.task_diff_capacity - candidate["characters"]
    assert 0 < padding < 100_000
    too_large_context = SuppliedContext("c" * (padding + 7), "c" * (padding + 7))
    with pytest.raises((EvidenceError, ValueError), match="capacity|part limit"):
        publication_review.prepare_part(config, role, state, too_large_context)
    assert not publication_review._part_path(descriptor, first).exists()
    exact_context = SuppliedContext("c" * padding, "c" * padding)
    part = publication_review.prepare_part(config, role, state, exact_context)
    actual = build_review_evidence(config, role, state, state.task, exact_context, actual_id)
    assert not actual.truncated
    assert part["selection"]["text"] == text
    assert publication_review.prepare_part(config, role, state, exact_context) == part
    with pytest.raises(EvidenceError, match="capacity"):
        publication_review.prepare_part(config, role, state, too_large_context)


@pytest.mark.parametrize(
    "reviewed", [{"role": "backend", "publication_review": False}], indirect=True
)
@pytest.mark.parametrize("outcome", ["pass", "changes_required"])
def test_consumed_part_transition_survives_pending_cleanup_interruption(
    reviewed, monkeypatch, outcome
):
    from scripts.dev_orchestrator import supervisor as supervisor_module

    state = _assign(reviewed)
    reviewed.outcomes.append(outcome)
    original_clear = supervisor_module.clear_pending_result
    interruptions = []

    def fail_after_commit(config, role, *, invocation_id):
        pending = read_pending_result(config, role)
        if pending and pending["task_id"] == state.task.task_id and not interruptions:
            interruptions.append(invocation_id)
            raise OSError("injected accepted transition cleanup interruption")
        return original_clear(config, role, invocation_id=invocation_id)

    with monkeypatch.context() as patch:
        patch.setattr(supervisor_module, "clear_pending_result", fail_after_commit)
        reviewed.supervisor.run_role(reviewed.role)
    assert len(interruptions) == 1
    invocation = interruptions[0]
    held = reviewed.supervisor.load_state(reviewed.role)
    assert held.last_result_invocation_id == invocation
    assert held.status is WorkerStatus.BLOCKED_SYSTEM
    assert held.blocker_kind == "system"
    if outcome == "pass":
        assert held.task is None and held.resume_status is WorkerStatus.IDLE
    else:
        assert held.task.task_id.startswith("PUBLICATION-REMEDIATE-")
        assert held.resume_status is WorkerStatus.IMPLEMENTING
    assert read_pending_result(reviewed.config, reviewed.role)["invocation_id"] == invocation
    call_count = len(reviewed.calls)
    ledger_before = reviewed.supervisor.completed_tasks.records(reviewed.role)
    observed = []

    def stop_before_next_model(*args, **kwargs):
        assert read_pending_result(reviewed.config, reviewed.role) is None
        observed.append(True)
        raise AgentInvocationError("codex", "paused", "test stops at the next fresh phase")

    restart = Supervisor(reviewed.config, reviewed.supervisor.invoker)
    monkeypatch.setattr(
        restart.invoker, "lead" if outcome == "pass" else "implement", stop_before_next_model
    )
    restart.run_role(reviewed.role)
    assert observed == [True]
    assert read_pending_result(reviewed.config, reviewed.role) is None
    assert len(reviewed.calls) == call_count
    assert restart.completed_tasks.records(reviewed.role) == ledger_before
    matches = [row for row in ledger_before if row["task_id"] == state.task.task_id]
    assert len(matches) == (1 if outcome == "pass" else 0)


@pytest.mark.parametrize(
    "reviewed",
    [
        {
            "role": "backend",
            "publication_review": False,
            "publication_file_sizes": (145_000, 100_000),
        }
    ],
    indirect=True,
)
@pytest.mark.parametrize("alter_second_input", [False, True])
def test_all_actual_parts_are_required_before_publication(reviewed, alter_second_input):
    from scripts.dev_orchestrator.github_publication import _proof, _Stop
    from scripts.dev_orchestrator.storage import AtomicJsonStore
    from tests.scripts.test_dev_orchestrator_github_publication import publish

    transport = Transport(reviewed)
    reviewed.network = transport
    state = _assign(reviewed)
    reviewed.supervisor.run_role(reviewed.role)
    assert reviewed.supervisor.load_state(reviewed.role).status is WorkerStatus.IDLE
    assert publish(reviewed, transport).status == "review_required"
    assert not transport.mutations
    with pytest.raises(_Stop) as missing:
        _proof(reviewed.config, reviewed.role)
    assert missing.value.status == "review_required"
    first_invocation = reviewed.calls[-1]["invocation_id"]
    first_task = state.task.task_id
    state = _assign(reviewed)
    assert state.task.task_id != first_task
    if alter_second_input:
        reviewed.prompt_transform = lambda text: text.replace(
            "## task_diff\n", "## task_diff\n+unreviewed injected content\n", 1
        )
    reviewed.supervisor.run_role(reviewed.role)
    if alter_second_input:
        held = reviewed.supervisor.load_state(reviewed.role)
        assert held.status is WorkerStatus.BLOCKED_SYSTEM
        assert "exact complete publication part" in held.blocked_reason
        assert all(
            row["task_id"] != state.task.task_id
            for row in reviewed.supervisor.completed_tasks.records(reviewed.role)
        )
        assert publish(reviewed, transport).status == "system_error"
        assert not transport.mutations
        return
    assert reviewed.supervisor.load_state(reviewed.role).status is WorkerStatus.IDLE
    _, proof, _ = _proof(reviewed.config, reviewed.role)
    group = proof["publication_review"]
    assert len(group["parts"]) == 2
    assert group["review_alias"] == "final_part_only"
    assert group["parts"][0]["proof"]["review"]["invocation_id"] == first_invocation
    assert proof["review"] == group["parts"][-1]["proof"]["review"]
    scope, complete = publication_review.read_scope_artifact(
        reviewed.config.coordination_root, group["scope"]
    )
    parts = [
        publication_review.read_part_artifact(
            reviewed.config.coordination_root,
            group["scope"],
            int(Path(item["part"]["path"]).stem.split("-")[-1]),
        )
        for item in group["parts"]
    ]
    publication_review.validate_parts(complete, [part["selection"] for part in parts])
    for item, part in zip(group["parts"], parts, strict=True):
        call = next(
            c
            for c in reviewed.calls
            if c["invocation_id"] == item["proof"]["review"]["invocation_id"]
        )
        assert call["phase"] == "review"
        assert part["selection"]["text"] in call["prompt"]
        assert scope["diff"]["sha256"] in call["prompt"]
    reviewed.review_again()
    assert _proof(reviewed.config, reviewed.role)[1] == proof
    ledger_path = reviewed.config.coordination_root / "completed-tasks" / f"{reviewed.role}.json"
    ledger_bytes = ledger_path.read_bytes()
    part_path = Path(group["parts"][1]["part"]["path"])
    part_bytes = part_path.read_bytes()
    for damage in ("missing_acceptance", "duplicate_acceptance", "altered_part"):
        records = AtomicJsonStore(ledger_path).read()
        if damage == "missing_acceptance":
            records = [row for row in records if row["task_id"] != first_task]
            AtomicJsonStore(ledger_path).write(records)
        elif damage == "duplicate_acceptance":
            records.append(next(row for row in records if row["task_id"] == first_task))
            AtomicJsonStore(ledger_path).write(records)
        else:
            damaged = AtomicJsonStore(part_path).read()
            damaged["selection"]["text"] += "changed"
            AtomicJsonStore(part_path).write(damaged)
        try:
            assert publish(reviewed, transport).status == "system_error"
            assert not transport.mutations
        finally:
            ledger_path.write_bytes(ledger_bytes)
            part_path.write_bytes(part_bytes)
    assert publish(reviewed, transport).status == "published"
    assert len(transport.mutations) == 2
    assert "2 accepted parts" in transport.body


@pytest.mark.parametrize(
    "reviewed", [{"role": "backend", "publication_review": False}], indirect=True
)
def test_normal_done_routes_to_host_review_without_an_implementation(reviewed, monkeypatch):
    from scripts.dev_orchestrator import github_publication
    from scripts.dev_orchestrator import supervisor as supervisor_module
    from scripts.dev_orchestrator.completion import CompletionGate

    transport = Transport(reviewed)
    monkeypatch.setattr(github_publication, "_run", transport)

    def observed_completion(config, role, **kwargs):
        return CompletionGate(
            False,
            ("Physical acceptance remains required",),
            needs_user=True,
            blocker_kind="acceptance",
            acceptance_reasons=("Physical acceptance remains required",),
            publication_reasons=(
                () if transport.mutations else ("A matching draft must be published",)
            ),
            head=reviewed.head,
        )

    monkeypatch.setattr(supervisor_module, "prepare_final_review", observed_completion)
    monkeypatch.setattr(supervisor_module, "completion_preflight", observed_completion)
    start = len(reviewed.calls)
    reviewed.supervisor.run_role(reviewed.role)
    assigned = reviewed.supervisor.load_state(reviewed.role)
    assert assigned.status is WorkerStatus.IMPLEMENTING
    assert publication_review.is_publication_review(assigned.task)
    assert read_pending_result(reviewed.config, reviewed.role) is None
    assert not transport.mutations
    reviewed.supervisor.run_role(reviewed.role)
    assert reviewed.supervisor.load_state(reviewed.role).status is WorkerStatus.IDLE
    reviewed.supervisor.run_role(reviewed.role)
    held = reviewed.supervisor.load_state(reviewed.role)
    assert held.status is WorkerStatus.BLOCKED_USER and held.blocker_kind == "acceptance"
    assert [call["phase"] for call in reviewed.calls[start:]] == ["plan", "review", "plan"]
    assert len(transport.mutations) == 2
    assert not (
        reviewed.config.coordination_root / "completion" / f"{reviewed.role}-acceptance.json"
    ).exists()


@pytest.mark.parametrize(
    "reviewed", [{"role": "backend", "publication_review": False}], indirect=True
)
def test_rejected_part_routes_to_real_remediation_then_a_new_scope(reviewed):
    from scripts.dev_orchestrator.github_publication import _proof, _Stop

    state = _assign(reviewed)
    old_head = reviewed.head
    old_scope, _text, old_descriptor, first = publication_review.validate_review_task(
        reviewed.config, reviewed.role, state.task, state.task_base_head, old_head
    )
    reviewed.outcomes.append("changes_required")
    reviewed.supervisor.run_role(reviewed.role)
    rejected_invocation = reviewed.calls[-1]["invocation_id"]
    rejected_path = (
        Path(old_descriptor["path"]).parent
        / "results"
        / str(first)
        / (rejected_invocation + ".json")
    )
    rejected_bytes = rejected_path.read_bytes()
    remediation = reviewed.supervisor.load_state(reviewed.role)
    assert remediation.status is WorkerStatus.IMPLEMENTING
    assert remediation.task.task_id.startswith("PUBLICATION-REMEDIATE-")
    assert "Repair publication-fixture.txt." in remediation.task.feedback
    reviewed.supervisor.run_role(reviewed.role)
    assert reviewed.head != old_head
    assert reviewed.supervisor.load_state(reviewed.role).status is WorkerStatus.REVIEWING
    reviewed.supervisor.run_role(reviewed.role)
    assert reviewed.supervisor.load_state(reviewed.role).status is WorkerStatus.IDLE
    with pytest.raises(_Stop) as missing:
        _proof(reviewed.config, reviewed.role)
    assert missing.value.status == "review_required"
    reviewed.ensure_publication_review()
    proof = _proof(reviewed.config, reviewed.role)[1]
    assert proof["head"] == reviewed.head
    assert proof["publication_review"]["scope"] != old_descriptor
    assert rejected_path.read_bytes() == rejected_bytes
    assert not (rejected_path.parent / "accepted.json").exists()
    assert old_scope["identity"]["head"] == old_head


@pytest.mark.parametrize("write_failure", [False, True])
def test_immutable_temp_cleanup_cannot_undo_commit_or_hide_write_failure(
    tmp_path, monkeypatch, caplog, write_failure
):
    destination = tmp_path / "scope.json"
    data = b'{"complete":"authority"}\n'
    original_unlink = Path.unlink

    def denied_unlink(path, *args, **kwargs):
        if path.name.endswith(".tmp"):
            raise PermissionError("injected staging cleanup denial")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied_unlink)
    if write_failure:

        def failed_fsync(_descriptor):
            raise OSError("original immutable write failure")

        monkeypatch.setattr(publication_review.os, "fsync", failed_fsync)
        with pytest.raises(OSError, match="original immutable write failure"):
            publication_review._save(tmp_path, destination, data)
        assert not destination.exists()
    else:
        publication_review._save(tmp_path, destination, data)
        assert destination.read_bytes() == data
    assert "publication staging cleanup failed" in caplog.text
    assert len(list(tmp_path.glob("*.tmp"))) == 1


def test_failed_exclusive_temp_open_preserves_the_preexisting_file(tmp_path, monkeypatch):
    from types import SimpleNamespace

    destination = tmp_path / "scope.json"
    temporary = tmp_path / ".scope.json.collision.tmp"
    temporary.write_bytes(b"preexisting unrelated staging bytes")
    monkeypatch.setattr(publication_review.uuid, "uuid4", lambda: SimpleNamespace(hex="collision"))
    with pytest.raises(FileExistsError):
        publication_review._save(tmp_path, destination, b"new authority")
    assert not destination.exists()
    assert temporary.read_bytes() == b"preexisting unrelated staging bytes"
