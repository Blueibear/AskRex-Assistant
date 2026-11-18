"""Durable model-reported blockers, separate from host and publication failures."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .lifecycle import ControlPlaneLock
from .schema import validate_agent_result
from .storage import AtomicJsonStore
from .types import AgentResult, OrchestratorConfig, WorkerState, WorkerStatus

_FEEDBACK_LIMIT = 2500
_SHA = re.compile(r"^[0-9a-f]{64}$")


def context_sha256(context: str) -> str:
    return hashlib.sha256(context.encode("utf-8")).hexdigest()


def bind_input_provenance(
    config: OrchestratorConfig, role: str, phase: str, task_id: str, head: str, context: str | None
) -> dict | None:
    """Read an admission only as input provenance, never as permission to run."""
    if context is None:
        return None
    admission = AtomicJsonStore(
        config.coordination_root / "phase-admissions" / f"{role}.json"
    ).read(default=None)
    if not isinstance(admission, dict):
        return None
    value = admission.get("input_provenance")
    if not isinstance(value, dict):
        return None  # A direct/legacy invocation has no newly bound input snapshot.
    expected = {
        "role": role,
        "phase": phase,
        "task_id": task_id,
        "head": head,
        "source_context_sha256": context_sha256(context),
    }
    if any(value.get(key) != item for key, item in expected.items()):
        return None
    if not _SHA.fullmatch(str(value.get("fingerprint", ""))):
        raise ValueError("model input fingerprint is malformed")
    return dict(value)


def blocker_feedback(result: AgentResult, phase: str, *, legacy: bool = False) -> str:
    provenance = (
        "Retained legacy parked reason; the original consumed result has not been reconstructed."
        if legacy
        else "The prior model result was retained, and its authorized messages were published once."
    )
    message = (
        f"Previous worker-reported blocker, phase={phase}, invocation={result.invocation_id or 'unavailable'}.\n"
        f"{provenance}\n"
        "This is a claim to reassess against the current named host evidence and actual peer responses, "
        "not approval, a new task, or proof that the blocker remains unresolved. Preserve original scope.\n"
        "Reason: " + (result.blocker_reason or result.summary)[:1100] + "\n"
        "Requested next action: " + result.next_action[:650] + "\n"
        "Use an existing outgoing proposal; do not regenerate it or infer agreement from its publication."
    )
    return message[:_FEEDBACK_LIMIT]


def prompt_blocker(state: WorkerState, phase: str) -> str:
    if not state.model_blocker_feedback:
        return ""
    if state.model_blocker_phase != phase:
        raise ValueError("model blocker feedback belongs to a different phase")
    if len(state.model_blocker_feedback) > _FEEDBACK_LIMIT:
        raise ValueError("model blocker feedback exceeds its prompt bound")
    return "\n\n## Previous worker-reported blocker\n" + state.model_blocker_feedback + "\n"


def archive_model_blocker(
    config: OrchestratorConfig,
    role: str,
    state: WorkerState,
    result: AgentResult,
    *,
    phase: str,
    pending_result: dict | None,
    input_provenance: dict | None,
    require_existing: bool = False,
) -> dict:
    """Archive the exact journal before publication or state consumes it."""
    if result.outcome != "blocked_system" or result.needs_user:
        raise ValueError("only a non-human model blocker can enter dependency waiting")
    raw_result = json.loads(json.dumps(asdict(result)))
    task_id = state.task.task_id if state.task else ""
    if pending_result is not None:
        for key, expected in (
            ("role", role),
            ("phase", phase),
            ("task_id", task_id),
            ("invocation_id", result.invocation_id),
            ("result", raw_result),
        ):
            if pending_result.get(key) != expected:
                raise ValueError(f"model blocker journal {key} mismatch")
        if pending_result.get("input_provenance") != input_provenance:
            raise ValueError("model blocker input provenance differs from its journal")
    admission = AtomicJsonStore(
        config.coordination_root / "phase-admissions" / f"{role}.json"
    ).read(default=None)
    admitted_input = admission.get("input_provenance") if isinstance(admission, dict) else None
    baseline = (
        {key: value for key, value in input_provenance.items() if key != "supplied_input"}
        if isinstance(input_provenance, dict)
        else None
    )
    if pending_result is not None and admitted_input is not None:
        if baseline != admitted_input:
            raise ValueError("model blocker input differs from its durable admission snapshot")
    if input_provenance is not None:
        if not isinstance(admission, dict) or admitted_input != baseline:
            raise ValueError("model blocker input admission is unavailable")
        if pending_result is not None:
            read_supplied_input(
                config, role, phase, task_id, result.invocation_id, input_provenance
            )
        for key, expected in (
            ("role", role),
            ("phase", phase),
            ("task_id", task_id),
            ("admission_id", input_provenance.get("admission_id")),
        ):
            if admission.get(key) != expected:
                raise ValueError(f"model blocker admission {key} mismatch")
        if not _SHA.fullmatch(str(input_provenance.get("source_context_sha256", ""))):
            raise ValueError("model blocker supplied context digest is malformed")
    if input_provenance is not None:
        for key, expected in (("role", role), ("phase", phase), ("task_id", task_id)):
            if input_provenance.get(key) != expected:
                raise ValueError(f"model blocker input {key} mismatch")
        if not _SHA.fullmatch(str(input_provenance.get("fingerprint", ""))):
            raise ValueError("model blocker input fingerprint is malformed")
        if pending_result is not None and input_provenance.get("head") != pending_result.get(
            "pre_head"
        ):
            raise ValueError("model blocker input revision differs from its journal")
    payload = {
        "schema_version": 1,
        "kind": "model_reported_dependency",
        "role": role,
        "phase": phase,
        "task_id": task_id,
        "task_base_head": state.task_base_head,
        "invocation_id": result.invocation_id,
        "result": raw_result,
        "pending_result": pending_result,
        "input_provenance": input_provenance,
    }
    identity = result.invocation_id or json.dumps(payload, sort_keys=True)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    path = config.coordination_root / "model-blockers" / f"{role}-{digest}.json"
    with ControlPlaneLock(config.coordination_root / "control-plane.lock"):
        store = AtomicJsonStore(path)
        if path.exists():
            existing = store.read()
            if not isinstance(existing, dict):
                raise ValueError("model blocker archive is malformed")
            previous = dict(existing)
            stamp = previous.pop("observed_at", None)
            if not isinstance(stamp, str) or previous != payload:
                raise ValueError("conflicting immutable model blocker archive")
            return existing
        if require_existing:
            raise ValueError("consumed model blocker archive is missing")
        record = {**payload, "observed_at": datetime.now(UTC).isoformat()}
        store.write(record)
        return record


def validate_consumed_blocker(
    config: OrchestratorConfig, role: str, state: WorkerState, pending: dict
) -> None:
    if (
        not state.last_result_invocation_id
        or pending.get("invocation_id") != state.last_result_invocation_id
    ):
        raise ValueError("consumed model blocker invocation mismatch")
    result = validate_agent_result(pending.get("result", {}))
    if result.role != role or result.invocation_id != state.last_result_invocation_id:
        raise ValueError("consumed model blocker identity mismatch")
    record = archive_model_blocker(
        config,
        role,
        state,
        result,
        phase=state.model_blocker_phase,
        pending_result=pending,
        input_provenance=pending.get("input_provenance"),
        require_existing=True,
    )
    fingerprint = (record.get("input_provenance") or {}).get("fingerprint", "")
    if (
        state.model_blocker_fingerprint != fingerprint
        or state.model_blocker_feedback != blocker_feedback(result, state.model_blocker_phase)
    ):
        raise ValueError("consumed model blocker state differs from its archive")


_INPUT_LIMIT = 2_000_000


def capture_supplied_input(
    config: OrchestratorConfig,
    role: str,
    phase: str,
    task_id: str,
    invocation_id: str,
    head: str,
    source_context: str | None,
    supplied_text: str,
    *,
    input_kind: str,
) -> dict | None:
    """Capture exact provider input before execution, without rescanning its source baseline."""
    import os

    admission = bind_input_provenance(config, role, phase, task_id, head, source_context)
    if admission is None:
        return None
    phase_admission = AtomicJsonStore(
        config.coordination_root / "phase-admissions" / f"{role}.json"
    ).read()
    if (
        not isinstance(phase_admission, dict)
        or phase_admission.get("input_provenance") != admission
    ):
        raise ValueError("model input admission changed before capture")
    data = supplied_text.encode("utf-8")
    if len(data) > _INPUT_LIMIT:
        raise ValueError("rendered model input exceeds its capture bound")
    digest = hashlib.sha256(invocation_id.encode("utf-8")).hexdigest()
    directory = config.coordination_root / "model-inputs" / f"{role}-{digest}"
    expected = config.coordination_root.resolve() / "model-inputs" / f"{role}-{digest}"
    # Resolve the namespace before mkdir: an existing junction must cause no
    # outside side effect, even an empty per-invocation directory.
    if directory.resolve() != expected:
        raise ValueError("supplied model input directory escapes its trusted root")
    directory.mkdir(parents=True, exist_ok=True)
    if directory.resolve() != expected:
        raise ValueError("supplied model input directory escapes its trusted root")
    path = directory / "input.txt"
    if path.is_symlink() or (path.exists() and path.resolve() != directory.resolve() / "input.txt"):
        raise ValueError("supplied model input path is not trusted")
    descriptor = {
        "kind": input_kind,
        "path": path.relative_to(config.coordination_root).as_posix(),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    provenance = {**admission, "supplied_input": descriptor}
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        read_supplied_input(config, role, phase, task_id, invocation_id, provenance)
        return provenance
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("conflicting immutable supplied model input")
    else:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    AtomicJsonStore(manifest_path).write(
        {
            "schema_version": 1,
            "kind": "model_supplied_input",
            "role": role,
            "phase": phase,
            "task_id": task_id,
            "invocation_id": invocation_id,
            "admission": admission,
            "phase_admission": phase_admission,
            "supplied_input": descriptor,
            "captured_at": datetime.now(UTC).isoformat(),
        }
    )
    read_supplied_input(config, role, phase, task_id, invocation_id, provenance)
    return provenance


def read_supplied_input(
    config: OrchestratorConfig,
    role: str,
    phase: str,
    task_id: str,
    invocation_id: str,
    provenance: dict,
) -> tuple[dict, bytes]:
    """Validate immutable actual-input bytes; read-only and no lease/control lock writes."""
    import stat

    digest = hashlib.sha256(invocation_id.encode("utf-8")).hexdigest()
    root = config.coordination_root.resolve()
    directory = root / "model-inputs" / f"{role}-{digest}"
    path = directory / "input.txt"
    manifest_path = directory / "manifest.json"
    for item in (path, manifest_path):
        metadata = item.lstat()
        if (
            item.resolve() != item
            or item.is_symlink()
            or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or metadata.st_size > _INPUT_LIMIT
        ):
            raise ValueError("supplied model input is outside its trusted bound")
    data = path.read_bytes()
    descriptor = provenance.get("supplied_input")
    if not isinstance(descriptor, dict) or descriptor.get("kind") not in {
        "cli_prompt_utf8",
        "openai_request_input_json",
    }:
        raise ValueError("supplied model input descriptor is malformed")
    expected_descriptor = {
        "kind": descriptor["kind"],
        "path": path.relative_to(root).as_posix(),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if descriptor != expected_descriptor:
        raise ValueError("supplied model input bytes differ from their descriptor")
    record = AtomicJsonStore(manifest_path).read()
    if not isinstance(record, dict):
        raise ValueError("supplied model input manifest is malformed")
    original = dict(record)
    stamp = original.pop("captured_at", None)
    if not isinstance(stamp, str):
        raise ValueError("supplied model input timestamp is missing")
    parsed = datetime.fromisoformat(stamp)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("supplied model input timestamp is not timezone-aware")
    admission = {key: value for key, value in provenance.items() if key != "supplied_input"}
    phase_admission = record.get("phase_admission")
    if not isinstance(phase_admission, dict):
        raise ValueError("supplied model input phase admission is missing")
    for key, expected_value in (
        ("schema_version", 1),
        ("kind", "model_phase_admission"),
        ("role", role),
        ("phase", phase),
        ("task_id", task_id),
        ("admission_id", admission.get("admission_id")),
        ("input_provenance", admission),
    ):
        if phase_admission.get(key) != expected_value:
            raise ValueError("supplied model input phase admission binding mismatch")
    admitted_at = datetime.fromisoformat(str(phase_admission.get("admitted_at", "")))
    if (
        admitted_at.tzinfo is None
        or admitted_at.utcoffset() is None
        or admitted_at > parsed
        or type(phase_admission.get("pid")) is not int
        or phase_admission["pid"] <= 0
    ):
        raise ValueError("supplied model input phase admission is invalid")
    expected = {
        "schema_version": 1,
        "kind": "model_supplied_input",
        "role": role,
        "phase": phase,
        "task_id": task_id,
        "invocation_id": invocation_id,
        "admission": admission,
        "phase_admission": phase_admission,
        "supplied_input": descriptor,
    }
    if original != expected:
        raise ValueError("supplied model input manifest binding mismatch")
    return record, data


def plan_input_fingerprint(role: str, provenance: dict | None, head: str) -> str:
    """A done claim needs its original admitted input, not a post-result baseline."""
    if (
        not isinstance(provenance, dict)
        or any(
            provenance.get(key) != expected
            for key, expected in (
                ("role", role),
                ("phase", "plan"),
                ("task_id", ""),
                ("head", head),
            )
        )
        or not isinstance(provenance.get("admission_id"), str)
        or not provenance["admission_id"].strip()
        or any(
            not isinstance(provenance.get(key), str) or not _SHA.fullmatch(provenance[key])
            for key in ("fingerprint", "source_context_sha256")
        )
    ):
        raise ValueError("original planning input provenance is missing or invalid")
    return provenance["fingerprint"]


def require_planning_hold_lineage(
    config: OrchestratorConfig,
    role: str,
    state: WorkerState,
    head: str,
    *,
    expected_pending: dict | None = None,
) -> None:
    """Require the consumed phase to belong to the current validated role handoff."""
    record = AtomicJsonStore(config.coordination_root / "handoff" / f"{role}.json").read(
        default=None
    )
    repo = config.backend_root if role == "backend" else config.mobile_root
    if (
        not isinstance(record, dict)
        or repo is None
        or not head
        or state.task_base_head != head
        or not state.last_result_invocation_id
        or record.get("role") != role
        or record.get("owner") != "supervisor"
        or not isinstance(record.get("lease_id"), str)
        or not record["lease_id"].strip()
        or record.get("head") != head
        or record.get("last_invocation_id") != state.last_result_invocation_id
        or Path(str(record.get("worktree", ""))).resolve() != repo.resolve()
        or (
            "pending_result" in record
            if expected_pending is None
            else record.get("pending_result") != expected_pending
        )
    ):
        raise ValueError("taskless planning hold no longer matches its invocation lease")


def validate_consumed_completion_result(
    config: OrchestratorConfig, role: str, state: WorkerState, pending: dict, head: str
) -> None:
    """Validate cleanup of an already-applied done/final-pass result without replaying it."""
    phase = pending.get("phase")
    task_id = pending.get("task_id")
    expected_outcome = (
        "done"
        if phase == "plan" and task_id == ""
        else "pass" if phase == "review" and task_id == f"FINAL-VERIFY-{role}" else ""
    )
    if (
        not expected_outcome
        or state.status is not WorkerStatus.BLOCKED_USER
        or state.blocker_kind != "acceptance"
        or state.task is not None
        or pending.get("role") != role
        or pending.get("invocation_id") != state.last_result_invocation_id
        or pending.get("pre_head") != head
        or pending.get("post_head") != head
    ):
        raise ValueError("consumed completion result identity mismatch")
    result = validate_agent_result(pending.get("result", {}))
    if (
        result.role != role
        or result.invocation_id != state.last_result_invocation_id
        or (phase == "review" and result.task_id != task_id)
        or result.outcome != expected_outcome
        or result.needs_user
    ):
        raise ValueError("consumed completion result does not match its applied phase")
    provenance = pending.get("input_provenance")
    if not isinstance(provenance, dict) or any(
        provenance.get(key) != expected
        for key, expected in (
            ("role", role),
            ("phase", phase),
            ("task_id", task_id),
            ("head", head),
        )
    ):
        raise ValueError("consumed completion result input provenance mismatch")
    read_supplied_input(config, role, phase, task_id, state.last_result_invocation_id, provenance)
    if phase == "plan":
        baseline = plan_input_fingerprint(role, provenance, head)
        if state.planning_input_fingerprint and state.planning_input_fingerprint != baseline:
            raise ValueError("consumed planning result baseline mismatch")
    require_planning_hold_lineage(config, role, state, head, expected_pending=pending)


def read_consumed_plan_fingerprint(
    config: OrchestratorConfig, role: str, state: WorkerState, head: str
) -> str:
    """Recover only the input baseline of a legacy taskless completion hold."""
    import stat

    if (
        state.role != role
        or state.status is not WorkerStatus.BLOCKED_USER
        or state.blocker_kind != "acceptance"
        or state.task is not None
        or state.resume_status is not WorkerStatus.PLANNING
    ):
        raise ValueError("legacy planning baseline requires a taskless acceptance hold")
    invocation_id = state.last_result_invocation_id
    if not invocation_id:
        return ""
    digest = hashlib.sha256(invocation_id.encode("utf-8")).hexdigest()
    root = config.coordination_root.resolve()
    path = root / "model-inputs" / f"{role}-{digest}" / "manifest.json"
    if not path.exists():
        return ""  # An older unproven hold does not acquire automatic resume authority.
    metadata = path.lstat()
    if (
        path.resolve() != path
        or path.is_symlink()
        or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or metadata.st_size > _INPUT_LIMIT
    ):
        raise ValueError("legacy planning input is outside its trusted bound")
    record = AtomicJsonStore(path).read()
    if not isinstance(record, dict) or not isinstance(record.get("admission"), dict):
        raise ValueError("legacy planning input manifest is malformed")
    admission = record["admission"]
    provenance = {**admission, "supplied_input": record.get("supplied_input")}
    read_supplied_input(config, role, "plan", "", invocation_id, provenance)
    if (
        any(
            admission.get(key) != expected
            for key, expected in (
                ("role", role),
                ("phase", "plan"),
                ("task_id", ""),
                ("head", head),
            )
        )
        or state.task_base_head != head
    ):
        raise ValueError("legacy planning input does not match its held checkpoint")
    if not _SHA.fullmatch(str(admission.get("fingerprint", ""))) or not _SHA.fullmatch(
        str(admission.get("source_context_sha256", ""))
    ):
        raise ValueError("legacy planning input digest is malformed")
    require_planning_hold_lineage(config, role, state, head)
    return admission["fingerprint"]


def record_dependency_check(
    config: OrchestratorConfig, role: str, state: WorkerState, current_fingerprint: str
) -> None:
    """Record an actual eligible wait without making logs part of its wake inputs."""
    import os

    if not state.last_result_invocation_id or not state.model_blocker_fingerprint:
        return
    digest = hashlib.sha256(state.last_result_invocation_id.encode("utf-8")).hexdigest()
    archive = config.coordination_root / "model-blockers" / f"{role}-{digest}.json"
    record = AtomicJsonStore(archive).read()
    if not isinstance(record, dict) or (
        record.get("kind") != "model_reported_dependency"
        or record.get("role") != role
        or record.get("phase") != state.model_blocker_phase
        or record.get("task_id") != (state.task.task_id if state.task else "")
        or record.get("invocation_id") != state.last_result_invocation_id
    ):
        raise ValueError("dependency check has no matching model blocker record")
    provenance = record.get("input_provenance")
    if not isinstance(provenance, dict) or (
        provenance.get("fingerprint") != state.model_blocker_fingerprint
        or current_fingerprint != state.model_blocker_fingerprint
    ):
        raise ValueError("dependency check fingerprint differs from the supplied baseline")
    with ControlPlaneLock(config.coordination_root / "control-plane.lock"):
        policy = AtomicJsonStore(config.coordination_root / "orchestrator-config.json").read()
        if (
            config.observe_only
            or not isinstance(policy, dict)
            or policy.get("observe_only") is not False
        ):
            return
        event = {
            "schema_version": 1,
            "kind": "model_dependency_wait",
            "checked_at": datetime.now(UTC).isoformat(),
            "role": role,
            "task_id": state.task.task_id if state.task else "",
            "phase": state.model_blocker_phase,
            "blocked_invocation_id": state.last_result_invocation_id,
            "admission_id": provenance.get("admission_id"),
            "source_context_sha256": provenance.get("source_context_sha256"),
            "external_fingerprint": state.model_blocker_fingerprint,
            "current_external_fingerprint": current_fingerprint,
            "runtime_pid": os.getpid(),
            "observe_only": False,
        }
        path = config.coordination_root / "logs/model-dependency-checks.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if (
            path.resolve()
            != config.coordination_root.resolve() / "logs/model-dependency-checks.jsonl"
        ):
            raise ValueError("dependency log path escapes its trusted root")
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, sort_keys=True, ensure_ascii=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
