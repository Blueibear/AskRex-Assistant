"""Durable diagnostics for failed validation, separate from success receipts."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .storage import AtomicJsonStore

if TYPE_CHECKING:
    from .validation import ValidationGate

_MAX_FEEDBACK_CHARS = 5_000
_OUTPUT_OMITTED = "\n[output truncated; complete output retained in failure artifacts]\n"


def output_text(stream: bytes | str | None) -> str:
    return stream.decode("utf-8", errors="replace") if isinstance(stream, bytes) else stream or ""


def _output_bytes(stream: bytes | str | None) -> bytes:
    return stream if isinstance(stream, bytes) else (stream or "").encode("utf-8")


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = "\n[... excerpt truncated ...]\n"
    remaining = max(0, limit - len(marker))
    leading = remaining * 2 // 3
    trailing = remaining - leading
    return (text[:leading] + marker + (text[-trailing:] if trailing else ""))[:limit]


def _failure_excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    # Pytest puts warnings after tracebacks. Its final summary may abbreviate the
    # exception, so retain the actual failure section rather than only the tail.
    failure = re.search(r"(?m)^=+[ \t]+(?:FAILURES|ERRORS)[ \t]+=+[ \t]*\r?$", text)
    if failure:
        following = re.search(r"(?m)^=+[^\r\n]*=+[ \t]*\r?$", text[failure.end() :])
        end = failure.end() + following.start() if following else len(text)
        text = text[failure.start() : end]
    else:
        traceback = text.find("Traceback (most recent call last):")
        if traceback >= 0:
            text = text[traceback:]
    return (_OUTPUT_OMITTED + _clip(text, max(0, limit - len(_OUTPUT_OMITTED))))[:limit]


def _write_stream(root: Path, path: Path, data: bytes) -> dict[str, Any]:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def record_validation_failure(
    root: Path,
    *,
    role: str,
    task_id: str,
    base_head: str,
    head: str,
    gate: ValidationGate,
    cwd: Path,
    exit_code: int | None,
    stdout: bytes | str | None,
    stderr: bytes | str | None,
    timed_out: bool = False,
) -> str:
    """Persist complete captured bytes before returning bounded failure feedback."""
    attempt_id = uuid.uuid4().hex
    directory = root / "validation-failures" / attempt_id
    manifest_path = directory / "manifest.json"
    try:
        directory.mkdir(parents=True, exist_ok=False)
        out = _write_stream(root, directory / "stdout.log", _output_bytes(stdout))
        err = _write_stream(root, directory / "stderr.log", _output_bytes(stderr))
        AtomicJsonStore(manifest_path).write(
            {
                "schema_version": 1,
                "kind": "validation_gate_failure",
                "attempt_id": attempt_id,
                "role": role,
                "task_id": task_id,
                "base_head": base_head,
                "head": head,
                "gate": gate.name,
                "command": list(gate.command),
                "cwd": str(cwd),
                "exit_code": exit_code,
                "timed_out": timed_out,
                "timeout_seconds": gate.timeout_seconds,
                "recorded_at": datetime.now(UTC).isoformat(),
                "capture_format": "raw_subprocess_bytes",
                "stdout": out,
                "stderr": err,
            }
        )
        manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    except OSError as exc:
        outcome = "timeout" if timed_out else f"exit code {exit_code}"
        raise OSError(f"cannot persist validation failure evidence ({outcome}): {exc}") from exc

    summary = (
        "Deterministic validation timed out before review."
        if timed_out
        else "Deterministic validation failed before independent review."
    )
    exit_detail = (
        f"unavailable (timed out after {gate.timeout_seconds} seconds)"
        if timed_out
        else str(exit_code)
    )
    lines = [
        summary,
        f"Gate: {_clip(gate.name, 160)}",
        f"Command: {_clip(subprocess.list2cmdline(list(gate.command)), 600)}",
        f"CWD: {_clip(str(cwd), 300)}",
        f"Exit code: {exit_detail}",
        "Failure artifacts (paths relative to coordination root):",
        f"Manifest: {manifest_path.relative_to(root).as_posix()}",
        f"Manifest SHA256: {manifest_hash}",
        f"STDOUT artifact: {out['path']} ({out['bytes']} bytes; SHA256 {out['sha256']})",
        f"STDERR artifact: {err['path']} ({err['bytes']} bytes; SHA256 {err['sha256']})",
    ]
    header = "\n".join(lines) + "\n"
    stdout_text, stderr_text = output_text(stdout), output_text(stderr)
    out_label, err_label = "STDOUT excerpt:\n", "\nSTDERR excerpt:\n"
    budget = _MAX_FEEDBACK_CHARS - len(header) - len(out_label) - len(err_label)
    out_budget = min(len(stdout_text), budget // 2)
    err_budget = min(len(stderr_text), budget - out_budget)
    out_budget = min(len(stdout_text), budget - err_budget)
    return (
        header
        + out_label
        + _failure_excerpt(stdout_text, out_budget)
        + err_label
        + _failure_excerpt(stderr_text, err_budget)
    )
