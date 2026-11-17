"""Revision-bound snapshots of the trusted Windows coordination check.

These receipts document an observed script result. They never certify writer
exclusivity, independently approve code, or replace the required security gates.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .storage import AtomicJsonStore, atomic_write_text
from .types import OrchestratorConfig, TaskItem

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z", re.IGNORECASE)


class CoordinationCheckError(ValueError):
    pass


def _manifest_path(config: OrchestratorConfig, role: str, task: TaskItem) -> Path:
    if role not in {"backend", "mobile"} or not _SAFE_ID.fullmatch(task.task_id):
        raise CoordinationCheckError("invalid role or task identity for coordination evidence")
    return (
        config.coordination_root / "evidence" / "supervisor-checks" / f"{role}-{task.task_id}.json"
    )


def _check_script(config: OrchestratorConfig) -> Path:
    return config.coordination_root / "scripts" / "check-coordination.ps1"


def _snapshot(config: OrchestratorConfig, role: str, phase: str, head: str) -> dict:
    if not _SHA.fullmatch(head):
        raise CoordinationCheckError("coordination check requires exact commit SHA")
    script = _check_script(config)
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(script),
        "-Role",
        role,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=config.coordination_root,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CoordinationCheckError(f"{phase} coordination check could not complete") from exc

    observed_at = datetime.now(UTC).isoformat()
    capture_id = uuid.uuid4().hex
    root = config.coordination_root
    rel = Path("evidence") / "supervisor-checks"
    stdout_rel = rel / f"{role}-{phase}-{capture_id}.stdout.txt"
    stderr_rel = rel / f"{role}-{phase}-{capture_id}.stderr.txt"
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    atomic_write_text(root / stdout_rel, stdout)
    atomic_write_text(root / stderr_rel, stderr)
    record = {
        "role": role,
        "phase": phase,
        "head": head,
        "observed_at": observed_at,
        "command": command,
        "exit_code": completed.returncode,
        "stdout_path": stdout_rel.as_posix(),
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_path": stderr_rel.as_posix(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
    }
    if completed.returncode != 0:
        raise CoordinationCheckError(
            f"{phase} coordination check failed (exit code {completed.returncode})"
        )
    return record


def begin_checks(
    config: OrchestratorConfig,
    role: str,
    task: TaskItem,
    base_head: str,
    initial_head: str,
) -> None:
    """Begin a new attempt. Prior attempt receipts cannot satisfy this attempt."""
    if not _check_script(config).is_file():
        return
    path = _manifest_path(config, role, task)
    initial = _snapshot(config, role, "initial", initial_head)
    AtomicJsonStore(path).write(
        {
            "schema_version": 1,
            "role": role,
            "task_id": task.task_id,
            "base_head": base_head,
            "attempt_id": uuid.uuid4().hex,
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
    if not _check_script(config).is_file():
        return
    path = _manifest_path(config, role, task)
    manifest = AtomicJsonStore(path).read(default=None)
    if (
        not isinstance(manifest, dict)
        or manifest.get("role") != role
        or manifest.get("task_id") != task.task_id
        or manifest.get("base_head") != base_head
        or not isinstance(manifest.get("initial"), dict)
        or not _SHA.fullmatch(str(manifest["initial"].get("head", "")))
    ):
        raise CoordinationCheckError("matching initial coordination check is missing")
    final = _snapshot(config, role, "final", head)
    manifest["final"] = final
    AtomicJsonStore(path).write(manifest)


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
    manifest = AtomicJsonStore(path).read(default=None)
    if not isinstance(manifest, dict):
        if strict and _check_script(config).is_file():
            raise CoordinationCheckError("revision-bound coordination checks are missing")
        return "No revision-bound initial/final coordination checks were captured."
    if (
        manifest.get("schema_version") != 1
        or manifest.get("role") != role
        or manifest.get("task_id") != task.task_id
        or manifest.get("base_head") != base_head
        or not isinstance(manifest.get("attempt_id"), str)
    ):
        raise CoordinationCheckError("coordination check manifest identity mismatch")

    lines = [f"manifest: {path.relative_to(config.coordination_root).as_posix()}"]
    for phase in ("initial", "final"):
        record = manifest.get(phase)
        expected_head = (
            head if phase == "final" else record.get("head") if isinstance(record, dict) else ""
        )
        if not isinstance(record, dict):
            if strict:
                raise CoordinationCheckError(f"{phase} coordination check is missing")
            lines.append(f"{phase}: missing, not verified")
            continue
        if (
            record.get("role") != role
            or record.get("phase") != phase
            or record.get("head") != expected_head
            or record.get("exit_code") != 0
        ):
            raise CoordinationCheckError(f"{phase} coordination evidence mismatch")
        for suffix in ("stdout", "stderr"):
            rel = record.get(f"{suffix}_path")
            expected_hash = record.get(f"{suffix}_sha256")
            if not isinstance(rel, str) or not isinstance(expected_hash, str):
                raise CoordinationCheckError("malformed coordination artifact")
            artifact = (config.coordination_root / rel).resolve()
            try:
                artifact.relative_to(config.coordination_root.resolve())
            except ValueError as exc:
                raise CoordinationCheckError("coordination artifact escapes shared root") from exc
            if not artifact.is_file():
                raise CoordinationCheckError("coordination artifact is missing")
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != expected_hash:
                raise CoordinationCheckError("coordination artifact hash mismatch")
        lines.extend(
            (
                f"{phase}: exit=0, UTC={record['observed_at']}, head={expected_head}",
                f"{phase} stdout: {record['stdout_path']} sha256={record['stdout_sha256']}",
                f"{phase} stderr: {record['stderr_path']} sha256={record['stderr_sha256']}",
            )
        )
    lines.append("These checks do not establish the absence of a competing writer or approval.")
    return "\n".join(lines)
