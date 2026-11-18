"""Explicit paused recovery of a consumed implementation's review-evidence hold.

This only restores the same checkpoint to REVIEWING. It never executes a model,
validation command, publication, acceptance, or completed-task transition.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .coordination_checks import read_check_summary
from .evidence import build_review_evidence
from .handoff import role_lease_lock_path, validate_handoff, verify_invocation_commits
from .lifecycle import ControlPlaneLock
from .model_blockers import read_supplied_input
from .postprocessing import _read_blob, repository_changes_allowed
from .schema import validate_agent_result, validate_phase_outcome
from .storage import AtomicJsonStore
from .types import OrchestratorConfig, WorkerState, WorkerStatus
from .validation import (
    _coordination_only_gates,
    _gates_for,
    _has_task_validation_override,
    _scoped_backend_gates_from_diff,
    load_validation_receipt,
)
from .worker_context import build_worker_context

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_HEAD = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_JSON_LIMIT = 2_000_000
_BLOB_LIMIT = 16_000_000


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError("review evidence recovery refused: " + message)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stamp(value: object) -> datetime:
    _require(isinstance(value, str), "missing evidence timestamp")
    stamp = datetime.fromisoformat(value)
    _require(
        stamp.tzinfo is not None and stamp.utcoffset() is not None and stamp <= datetime.now(UTC),
        "invalid evidence timestamp",
    )
    return stamp


class _Proof:
    """Bound ordinary files before readers run and recheck exact bytes before save."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.files: dict[str, tuple[bytes, int]] = {}
        self.absent: set[str] = set()

    def _read(self, relative: str, limit: int) -> bytes:
        path = self.root / relative
        _require(not Path(relative).is_absolute(), "absolute evidence path")
        _require(path.resolve() == path and self.root in path.parents, "redirected evidence path")
        metadata = path.lstat()
        _require(
            stat.S_ISREG(metadata.st_mode)
            and not path.is_symlink()
            and not getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            and metadata.st_size <= limit,
            "evidence is not an ordinary bounded file: " + relative,
        )
        data = path.read_bytes()
        _require(len(data) == metadata.st_size, "evidence changed while reading: " + relative)
        return data

    def read(self, relative: str, limit: int = _JSON_LIMIT) -> bytes:
        data = self._read(relative, limit)
        previous = self.files.setdefault(relative, (data, limit))
        _require(previous[0] == data, "evidence changed during recovery: " + relative)
        return data

    def json(self, relative: str) -> dict:
        value = json.loads(self.read(relative))
        _require(isinstance(value, dict), "evidence is not an object: " + relative)
        return value

    def require_absent(self, relative: str) -> None:
        path = self.root / relative
        _require(not path.exists() and not path.is_symlink(), "unresolved activity: " + relative)
        self.absent.add(relative)

    def recheck(self) -> None:
        for relative, (data, limit) in self.files.items():
            _require(
                self._read(relative, limit) == data, "evidence changed before save: " + relative
            )
        for relative in tuple(self.absent):
            self.require_absent(relative)


def _admission(record: dict, role: str, phase: str, task_id: str, head: str) -> datetime:
    provenance = record.get("input_provenance")
    _require(isinstance(provenance, dict), "missing admitted input provenance")
    for key, value in (
        ("schema_version", 1),
        ("kind", "model_phase_admission"),
        ("role", role),
        ("phase", phase),
        ("task_id", task_id),
    ):
        _require(record.get(key) == value, "phase admission " + key + " mismatch")
    for key, value in (
        ("role", role),
        ("phase", phase),
        ("task_id", task_id),
        ("head", head),
        ("admission_id", record.get("admission_id")),
    ):
        _require(provenance.get(key) == value, "admitted input " + key + " mismatch")
    _require(
        bool(re.fullmatch(r"[0-9a-f]{32}", str(record.get("admission_id", ""))))
        and type(record.get("pid")) is int
        and record["pid"] > 0
        and bool(_DIGEST.fullmatch(str(provenance.get("fingerprint", ""))))
        and bool(_DIGEST.fullmatch(str(provenance.get("source_context_sha256", "")))),
        "malformed phase admission",
    )
    return _stamp(record.get("admitted_at"))


def _completed_implementation(
    config: OrchestratorConfig, state: WorkerState, lease: dict, proof: _Proof
) -> tuple[str, datetime, datetime]:
    from .runner import extract_agent_result

    task = state.task
    assert task is not None
    role, invocation = state.role, state.last_result_invocation_id
    directory = f"postprocessing-failures/{role}-{invocation}"
    record = proof.json(directory + "/completed-result.json")
    pre_head = record.get("pre_head", "")
    _require(isinstance(pre_head, str) and bool(_HEAD.fullmatch(pre_head)), "invalid pre-HEAD")
    for key, value in (
        ("schema_version", 1),
        ("kind", "completed_implementation_result"),
        ("role", role),
        ("task_id", task.task_id),
        ("invocation_id", invocation),
        ("task_base_head", state.task_base_head),
        ("task_prompt_sha256", _digest(task.prompt.encode("utf-8"))),
        ("provider", lease.get("last_provider")),
    ):
        _require(record.get(key) == value, "completed implementation " + key + " mismatch")
    _require(record["provider"] in {"codex", "claude"}, "not a local implementation result")
    _require(
        record.get("repository_changes_allowed") is repository_changes_allowed(task),
        "original repository policy changed",
    )
    _require(
        record.get("lease")
        == {
            key: pre_head if key == "head" else lease[key]
            for key in ("lease_id", "worktree", "branch", "head")
        },
        "original implementation lease changed",
    )
    _require(
        record.get("provenance") == {"source": "runner_stdout_utf8_text", "process_exit_code": 0},
        "implementation did not exit successfully",
    )
    result = validate_agent_result(record["result"])
    validate_phase_outcome(result, "implement")
    _require(
        result.outcome == "ready_for_review"
        and result.needs_user is False
        and result.role == role
        and result.task_id == task.task_id
        and result.invocation_id == invocation
        and result.task_prompt in {"", task.prompt},
        "original result is not this task's ready-for-review checkpoint",
    )
    blobs = {}
    for key, name in (
        ("model_stdout", "model-stdout.log"),
        ("model_stderr", "model-stderr.log"),
        ("owner", "scratch-owner.json"),
        ("marker", "activity-marker.json"),
    ):
        reference = record.get(key)
        _require(isinstance(reference, dict), "missing completed artifact: " + key)
        relative = directory + "/" + name
        _require(reference.get("path") == relative, "unexpected completed artifact path")
        blobs[key] = proof.read(relative, _BLOB_LIMIT)
        _require(
            _read_blob(config, proof.root / directory, reference) == blobs[key],
            "completed artifact changed",
        )
    _require(
        extract_agent_result(blobs["model_stdout"].decode("utf-8")) == result,
        "original stdout differs from its retained result",
    )
    owner, marker = (json.loads(blobs[name]) for name in ("owner", "marker"))
    _require(isinstance(owner, dict) and isinstance(marker, dict), "malformed ownership capture")
    scratch = record.get("scratch_path")
    _require(isinstance(scratch, str) and Path(scratch).is_absolute(), "invalid historical scratch")
    # The scratch is normally deleted after publication. Archived ownership is
    # checked as historical evidence; a recycled PID is never treated as authority.
    for artifact in (owner, marker):
        for key, value in (
            ("role", role),
            ("invocation_id", invocation),
            ("pre_head", pre_head),
            ("scratch_path", scratch),
        ):
            _require(artifact.get(key) == value, "historical ownership " + key + " mismatch")
    _require(
        bool(re.fullmatch(r"[0-9a-f]{32}", str(owner.get("nonce", ""))))
        and marker.get("scratch_nonce") == owner["nonce"]
        and marker.get("provider") == record["provider"]
        and marker.get("status") == "postprocessing"
        and type(marker.get("pid")) is int
        and marker["pid"] > 0,
        "historical completed marker mismatch",
    )
    started, ended = (
        _stamp(marker.get(key)) for key in ("process_started_at", "process_completed_at")
    )
    _require(started <= ended, "implementation completion precedes its start")

    input_directory = f"model-inputs/{role}-{_digest(invocation.encode('utf-8'))}"
    manifest = proof.json(input_directory + "/manifest.json")
    supplied = proof.read(input_directory + "/input.txt")
    admission = manifest.get("admission")
    _require(isinstance(admission, dict), "missing original implementation admission")
    validated, actual = read_supplied_input(
        config,
        role,
        "implement",
        task.task_id,
        invocation,
        {**admission, "supplied_input": manifest.get("supplied_input")},
    )
    _require(validated == manifest and actual == supplied, "original input changed while reading")
    admitted = _admission(manifest["phase_admission"], role, "implement", task.task_id, pre_head)
    _require(admitted <= _stamp(manifest.get("captured_at")) <= started, "input timing mismatch")
    _require(
        manifest["supplied_input"]["kind"] == "cli_prompt_utf8"
        and f"Task {task.task_id}: {task.prompt}." in supplied.decode("utf-8")
        and f"AskRex-Orchestrator-Invocation-ID: {invocation}" in supplied.decode("utf-8"),
        "original supplied input does not identify this task and invocation",
    )
    repo = Path(lease["worktree"])
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", state.task_base_head, pre_head],
        cwd=repo,
        capture_output=True,
        check=False,
        timeout=30,
    )
    _require(ancestor.returncode == 0, "task base is not an ancestor of original pre-HEAD")
    verify_invocation_commits(repo, pre_head, lease["head"], invocation)
    _require(
        record["repository_changes_allowed"] or pre_head == lease["head"],
        "read-only task moved HEAD",
    )
    return pre_head, admitted, ended


def _gate_identity(gate, repo: Path) -> tuple[str, tuple[str, ...], Path]:
    cwd = (repo / gate.cwd).resolve()
    _require(cwd == repo or repo in cwd.parents, "validation gate directory escapes its lease")
    return gate.name, gate.command, cwd


def _canonical_checks(config, state, lease, proof, pre_head, admitted, ended, review_at):
    from .supervisor import _is_coordination_only_task

    task = state.task
    role, head, base = state.role, lease["head"], state.task_base_head
    receipt_path = f"validation/{role}-{task.task_id}.json"
    raw_receipt = proof.json(receipt_path)
    receipt = load_validation_receipt(config, role, task.task_id, base_head=base, head=head)
    gates = _gates_for(config, role, task.task_id)
    _require(bool(gates), "canonical iteration validation is disabled")
    if not _has_task_validation_override(config, role, task.task_id):
        gates = (
            _coordination_only_gates(config, role, base_head=base, head=head)
            if _is_coordination_only_task(task)
            else _scoped_backend_gates_from_diff(config, role, base_head=base, head=head) or gates
        )
    repo = Path(lease["worktree"]).resolve()
    expected = [_gate_identity(gate, repo) for gate in gates]
    _require(
        [_gate_identity(gate, repo) for gate in receipt.gates] == expected
        and all(
            type(gate.get("exit_code")) is int and gate["exit_code"] == 0
            for gate in raw_receipt["gates"]
        ),
        "receipt does not prove the current canonical gates",
    )
    manifest = proof.json(f"evidence/supervisor-checks/{role}-{task.task_id}.json")
    scope = manifest.get("scope")
    _require(
        scope in {"implementation-attempt", "recovered-checkpoint-validation"},
        "unknown check scope",
    )
    script_digest = _digest(proof.read("scripts/check-coordination.ps1"))
    for phase in ("initial", "final"):
        record = manifest.get(phase)
        _require(isinstance(record, dict), "missing " + phase + " coordination capture")
        _require(record.get("script_sha256") == script_digest, "coordination script changed")
        for stream in ("stdout", "stderr"):
            relative = record.get(stream + "_path")
            _require(
                isinstance(relative, str) and relative.startswith("evidence/supervisor-checks/"),
                "invalid capture path",
            )
            proof.read(relative, _BLOB_LIMIT)
    read_check_summary(config, role, task, base, head, strict=True)
    initial, final = manifest["initial"], manifest["final"]
    _require(
        initial["head"] == (pre_head if scope == "implementation-attempt" else head)
        and initial["lease"]["lease_id"] == lease["lease_id"],
        "opening coordination capture belongs to another checkpoint",
    )
    opened, closed, passed = (
        _stamp(initial["observed_at"]),
        _stamp(final["observed_at"]),
        _stamp(receipt.passed_at),
    )
    _require(ended <= passed <= closed <= review_at, "validation/review lineage timing mismatch")
    _require(
        opened <= admitted if scope == "implementation-attempt" else ended <= opened,
        "opening capture timing mismatch",
    )


def revalidate_review_evidence(config: OrchestratorConfig, role: str) -> dict:
    """Restore only a proven regular review hold, under the existing OS locks."""
    from .cli import load_config
    from .supervisor import _state_from_dict

    _require(role in {"backend", "mobile"}, "unsupported role")
    _require(config.observe_only is True, "pause the supervisor first")
    proof = _Proof(config.coordination_root)
    state_path = f"state/{role}.json"
    with ControlPlaneLock(role_lease_lock_path(config, role)):
        with ControlPlaneLock(proof.root / "control-plane.lock", timeout_seconds=2):
            policy = proof.json("orchestrator-config.json")
            _require(
                policy.get("observe_only") is True and load_config(proof.root) == config,
                "paused configuration changed",
            )
            raw_state = proof.json(state_path)
            state = _state_from_dict(role, raw_state)
            _require(
                raw_state.get("role") == role
                and state.status is WorkerStatus.BLOCKED_SYSTEM
                and state.blocker_kind == "incomplete_evidence"
                and state.resume_status is None
                and raw_state.get("resume_status") is None
                and bool(state.blocked_reason)
                and state.task is not None
                and bool(_SAFE_ID.fullmatch(state.task.task_id))
                and not state.task.task_id.startswith("FINAL-VERIFY-")
                and not state.completion_host_action
                and not state.result_contract_phase
                and not state.model_blocker_phase,
                "not a regular incomplete-review-evidence hold",
            )
            _require(bool(_HEAD.fullmatch(state.task_base_head)), "missing exact task base")
            _require(
                str(uuid.UUID(state.last_result_invocation_id)) == state.last_result_invocation_id,
                "invalid consumed invocation",
            )
            for key in (
                "iteration",
                "implementation_failures",
                "review_failures",
                "result_contract_failures",
            ):
                value = raw_state.get(key, 0)
                _require(type(value) is int and value >= 0, "invalid checkpoint counter: " + key)
            _require(state.iteration > 0, "implementation has not been consumed")
            for directory in ("active-agents", "cloud-sessions"):
                proof.require_absent(f"{directory}/{role}.json")
            for suffix in ("request.json", "ack.json"):
                proof.require_absent(f"handoff/{role}.{suffix}")
            lease = proof.json(f"handoff/{role}.json")
            _require(lease.get("pending_result") is None, "a result is still pending")
            _require(
                lease.get("last_invocation_id") == state.last_result_invocation_id,
                "consumed result differs from the lease",
            )
            validate_handoff(config, role)
            review = proof.json(f"phase-admissions/{role}.json")
            review_at = _admission(review, role, "review", state.task.task_id, lease["head"])

        pre_head, admitted, ended = _completed_implementation(config, state, lease, proof)
        _canonical_checks(config, state, lease, proof, pre_head, admitted, ended, review_at)
        context = build_worker_context(config, role, state, lease["head"])
        bundle = build_review_evidence(
            config, role, state, state.task, context.text, "review-evidence-revalidation"
        )
        _require(
            not bundle.truncated
            and not bundle.truncation_reasons
            and bundle.base_head == state.task_base_head
            and bundle.head == lease["head"],
            "current review evidence is incomplete: " + ", ".join(bundle.truncation_reasons),
        )
        with ControlPlaneLock(proof.root / "control-plane.lock", timeout_seconds=2):
            proof.recheck()
            validate_handoff(config, role)
            # Preserve the exact current task, all counters, prior result identity,
            # unknown state extensions, and every ledger. Normal review runs later.
            resumed = {
                **raw_state,
                "status": WorkerStatus.REVIEWING.value,
                "blocked_reason": "",
                "blocker_kind": "",
            }
            AtomicJsonStore(proof.root / state_path).write(resumed)
            state_after = (proof.root / state_path).read_bytes()
        return {
            "role": role,
            "task_id": state.task.task_id,
            "status": "reviewing",
            "base_head": state.task_base_head,
            "head": lease["head"],
            "implementation_invocation_id": state.last_result_invocation_id,
            "evidence_sha256": _digest(bundle.text.encode("utf-8")),
            "evidence_chars": len(bundle.text),
            "state_before_sha256": _digest(proof.files[state_path][0]),
            "state_after_sha256": _digest(state_after),
            "validated_artifacts": {
                name: _digest(value[0]) for name, value in sorted(proof.files.items())
            },
        }
