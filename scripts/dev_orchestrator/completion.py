from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .coordination import active_owned_issues
from .handoff import HandoffRequired, validate_handoff
from .lifecycle import ControlPlaneLock
from .storage import AtomicJsonStore
from .types import OrchestratorConfig


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
    config: OrchestratorConfig, role: str, current_head: str
) -> tuple[str, ...]:
    manifest = COMPLETION_MANIFESTS[role]
    root = config.coordination_root
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


def evaluate_completion(config: OrchestratorConfig, role: str) -> CompletionGate:
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
    acceptance = _acceptance_reasons(config, role, head)
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

    if not reasons:
        reasons.extend(_run_local_gates(repo, manifest))
    return CompletionGate(not reasons, tuple(reasons), needs_user, blocker_kind)
