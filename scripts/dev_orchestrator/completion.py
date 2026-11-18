from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .coordination import active_owned_issues
from .handoff import HandoffRequired, validate_handoff
from .lifecycle import ControlPlaneLock
from .storage import AtomicJsonStore
from .types import OrchestratorConfig
from .validation import ValidationReceipt, load_validation_receipt, run_iteration_validation


@dataclass(frozen=True)
class LocalGate:
    name: str
    command: tuple[str, ...]
    cwd: str = "."


@dataclass(frozen=True)
class CompletionManifest:
    base_branch: str
    required_checks: tuple[str, ...]
    local_gates: tuple[LocalGate, ...]
    physical_label: str


@dataclass(frozen=True)
class CompletionGate:
    ready: bool
    reasons: tuple[str, ...] = ()
    needs_user: bool = False
    blocker_kind: str = ""
    head: str = ""
    evidence: dict | None = None
    system_error: str = ""


_BACKEND_CHECKS = (
    "Lint & Format Check",
    "Security Audit Gate",
    "Type Check (mypy)",
    "GUI ESLint",
    "GUI TypeScript Typecheck",
    "GUI Vitest Tests",
    "GUI Build",
    "Node Dependency Audit",
    "Python 3.11 Tests & Coverage",
    "Dependency Vulnerability Scan",
    "Pre-commit Hook Validation",
    "GUI Raw API Fetch Guard",
    "Wheel Contents Smoke Test",
    "Hardcoded Secret Scan",
    "commitlint",
    "Installed Windows Electron artifact",
)

_BACKEND_GATES = (
    LocalGate("backend pytest", ("py", "-3.11", "-m", "pytest", "-q")),
    LocalGate("backend ruff", ("py", "-3.11", "-m", "ruff", "check", ".")),
    LocalGate("backend mypy", ("py", "-3.11", "-m", "mypy", "rex", "--ignore-missing-imports")),
    LocalGate("backend security", ("py", "-3.11", "scripts/security_audit.py", "--release-gate")),
    LocalGate("gui lint", ("npm.cmd", "run", "lint"), "gui"),
    LocalGate("gui typecheck", ("npm.cmd", "run", "typecheck"), "gui"),
    LocalGate("gui tests", ("npm.cmd", "test", "--", "--run"), "gui"),
    LocalGate("gui build", ("npm.cmd", "run", "build"), "gui"),
    LocalGate("gui audit", ("npm.cmd", "audit", "--audit-level=high"), "gui"),
)

_MOBILE_CHECKS = ("Mobile Lint", "Mobile Tests", "Mobile Typecheck")
_MOBILE_GATES = (
    LocalGate("mobile tests", ("npm.cmd", "test")),
    LocalGate("mobile lint", ("npm.cmd", "run", "lint")),
    LocalGate("mobile typecheck", ("npx.cmd", "tsc", "--noEmit")),
)

COMPLETION_MANIFESTS = {
    "backend": CompletionManifest(
        "master", _BACKEND_CHECKS, _BACKEND_GATES, "Windows physical acceptance"
    ),
    "mobile": CompletionManifest(
        "main", _MOBILE_CHECKS, _MOBILE_GATES, "iPhone physical acceptance"
    ),
}


def _repo(config: OrchestratorConfig, role: str) -> Path:
    root = config.backend_root if role == "backend" else config.mobile_root
    if root is None:
        raise ValueError(f"missing repository root for {role}")
    return root


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=repo, capture_output=True, text=True, check=False)


def _lease_record(root: Path, role: str) -> dict:
    payload = AtomicJsonStore(root / "handoff" / f"{role}.json").read(default={})
    if (
        payload.get("owner") != "supervisor"
        or not payload.get("lease_id")
        or not payload.get("head")
    ):
        raise ValueError(f"{role} supervisor lease is required before acceptance")
    return payload


def _protocol_digest(root: Path) -> str:
    import hashlib

    path = root / "PROTOCOL.md"
    data = path.read_bytes() if path.is_file() else b""
    return hashlib.sha256(data).hexdigest()


def record_acceptance(root: Path, role: str, kind: str, evidence: str) -> Path:
    with ControlPlaneLock(root / "control-plane.lock"):
        if role not in COMPLETION_MANIFESTS:
            raise ValueError(f"unsupported completion role: {role}")
        if kind not in {"physical", "cross_repo", "documentation"}:
            raise ValueError(f"unsupported acceptance kind: {kind}")
        if not evidence.strip():
            raise ValueError("acceptance evidence must be non-empty")
        lease = _lease_record(root, role)
        record = {
            "confirmed": True,
            "evidence": evidence.strip(),
            "confirmed_at": datetime.now(UTC).isoformat(),
            "role_head": str(lease["head"]),
            "role_lease_id": str(lease["lease_id"]),
        }
        if kind == "cross_repo":
            peer = "mobile" if role == "backend" else "backend"
            peer_lease = _lease_record(root, peer)
            record.update(
                {
                    "peer_role": peer,
                    "peer_head": str(peer_lease["head"]),
                    "peer_lease_id": str(peer_lease["lease_id"]),
                    "protocol_sha256": _protocol_digest(root),
                }
            )
        path = root / "completion" / f"{role}-acceptance.json"
        store = AtomicJsonStore(path)
        payload = dict(store.read(default={}))
        payload[kind] = record
        store.write(payload)
        return path


def _acceptance_reasons(
    config: OrchestratorConfig, role: str, current_head: str, *, payload: dict | None = None
) -> tuple[str, ...]:
    manifest = COMPLETION_MANIFESTS[role]
    root = config.coordination_root
    if payload is None:
        payload = AtomicJsonStore(root / "completion" / f"{role}-acceptance.json").read(default={})
    reasons: list[str] = []
    try:
        lease = _lease_record(root, role)
    except ValueError:
        lease = {}
    documentation = payload.get("documentation") or {}
    documentation_valid = (
        documentation.get("confirmed")
        and str(documentation.get("evidence", "")).strip()
        and str(documentation.get("role_head", "")) == current_head
        and str(documentation.get("role_head", "")) == str(lease.get("head", ""))
        and str(documentation.get("role_lease_id", "")) == str(lease.get("lease_id", ""))
    )
    if not documentation_valid:
        reasons.append("documentation truth acceptance for the current leased HEAD is required")

    physical = payload.get("physical") or {}
    physical_valid = (
        physical.get("confirmed")
        and str(physical.get("evidence", "")).strip()
        and str(physical.get("role_head", "")) == current_head
        and str(physical.get("role_head", "")) == str(lease.get("head", ""))
        and str(physical.get("role_lease_id", "")) == str(lease.get("lease_id", ""))
    )
    if not physical_valid:
        reasons.append(
            f"{manifest.physical_label} evidence for the current leased HEAD is required"
        )

    cross_repo = payload.get("cross_repo") or {}
    peer = "mobile" if role == "backend" else "backend"
    try:
        peer_lease = _lease_record(root, peer)
    except ValueError:
        peer_lease = {}
    try:
        peer_repo = _repo(config, peer)
    except ValueError:
        peer_repo = None
    if peer_repo is None:
        peer_head = ""
    else:
        peer_head_result = _run(peer_repo, "git", "rev-parse", "HEAD")
        peer_head = peer_head_result.stdout.strip() if peer_head_result.returncode == 0 else ""
    cross_valid = (
        cross_repo.get("confirmed")
        and str(cross_repo.get("evidence", "")).strip()
        and str(cross_repo.get("role_head", "")) == current_head
        and str(cross_repo.get("role_lease_id", "")) == str(lease.get("lease_id", ""))
        and str(cross_repo.get("peer_role", "")) == peer
        and str(cross_repo.get("peer_head", "")) == peer_head
        and str(cross_repo.get("peer_head", "")) == str(peer_lease.get("head", ""))
        and str(cross_repo.get("peer_lease_id", "")) == str(peer_lease.get("lease_id", ""))
        and str(cross_repo.get("protocol_sha256", "")) == _protocol_digest(root)
    )
    if not cross_valid:
        reasons.append(
            "cross-repo contract acknowledgment for the current leased revisions is required"
        )
    return tuple(reasons)


def _ci_workflow_present(repo: Path) -> bool:
    root = repo / ".github" / "workflows"
    if not root.is_dir():
        return False
    return any(root.glob("*.yml")) or any(root.glob("*.yaml"))


def _run_local_gates(repo: Path, manifest: CompletionManifest) -> tuple[str, ...]:
    failures: list[str] = []
    for gate in manifest.local_gates:
        cwd = (repo / gate.cwd).resolve()
        try:
            result = subprocess.run(
                gate.command,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=3600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{gate.name} could not complete: {type(exc).__name__}")
            continue
        if result.returncode != 0:
            failures.append(f"{gate.name} failed with exit code {result.returncode}")
    return tuple(failures)


def _check_name(check: dict) -> str:
    return str(check.get("name") or check.get("context") or "")


def _check_conclusion(check: dict) -> str:
    return str(check.get("conclusion") or check.get("state") or "").upper()


def _completion_preflight(config: OrchestratorConfig, role: str) -> CompletionGate:
    if role not in COMPLETION_MANIFESTS:
        raise ValueError(f"unsupported completion role: {role}")
    repo = _repo(config, role)
    manifest = COMPLETION_MANIFESTS[role]
    reasons: list[str] = []
    issues = active_owned_issues(
        config.coordination_root, role, deferred_issue_ids=config.deferred_issue_ids
    )
    if issues:
        reasons.append(
            "owned live-test/retest issues remain: " + ", ".join(path.stem for path in issues)
        )

    git_dir = _run(repo, "git", "rev-parse", "--git-dir")
    if git_dir.returncode != 0:
        reasons.append("cannot inspect repository Git metadata")
        return CompletionGate(False, tuple(reasons))
    try:
        validate_handoff(config, role)
    except HandoffRequired as exc:
        reasons.append(f"supervisor lease is invalid: {exc}")
        return CompletionGate(False, tuple(reasons))
    lease = _lease_record(config.coordination_root, role)

    status = _run(repo, "git", "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0 or status.stdout.strip():
        reasons.append("repository is not clean")
    head_result = _run(repo, "git", "rev-parse", "HEAD")
    if head_result.returncode != 0:
        reasons.append("cannot resolve current Git HEAD")
        return CompletionGate(False, tuple(reasons))
    head = head_result.stdout.strip()
    acceptance_payload = AtomicJsonStore(
        config.coordination_root / "completion" / f"{role}-acceptance.json"
    ).read(default={})
    acceptance = _acceptance_reasons(config, role, head, payload=acceptance_payload)
    reasons.extend(acceptance)
    needs_user = bool(acceptance)
    blocker_kind = "acceptance" if acceptance else ""

    if not _ci_workflow_present(repo):
        reasons.append("repository has no CI workflow; CI must be established before completion")
    if shutil.which("gh") is None:
        reasons.append("GitHub CLI is unavailable for deterministic CI verification")
        return CompletionGate(False, tuple(reasons), needs_user, blocker_kind)

    pr = _run(
        repo,
        "gh",
        "pr",
        "view",
        "--json",
        "state,headRefOid,headRefName,baseRefName,statusCheckRollup",
    )
    if pr.returncode != 0:
        detail = "\n".join(part for part in (pr.stderr, pr.stdout) if part).lower()
        auth_markers = (
            "requires authentication",
            "gh auth login",
            "not authenticated",
            "http 401",
        )
        if any(marker in detail for marker in auth_markers):
            reasons.append(
                "GitHub CLI authentication is required for deterministic CI verification"
            )
            return CompletionGate(False, tuple(reasons), True, "github_auth")
        reasons.append("no GitHub pull request is available for the current branch")
        return CompletionGate(False, tuple(reasons), needs_user, blocker_kind)
    try:
        payload = json.loads(pr.stdout)
    except json.JSONDecodeError:
        reasons.append("GitHub pull-request status was not valid JSON")
        return CompletionGate(False, tuple(reasons), needs_user, blocker_kind)

    if not isinstance(payload, dict):
        return CompletionGate(False, ("GitHub pull-request status was not an object",))
    reasons.extend(_pull_request_reasons(payload, manifest, head, lease))
    evidence = {
        "schema_version": 1,
        "role": role,
        "head": head,
        "lease_id": lease["lease_id"],
        "required_checks": list(manifest.required_checks),
        "pull_request": payload,
        "acceptance": acceptance_payload,
    }
    return CompletionGate(
        not reasons, tuple(reasons), needs_user, blocker_kind, head=head, evidence=evidence
    )


def _pull_request_reasons(
    payload: dict, manifest: CompletionManifest, head: str, lease: dict
) -> tuple[str, ...]:
    reasons: list[str] = []
    if str(payload.get("state", "")).upper() != "OPEN":
        reasons.append("pull request must be open")
    if str(payload.get("baseRefName", "")) != manifest.base_branch:
        reasons.append(f"pull request base must be {manifest.base_branch}")
    if str(payload.get("headRefOid", "")) != head:
        reasons.append("pull request is not at the current Git HEAD")
    if str(payload.get("headRefName", "")) != str(lease.get("branch", "")):
        reasons.append("pull request head branch does not match the active supervisor lease")

    checks = payload.get("statusCheckRollup") or []
    by_name: dict[str, list[str]] = {}
    for check in checks:
        by_name.setdefault(_check_name(check), []).append(_check_conclusion(check))
    missing_or_bad = [
        name
        for name in manifest.required_checks
        if not by_name.get(name) or any(value != "SUCCESS" for value in by_name[name])
    ]
    if missing_or_bad:
        reasons.append("required CI checks are not successful: " + ", ".join(missing_or_bad))

    hard_failures = [
        name
        for name, conclusions in by_name.items()
        if any(
            value in {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "ERROR"}
            for value in conclusions
        )
    ]
    if hard_failures:
        reasons.append("CI contains failing checks: " + ", ".join(hard_failures))

    return tuple(reasons)


def evaluate_completion(config: OrchestratorConfig, role: str) -> CompletionGate:
    """Check all completion requirements, including fresh local release gates."""
    gate = _completion_preflight(config, role)
    if not gate.ready:
        return gate
    failures = _run_local_gates(_repo(config, role), COMPLETION_MANIFESTS[role])
    return replace(gate, ready=not failures, reasons=failures)


def prepare_final_review(config: OrchestratorConfig, role: str, *, head: str) -> CompletionGate:
    """Execute the canonical final gates and retain their actual review evidence."""
    gate = _completion_preflight(config, role)
    if not gate.ready:
        return gate
    if gate.head != head:
        return CompletionGate(False, system_error="final validation revision changed")
    task_id = f"FINAL-VERIFY-{role}"
    local_gates = COMPLETION_MANIFESTS[role].local_gates
    # Final release gates are authoritative even when ordinary iteration gates
    # are disabled or scoped. This configuration is local to this execution.
    validation_config = replace(
        config,
        metadata={
            **config.metadata,
            "iteration_validation": {
                "enabled": True,
                role: {
                    "overrides": [
                        {
                            "task_prefix": task_id,
                            "gates": [
                                {"name": item.name, "command": list(item.command), "cwd": item.cwd}
                                for item in local_gates
                            ],
                        }
                    ],
                },
            },
        },
    )
    report = run_iteration_validation(
        validation_config,
        role,
        task_id,
        base_head=head,
        head=head,
        completion_evidence=gate.evidence,
    )
    if not report.passed or report.receipt is None:
        return CompletionGate(
            False,
            (report.feedback,) if report.feedback else (),
            system_error=report.system_error
            or ("final validation produced no receipt" if report.passed else ""),
        )
    return gate


def load_final_validation_receipt(
    config: OrchestratorConfig, role: str, *, head: str
) -> ValidationReceipt:
    """Require the final task's real gates and complete completion observations."""
    receipt = load_validation_receipt(
        config, role, f"FINAL-VERIFY-{role}", base_head=head, head=head
    )
    manifest = COMPLETION_MANIFESTS[role]
    actual = [(item.name, item.command, item.cwd) for item in receipt.gates]
    expected = [(item.name, item.command, item.cwd) for item in manifest.local_gates]
    if not expected or actual != expected:
        raise ValueError("final validation receipt does not match the completion manifest")
    evidence = receipt.completion_evidence
    lease = _lease_record(config.coordination_root, role)
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema_version") != 1
        or evidence.get("role") != role
        or evidence.get("head") != head
        or evidence.get("lease_id") != lease.get("lease_id")
        or evidence.get("required_checks") != list(manifest.required_checks)
        or not isinstance(evidence.get("pull_request"), dict)
        or not isinstance(evidence.get("acceptance"), dict)
    ):
        raise ValueError("final validation is missing bound completion evidence")
    reasons = _pull_request_reasons(evidence["pull_request"], manifest, head, lease)
    reasons += _acceptance_reasons(config, role, head, payload=evidence["acceptance"])
    if reasons:
        raise ValueError("final completion evidence is invalid: " + "; ".join(reasons))
    return receipt


def recheck_final_completion(config: OrchestratorConfig, role: str, *, head: str) -> CompletionGate:
    """Refresh mutable CI/acceptance gates while retaining same-HEAD local proof."""
    gate = _completion_preflight(config, role)
    if not gate.ready:
        return gate
    if gate.head != head:
        return CompletionGate(
            False, ("final review revision changed",), blocker_kind="incomplete_evidence"
        )
    try:
        load_final_validation_receipt(config, role, head=head)
    except (OSError, ValueError) as exc:
        return CompletionGate(False, (str(exc),), blocker_kind="incomplete_evidence")
    return gate
