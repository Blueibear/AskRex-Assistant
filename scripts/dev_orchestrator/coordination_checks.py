"""Revision-bound coordination checks and observations of exclusive role ownership.

The existing handoff and OS lock are the authority. Captures provide evidence,
not new authority, independent approval, or proof about unregistered processes.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .completion import load_final_validation_receipt
from .handoff import HandoffRequired, role_lease_lock_path, validate_handoff
from .lifecycle import ControlPlaneLock
from .storage import AtomicJsonStore, atomic_write_text
from .types import OrchestratorConfig, TaskItem
from .validation import load_validation_receipt

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z", re.IGNORECASE)


class CoordinationCheckError(ValueError):
    pass


def _manifest_path(config: OrchestratorConfig, role: str, task: TaskItem) -> Path:
    if role not in {"backend", "mobile"} or not _SAFE_ID.fullmatch(task.task_id):
        raise CoordinationCheckError("invalid role or task identity for coordination evidence")
    return config.coordination_root / "evidence/supervisor-checks" / f"{role}-{task.task_id}.json"


def _check_script(config: OrchestratorConfig) -> Path:
    return config.coordination_root / "scripts/check-coordination.ps1"


def _checks_required(config: OrchestratorConfig, role: str) -> bool:
    # A leased production workstream cannot silently omit its mandatory script.
    return (
        _check_script(config).exists()
        or (config.coordination_root / "handoff" / f"{role}.json").exists()
    )


def _read(path: Path) -> dict | None:
    try:
        value = AtomicJsonStore(path).read(default=None)
    except (OSError, ValueError) as exc:
        raise CoordinationCheckError("cannot read coordination check manifest") from exc
    if value is not None and not isinstance(value, dict):
        raise CoordinationCheckError("malformed coordination check manifest")
    return value


def _writer_state(config: OrchestratorConfig, role: str, head: str) -> dict:
    """Call only while holding the role lease lock; preserve unknown writers."""
    validate_handoff(config, role)
    root = config.coordination_root
    for directory in ("active-agents", "cloud-sessions"):
        if (root / directory / f"{role}.json").exists():
            raise CoordinationCheckError(f"unresolved {directory} writer activity for {role}")
    lease = _read(root / "handoff" / f"{role}.json")
    if not lease or lease.get("head") != head:
        raise CoordinationCheckError("coordination check revision differs from leased HEAD")
    return {
        "owner": "supervisor",
        "lease_id": lease["lease_id"],
        "head": head,
        "worktree": lease["worktree"],
        "branch": lease["branch"],
        "lock": str(role_lease_lock_path(config, role)),
        "no_registered_competing_writer": True,
    }


def _check_command(config: OrchestratorConfig, role: str) -> list[str]:
    script = _check_script(config)
    invocation = (
        "$ErrorActionPreference='Stop'; & '"
        + str(script).replace("'", "''")
        + "' -Role '"
        + role
        + "'"
    )
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", invocation]


def _snapshot(config: OrchestratorConfig, role: str, phase: str, head: str) -> dict:
    if not _SHA.fullmatch(head):
        raise CoordinationCheckError("coordination check requires exact commit SHA")
    script = _check_script(config)
    if not script.is_file():
        raise CoordinationCheckError("mandatory coordination check script is missing")
    command = _check_command(config, role)
    try:
        with ControlPlaneLock(role_lease_lock_path(config, role)):
            before = _writer_state(config, role, head)
            script_hash = hashlib.sha256(script.read_bytes()).hexdigest()
            completed = subprocess.run(
                command, cwd=config.coordination_root, capture_output=True, timeout=120, check=False
            )
            after = _writer_state(config, role, head)
            if before != after or hashlib.sha256(script.read_bytes()).hexdigest() != script_hash:
                raise CoordinationCheckError("coordination script or lease changed during check")
    except (HandoffRequired, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise CoordinationCheckError(f"{phase} coordination ownership/check failed: {exc}") from exc
    observed_at = datetime.now(UTC).isoformat()
    capture_id = uuid.uuid4().hex
    relative = Path("evidence/supervisor-checks")
    record = {
        "role": role,
        "phase": phase,
        "head": head,
        "observed_at": observed_at,
        "command": command,
        "exit_code": completed.returncode,
        "script_sha256": script_hash,
        "lease": after,
    }
    for stream in ("stdout", "stderr"):
        output = getattr(completed, stream).decode("utf-8", errors="replace")
        rel = relative / f"{role}-{phase}-{capture_id}.{stream}.txt"
        atomic_write_text(config.coordination_root / rel, output)
        record[f"{stream}_path"] = rel.as_posix()
        record[f"{stream}_sha256"] = hashlib.sha256(
            (config.coordination_root / rel).read_bytes()
        ).hexdigest()
    if completed.returncode != 0:
        raise CoordinationCheckError(
            f"{phase} coordination check failed (exit code {completed.returncode}); "
            f"stdout={record['stdout_path']}; stderr={record['stderr_path']}"
        )
    return record


def begin_checks(
    config: OrchestratorConfig,
    role: str,
    task: TaskItem,
    base_head: str,
    initial_head: str,
    *,
    recovered: bool = False,
) -> None:
    """Capture the opening of a new invocation or recovery-validation attempt."""
    path = _manifest_path(config, role, task)
    if not _checks_required(config, role):
        return
    if not _SHA.fullmatch(base_head):
        raise CoordinationCheckError("coordination check requires exact task base SHA")
    initial = _snapshot(config, role, "initial", initial_head)
    AtomicJsonStore(path).write(
        {
            "schema_version": 2,
            "role": role,
            "task_id": task.task_id,
            "base_head": base_head,
            "attempt_id": uuid.uuid4().hex,
            "scope": "recovered-checkpoint-validation" if recovered else "implementation-attempt",
            "initial": initial,
            "final": None,
        }
    )


def finish_checks(
    config: OrchestratorConfig,
    role: str,
    task: TaskItem,
    base_head: str,
    head: str,
) -> None:
    path = _manifest_path(config, role, task)
    if not _checks_required(config, role):
        return
    manifest = _read(path)
    if (
        not manifest
        or manifest.get("schema_version") != 2
        or manifest.get("role") != role
        or manifest.get("task_id") != task.task_id
        or manifest.get("base_head") != base_head
        or not isinstance(manifest.get("initial"), dict)
    ):
        raise CoordinationCheckError("matching initial coordination check is missing")
    try:
        load_validation_receipt(config, role, task.task_id, base_head=base_head, head=head)
        receipt_path = config.coordination_root / "validation" / f"{role}-{task.task_id}.json"
        receipt_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    except (OSError, ValueError) as exc:
        raise CoordinationCheckError("matching successful validation receipt is missing") from exc
    final = _snapshot(config, role, "final", head)
    if manifest["initial"].get("lease", {}).get("lease_id") != final["lease"]["lease_id"]:
        raise CoordinationCheckError("role lease changed between opening and closing checks")
    manifest["final"] = final
    manifest["validation_sha256"] = receipt_hash
    AtomicJsonStore(path).write(manifest)


def _bounded(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return (
        text[: limit // 2]
        + "\n[bounded output; full captured bytes at hashed path]\n"
        + text[-limit // 2 :]
    )


def read_check_summary(
    config: OrchestratorConfig,
    role: str,
    task: TaskItem,
    base_head: str,
    head: str,
    *,
    strict: bool = False,
) -> str:
    path = _manifest_path(config, role, task)
    manifest = _read(path)
    if manifest is None:
        if strict and _checks_required(config, role):
            raise CoordinationCheckError("revision-bound coordination checks are missing")
        return "No revision-bound initial/final coordination checks were captured."
    if (
        manifest.get("schema_version") != 2
        or manifest.get("role") != role
        or manifest.get("task_id") != task.task_id
        or manifest.get("base_head") != base_head
        or not isinstance(manifest.get("attempt_id"), str)
        or not manifest["attempt_id"]
    ):
        raise CoordinationCheckError("coordination check manifest identity mismatch")
    lines = [
        f"manifest: {path.relative_to(config.coordination_root).as_posix()}",
        f"scope: {manifest.get('scope')}",
    ]
    prior_time = None
    lease_id = None
    for phase in ("initial", "final"):
        record = manifest.get(phase)
        if not isinstance(record, dict):
            if strict:
                raise CoordinationCheckError(f"{phase} coordination check is missing")
            lines.append(f"{phase}: missing, not verified")
            continue
        observed_head = record.get("head", "")
        lease = record.get("lease", {})
        if (
            record.get("role") != role
            or record.get("phase") != phase
            or not isinstance(observed_head, str)
            or not _SHA.fullmatch(observed_head)
            or (phase == "final" and observed_head != head)
            or record.get("exit_code") != 0
            or not isinstance(lease, dict)
            or lease.get("owner") != "supervisor"
            or lease.get("head") != observed_head
            or not lease.get("lease_id")
            or lease.get("no_registered_competing_writer") is not True
        ):
            raise CoordinationCheckError(f"{phase} coordination evidence mismatch")
        try:
            stamp = datetime.fromisoformat(record["observed_at"])
            if (
                stamp.tzinfo is None
                or stamp.utcoffset() is None
                or (prior_time and stamp < prior_time)
            ):
                raise ValueError("invalid timestamp")
        except (KeyError, TypeError, ValueError) as exc:
            raise CoordinationCheckError("invalid coordination observation timestamp") from exc
        prior_time = stamp
        if lease_id and lease_id != lease["lease_id"]:
            raise CoordinationCheckError("coordination evidence changed lease identity")
        lease_id = lease["lease_id"]
        lines.extend(
            (
                f"{phase}: exit=0, UTC={record['observed_at']}, head={observed_head}",
                f"{phase} lease_id={lease_id}: exclusive OS role lock and clean matching handoff; no registered competing writer",
            )
        )
        for stream in ("stdout", "stderr"):
            relative = record.get(f"{stream}_path")
            digest = record.get(f"{stream}_sha256")
            if not isinstance(relative, str) or not isinstance(digest, str):
                raise CoordinationCheckError("malformed coordination artifact")
            artifact = (config.coordination_root / relative).resolve()
            try:
                artifact.relative_to(
                    (config.coordination_root / "evidence/supervisor-checks").resolve()
                )
                data = artifact.read_bytes()
            except (OSError, ValueError) as exc:
                raise CoordinationCheckError(
                    "coordination artifact is missing or escapes evidence root"
                ) from exc
            if hashlib.sha256(data).hexdigest() != digest:
                raise CoordinationCheckError("coordination artifact hash mismatch")
            lines.extend(
                (
                    f"{phase} {stream}: {relative} sha256={digest}",
                    _bounded(
                        data.decode("utf-8", errors="replace"), 2000 if stream == "stdout" else 400
                    ),
                )
            )
    if isinstance(manifest.get("final"), dict):
        try:
            load_validation_receipt(config, role, task.task_id, base_head=base_head, head=head)
            receipt = config.coordination_root / "validation" / f"{role}-{task.task_id}.json"
            if hashlib.sha256(receipt.read_bytes()).hexdigest() != manifest.get(
                "validation_sha256"
            ):
                raise CoordinationCheckError("validation receipt hash mismatch")
            with ControlPlaneLock(role_lease_lock_path(config, role)):
                current = _writer_state(config, role, head)
                if current["lease_id"] != lease_id:
                    raise CoordinationCheckError(
                        "current role lease differs from captured evidence"
                    )
        except (HandoffRequired, OSError, RuntimeError, ValueError) as exc:
            raise CoordinationCheckError(
                f"coordination evidence is no longer current: {exc}"
            ) from exc
        lines.append(f"validation receipt sha256={manifest['validation_sha256']}")
    lines.append(
        "Unregistered external writers are outside this observation. This is evidence, not independent approval."
    )
    return "\n".join(lines)


def retire_final_preparation(
    config: OrchestratorConfig,
    role: str,
    task: TaskItem,
    head: str,
) -> None:
    """Preserve old proof only for an explicit new claim, before its state save.

    Each raw UTF-8 artifact is copied durably to a content-addressed history path
    and verified before removal. Replaying an interrupted planner result retires
    only remaining old files. A saved FINAL-VERIFY task never calls this helper.
    """
    if task.task_id != f"FINAL-VERIFY-{role}":
        raise CoordinationCheckError("final retirement requires the reserved final task")
    manifest = _manifest_path(config, role, task)
    receipt = config.coordination_root / "validation" / f"{role}-{task.task_id}.json"
    history = config.coordination_root / "evidence/final-preparation-history" / role
    try:
        with ControlPlaneLock(role_lease_lock_path(config, role)):
            sources = [
                (kind, path)
                for kind, path in (("validation", receipt), ("checks", manifest))
                if path.exists() or path.is_symlink()
            ]
            if not sources:
                return
            _writer_state(config, role, head)
            for kind, path in sources:
                data = path.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                archived = history / f"{kind}-{digest}.json"
                if not archived.exists():
                    atomic_write_text(archived, data.decode("utf-8"))
                if archived.read_bytes() != data:
                    raise CoordinationCheckError("final preparation archive integrity mismatch")
                if path.read_bytes() != data:
                    raise CoordinationCheckError("final preparation changed during retirement")
                path.unlink()
    except CoordinationCheckError:
        raise
    except (HandoffRequired, OSError, ValueError) as exc:
        raise CoordinationCheckError(f"cannot preserve prior final preparation: {exc}") from exc


def final_preparation_checkpoint(
    config: OrchestratorConfig,
    role: str,
    task: TaskItem,
    head: str,
) -> Literal["new", "open", "validated", "complete"]:
    """Read final preparation progress without replacing any saved proof.

    A successful receipt belongs to this opening only if its exact final gates,
    revision, lease and time binding still match. An existing closing capture
    also binds the receipt bytes. Invalid present artifacts are never absence.
    The supervisor retains the same role lock across any missing step and save.
    """
    if task.task_id != f"FINAL-VERIFY-{role}":
        raise CoordinationCheckError("final preparation requires the reserved final task")
    return _preparation_checkpoint(config, role, task, head, head, final=True)


def publication_preparation_checkpoint(
    config: OrchestratorConfig,
    role: str,
    task: TaskItem,
    base_head: str,
    head: str,
) -> Literal["new", "open", "validated", "complete"]:
    """Resume real canonical preparation for a host-designated publication part."""
    from .publication_review import validate_review_task

    validate_review_task(config, role, task, base_head, head)
    return _preparation_checkpoint(config, role, task, base_head, head, final=False)


def _preparation_checkpoint(
    config: OrchestratorConfig,
    role: str,
    task: TaskItem,
    base_head: str,
    head: str,
    *,
    final: bool,
) -> Literal["new", "open", "validated", "complete"]:
    path = _manifest_path(config, role, task)
    receipt_path = config.coordination_root / "validation" / f"{role}-{task.task_id}.json"
    try:
        with ControlPlaneLock(role_lease_lock_path(config, role)):
            manifest_present = path.exists() or path.is_symlink()
            receipt_present = receipt_path.exists() or receipt_path.is_symlink()
            if not manifest_present:
                if receipt_present:
                    raise CoordinationCheckError(
                        "final receipt has no opening coordination evidence"
                    )
                return "new"
            manifest = _read(path)
            if (
                not manifest
                or not isinstance(manifest.get("initial"), dict)
                or "final" not in manifest
                or (manifest["final"] is not None and not isinstance(manifest["final"], dict))
                or manifest.get("scope") != "recovered-checkpoint-validation"
            ):
                raise CoordinationCheckError("malformed final preparation checkpoint")
            complete = isinstance(manifest["final"], dict)
            if complete and not receipt_present:
                raise CoordinationCheckError("completed final checkpoint has no validation receipt")
            # This verifies identity, capture timestamps, stream paths/hashes and,
            # when complete, the exact successful receipt digest.
            read_check_summary(config, role, task, base_head, head, strict=complete)
            current = _writer_state(config, role, head)
            script_hash = hashlib.sha256(_check_script(config).read_bytes()).hexdigest()
            command = _check_command(config, role)
            for phase in ("initial", "final") if complete else ("initial",):
                record = manifest[phase]
                if (
                    record["head"] != head
                    or record["lease"] != current
                    or record.get("script_sha256") != script_hash
                    or record.get("command") != command
                ):
                    raise CoordinationCheckError(
                        f"{phase} final checkpoint differs from current revision, lease or script"
                    )
            initial_at = datetime.fromisoformat(manifest["initial"]["observed_at"])
            now = datetime.now(UTC)
            if initial_at > now:
                raise CoordinationCheckError("final opening observation is in the future")
            if not receipt_present:
                return "open"
            receipt = (
                load_final_validation_receipt(config, role, head=head)
                if final
                else load_validation_receipt(
                    config, role, task.task_id, base_head=base_head, head=head
                )
            )
            passed_at = datetime.fromisoformat(receipt.passed_at)
            if passed_at < initial_at or passed_at > now:
                raise CoordinationCheckError(
                    "final receipt does not belong to this opening observation"
                )
            if complete:
                final_at = datetime.fromisoformat(manifest["final"]["observed_at"])
                if passed_at > final_at or final_at > now:
                    raise CoordinationCheckError("final receipt is outside its closing observation")
                return "complete"
            return "validated"
    except CoordinationCheckError:
        raise
    except (
        HandoffRequired,
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ) as exc:
        raise CoordinationCheckError(f"final preparation checkpoint is invalid: {exc}") from exc
