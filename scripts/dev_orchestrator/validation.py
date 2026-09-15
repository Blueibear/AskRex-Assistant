from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .storage import AtomicJsonStore
from .types import OrchestratorConfig

_MAX_OUTPUT_CHARS = 4_000


@dataclass(frozen=True)
class ValidationGate:
    name: str
    command: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: int = 3600


@dataclass(frozen=True)
class ValidationGateEvidence:
    name: str
    command: tuple[str, ...]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command),
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass(frozen=True)
class ValidationReceipt:
    role: str
    task_id: str
    base_head: str
    head: str
    gates: tuple[ValidationGateEvidence, ...]
    passed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "task_id": self.task_id,
            "base_head": self.base_head,
            "head": self.head,
            "gates": [gate.to_dict() for gate in self.gates],
            "passed_at": self.passed_at,
        }


@dataclass(frozen=True)
class ValidationReport:
    passed: bool
    feedback: str = ""
    system_error: str = ""
    receipt: ValidationReceipt | None = None


def _receipt_path(config: OrchestratorConfig, role: str, task_id: str) -> Path:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", task_id) is None:
        raise ValueError("validation receipt requires a safe non-empty task id")
    return config.coordination_root / "validation" / f"{role}-{task_id}.json"


def _clear_receipt(config: OrchestratorConfig, role: str, task_id: str) -> None:
    _receipt_path(config, role, task_id).unlink(missing_ok=True)


def _git_head(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True, stderr=subprocess.DEVNULL
    ).strip()


def load_validation_receipt(
    config: OrchestratorConfig,
    role: str,
    task_id: str,
    *,
    base_head: str,
    head: str,
) -> ValidationReceipt:
    payload = AtomicJsonStore(_receipt_path(config, role, task_id)).read(default=None)
    if not isinstance(payload, dict):
        raise FileNotFoundError("matching validation receipt is missing")
    if (
        payload.get("role") != role
        or payload.get("task_id") != task_id
        or payload.get("base_head") != base_head
        or payload.get("head") != head
    ):
        raise ValueError("validation receipt revision binding mismatch")
    raw_gates = payload.get("gates")
    if not isinstance(raw_gates, list):
        raise ValueError("validation receipt gates are malformed")
    gates: list[ValidationGateEvidence] = []
    for raw in raw_gates:
        if not isinstance(raw, dict):
            raise ValueError("validation receipt gate is malformed")
        command = raw.get("command")
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise ValueError("validation receipt command is malformed")
        try:
            exit_code = int(raw.get("exit_code", -1))
        except (TypeError, ValueError) as exc:
            raise ValueError("validation receipt exit code is malformed") from exc
        if exit_code != 0:
            raise ValueError("validation receipt must contain only successful gates")
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError("validation receipt gate name is malformed")
        gates.append(
            ValidationGateEvidence(
                name=name,
                command=tuple(command),
                cwd=str(raw.get("cwd", "")),
                exit_code=exit_code,
                stdout=str(raw.get("stdout", "")),
                stderr=str(raw.get("stderr", "")),
            )
        )
    passed_at = str(payload.get("passed_at", ""))
    try:
        passed_stamp = datetime.fromisoformat(passed_at)
    except ValueError as exc:
        raise ValueError("validation receipt timestamp is malformed") from exc
    if passed_stamp.tzinfo is None or passed_stamp.utcoffset() is None:
        raise ValueError("validation receipt timestamp must be timezone-aware")
    return ValidationReceipt(
        role=role,
        task_id=task_id,
        base_head=base_head,
        head=head,
        gates=tuple(gates),
        passed_at=passed_at,
    )


_DEFAULT_GATES: dict[str, tuple[ValidationGate, ...]] = {
    "backend": (
        ValidationGate("backend pytest", ("py", "-3.11", "-m", "pytest", "-q")),
        ValidationGate("backend ruff", ("py", "-3.11", "-m", "ruff", "check", ".")),
        ValidationGate("backend diff check", ("git", "diff", "--check", "origin/master...HEAD")),
    ),
    "mobile": (
        ValidationGate("mobile tests", ("npm.cmd", "test")),
        ValidationGate("mobile lint", ("npm.cmd", "run", "lint")),
        ValidationGate("mobile typecheck", ("npx.cmd", "tsc", "--noEmit")),
        ValidationGate("mobile diff check", ("git", "diff", "--check", "origin/main...HEAD")),
    ),
}


def _bounded(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return "[output truncated; showing final 12000 characters]\n" + text[-_MAX_OUTPUT_CHARS:]


def _parse_gate(raw: Any) -> ValidationGate:
    if not isinstance(raw, dict):
        raise ValueError("validation gate must be an object")
    name = str(raw.get("name", "")).strip()
    command = raw.get("command")
    if not name:
        raise ValueError("validation gate name must be non-empty")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(x, str) and x for x in command)
    ):
        raise ValueError(f"validation gate {name!r} requires a non-empty string command list")
    cwd = str(raw.get("cwd", ".")).strip() or "."
    timeout = int(raw.get("timeout_seconds", 3600))
    if timeout <= 0:
        raise ValueError(f"validation gate {name!r} timeout must be positive")
    return ValidationGate(name, tuple(command), cwd, timeout)


def _gates_for(config: OrchestratorConfig, role: str, task_id: str) -> tuple[ValidationGate, ...]:
    settings = config.metadata.get("iteration_validation", {})
    if isinstance(settings, dict) and settings.get("enabled") is False:
        return ()
    role_settings = settings.get(role, {}) if isinstance(settings, dict) else {}
    if not isinstance(role_settings, dict):
        raise ValueError(f"iteration validation config for {role} must be an object")
    overrides = role_settings.get("overrides", [])
    if not isinstance(overrides, list):
        raise ValueError(f"iteration validation overrides for {role} must be a list")
    for override in overrides:
        if not isinstance(override, dict):
            raise ValueError(f"iteration validation override for {role} must be an object")
        prefix = str(override.get("task_prefix", ""))
        if prefix and task_id.startswith(prefix):
            gates = override.get("gates", [])
            if not isinstance(gates, list) or not gates:
                raise ValueError(f"validation override {prefix!r} for {role} has no gates")
            return tuple(_parse_gate(item) for item in gates)
    configured = role_settings.get("gates")
    if configured is not None:
        if not isinstance(configured, list) or not configured:
            raise ValueError(f"iteration validation gates for {role} must be a non-empty list")
        return tuple(_parse_gate(item) for item in configured)
    try:
        return _DEFAULT_GATES[role]
    except KeyError as exc:
        raise ValueError(f"unsupported validation role: {role}") from exc


def _repo_root(config: OrchestratorConfig, role: str) -> Path:
    root = config.backend_root if role == "backend" else config.mobile_root
    if root is None:
        raise ValueError(f"missing repository root for {role}")
    return root.resolve()


def _render_failure(
    gate: ValidationGate, cwd: Path, result: subprocess.CompletedProcess[str]
) -> str:
    command = subprocess.list2cmdline(list(gate.command))
    stdout = _bounded(result.stdout or "")
    stderr = _bounded(result.stderr or "")
    return (
        "Deterministic validation failed before independent review.\n"
        f"Gate: {gate.name}\n"
        f"Command: {command}\n"
        f"CWD: {cwd}\n"
        f"Exit code: {result.returncode}\n"
        f"STDOUT:\n{stdout}\n"
        f"STDERR:\n{stderr}"
    )


def run_iteration_validation(
    config: OrchestratorConfig,
    role: str,
    task_id: str,
    *,
    base_head: str = "",
    head: str = "",
) -> ValidationReport:
    _receipt_path(config, role, task_id)
    try:
        _clear_receipt(config, role, task_id)
        gates = _gates_for(config, role, task_id)
        if not gates:
            return ValidationReport(True)
        repo = _repo_root(config, role)
        if head and _git_head(repo) != head:
            raise ValueError("validation target revision changed before gates ran")
        evidence: list[ValidationGateEvidence] = []
        for gate in gates:
            cwd = (repo / gate.cwd).resolve()
            if cwd != repo and repo not in cwd.parents:
                raise ValueError(f"validation gate {gate.name!r} cwd escapes repository")
            result = subprocess.run(
                gate.command,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=gate.timeout_seconds,
                check=False,
            )
            if result.returncode != 0:
                return ValidationReport(False, _render_failure(gate, cwd, result))
            evidence.append(
                ValidationGateEvidence(
                    name=gate.name,
                    command=gate.command,
                    cwd=gate.cwd,
                    exit_code=result.returncode,
                    stdout=_bounded(result.stdout or ""),
                    stderr=_bounded(result.stderr or ""),
                )
            )
        if head and _git_head(repo) != head:
            raise ValueError("validation target revision changed while gates ran")
        receipt = ValidationReceipt(
            role=role,
            task_id=task_id,
            base_head=base_head,
            head=head,
            gates=tuple(evidence),
            passed_at=datetime.now(UTC).isoformat(),
        )
        AtomicJsonStore(_receipt_path(config, role, task_id)).write(receipt.to_dict())
        return ValidationReport(True, receipt=receipt)
    except subprocess.TimeoutExpired as exc:
        output = _bounded((exc.stdout or "") + (exc.stderr or ""))
        return ValidationReport(
            False, f"Deterministic validation timed out before review.\n{output}"
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        gate_name = locals().get("gate")
        detail = f" for {gate_name.name}" if isinstance(gate_name, ValidationGate) else ""
        return ValidationReport(
            False, system_error=f"deterministic validation infrastructure failure{detail}: {exc}"
        )
