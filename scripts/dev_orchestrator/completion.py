from __future__ import annotations

import hashlib
import json
import shutil
import stat
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
    local_remediation: tuple[str, ...] = ()
    publication_reasons: tuple[str, ...] = ()
    acceptance_reasons: tuple[str, ...] = ()
    observation_fingerprint: str = ""
    waiting_for_ci: bool = False
    remediation_fingerprint: str = ""
    observation_retryable: bool = False


_HARD_CI_FAILURES = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "ERROR"}


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


def completion_observation_fingerprint(config: OrchestratorConfig, role: str, head: str) -> str:
    """Hash material local completion inputs, never invocation/timestamp churn."""
    root = config.coordination_root
    repo = _repo(config, role).resolve()
    peer = "mobile" if role == "backend" else "backend"

    def lease_identity(name: str) -> dict:
        value = AtomicJsonStore(root / "handoff" / f"{name}.json").read(default={})
        return {
            key: value.get(key)
            for key in ("role", "owner", "worktree", "branch", "head", "lease_id")
        }

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "absent"

    workflows = repo / ".github" / "workflows"
    files: list[tuple[str, str]] = []
    for directory in (repo / ".github", workflows):
        if directory.exists():
            metadata = directory.lstat()
            if directory.is_symlink() or (
                getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError("completion workflow directory must not be a reparse point")
    for path in sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")]):
        metadata = path.lstat()
        if (
            not path.is_file()
            or path.is_symlink()
            or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or path.resolve().parent != workflows.resolve()
            or metadata.st_size > 2_000_000
        ):
            raise ValueError("completion workflow source is outside its trusted bound")
        files.append((path.name, digest(path)))
    policy = config.metadata.get("host_publication", {})
    manifest = COMPLETION_MANIFESTS[role]
    payload = {
        "schema_version": 1,
        "role": role,
        "head": head,
        "lease": lease_identity(role),
        "peer_lease": lease_identity(peer),
        "workflows": files,
        "completion_contract": {
            "base_branch": manifest.base_branch,
            "required_checks": list(manifest.required_checks),
            "local_gates": [
                {"name": gate.name, "command": list(gate.command), "cwd": gate.cwd}
                for gate in manifest.local_gates
            ],
            "physical_label": manifest.physical_label,
        },
        "acceptance": digest(root / "completion" / f"{role}-acceptance.json"),
        "protocol": _protocol_digest(root),
        "host_publication_enabled": isinstance(policy, dict) and policy.get("enabled") is True,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _remediation_fingerprint(repo: Path, kind: str, facts: object) -> str:
    # Bind recurring failures to source content and stable observed gate facts,
    # never commit time, invocation IDs, raw duration tails or new artifact paths.
    tree = _run(repo, "git", "rev-parse", "HEAD^{tree}")
    tree_id = tree.stdout.strip()
    if (
        tree.returncode != 0
        or len(tree_id) != 40
        or any(character not in "0123456789abcdef" for character in tree_id)
    ):
        raise ValueError("completion remediation cannot resolve its source tree")
    payload = {"kind": kind, "tree": tree_id, "facts": facts}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _github_auth_required(detail: str) -> bool:
    return any(
        marker in detail.lower()
        for marker in ("requires authentication", "gh auth login", "not authenticated", "http 401")
    )


def completion_preflight(config: OrchestratorConfig, role: str) -> CompletionGate:
    """Observe completion requirements without running release tests or a provider."""
    return _completion_preflight(config, role)


def _completion_preflight(config: OrchestratorConfig, role: str) -> CompletionGate:
    if role not in COMPLETION_MANIFESTS:
        raise ValueError(f"unsupported completion role: {role}")
    repo = _repo(config, role)
    manifest = COMPLETION_MANIFESTS[role]
    reasons: list[str] = []
    local: list[str] = []
    publication: list[str] = []
    acceptance: tuple[str, ...] = ()
    head = ""
    fingerprint = ""
    remediation_fingerprint = ""

    def result(**changes) -> CompletionGate:
        values = {
            "ready": False,
            "reasons": tuple(reasons),
            "needs_user": bool(acceptance),
            "blocker_kind": "acceptance" if acceptance else "",
            "head": head,
            "local_remediation": tuple(local),
            "publication_reasons": tuple(publication),
            "acceptance_reasons": acceptance,
            "observation_fingerprint": fingerprint,
            "remediation_fingerprint": remediation_fingerprint,
        }
        values.update(changes)
        return CompletionGate(**values)

    def host_error(message: str, *, retryable: bool = False) -> CompletionGate:
        reasons.append(message)
        return result(system_error=message, observation_retryable=retryable)

    issues = active_owned_issues(
        config.coordination_root, role, deferred_issue_ids=config.deferred_issue_ids
    )
    if issues:
        reasons.append(
            "owned live-test/retest issues remain: " + ", ".join(path.stem for path in issues)
        )
    git_dir = _run(repo, "git", "rev-parse", "--git-dir")
    if git_dir.returncode != 0:
        return host_error("cannot inspect repository Git metadata")
    try:
        validate_handoff(config, role)
    except HandoffRequired as exc:
        return host_error(f"supervisor lease is invalid: {exc}")
    lease = _lease_record(config.coordination_root, role)
    status = _run(repo, "git", "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0 or status.stdout.strip():
        return host_error("repository is not clean")
    head_result = _run(repo, "git", "rev-parse", "HEAD")
    if head_result.returncode != 0:
        return host_error("cannot resolve current Git HEAD")
    head = head_result.stdout.strip()
    fingerprint = completion_observation_fingerprint(config, role, head)
    acceptance_payload = AtomicJsonStore(
        config.coordination_root / "completion" / f"{role}-acceptance.json"
    ).read(default={})
    acceptance = _acceptance_reasons(config, role, head, payload=acceptance_payload)
    reasons.extend(acceptance)

    if not _ci_workflow_present(repo):
        reasons.append("repository has no CI workflow; CI must be established before completion")
        local.append("missing_ci_workflow")
    if shutil.which("gh") is None:
        return host_error("GitHub CLI is unavailable for deterministic CI verification")

    pr = _run(
        repo,
        "gh",
        "pr",
        "view",
        "--json",
        "state,headRefOid,headRefName,baseRefName,statusCheckRollup",
    )
    if pr.returncode != 0:
        detail = "\n".join(part for part in (pr.stderr, pr.stdout) if part)
        if _github_auth_required(detail):
            reasons.append(
                "GitHub CLI authentication is required for deterministic CI verification"
            )
            return result(needs_user=True, blocker_kind="github_auth")
        # A failing view command is not absence evidence. Independently establish
        # exact-branch absence before requesting any host publication action.
        listed = _run(
            repo,
            "gh",
            "pr",
            "list",
            "--state",
            "all",
            "--head",
            str(lease["branch"]),
            "--json",
            "state,headRefOid,headRefName,baseRefName,statusCheckRollup",
        )
        if listed.returncode != 0:
            listed_detail = "\n".join(part for part in (listed.stderr, listed.stdout) if part)
            if _github_auth_required(listed_detail):
                reasons.append("GitHub CLI authentication is required: " + listed_detail)
                return result(needs_user=True, blocker_kind="github_auth")
            return host_error(
                "GitHub pull-request observation failed: "
                + "\n".join(part for part in (detail, listed.stderr, listed.stdout) if part),
                retryable=True,
            )
        try:
            candidates = json.loads(listed.stdout)
        except json.JSONDecodeError:
            return host_error("GitHub pull-request list was not valid JSON: " + listed.stdout)
        if not isinstance(candidates, list):
            return host_error("GitHub pull-request list was not an array")
        if candidates:
            return host_error(
                "GitHub pull-request view failed despite existing branch records: " + detail
            )
        reason = "no GitHub pull request is available for the current branch"
        reasons.append(reason)
        publication.append(reason)
        return result()
    try:
        payload = json.loads(pr.stdout)
    except json.JSONDecodeError:
        return host_error("GitHub pull-request status was not valid JSON: " + pr.stdout)
    if not isinstance(payload, dict):
        return host_error("GitHub pull-request status was not an object")
    checks = payload.get("statusCheckRollup") or []
    if not isinstance(checks, list) or any(not isinstance(item, dict) for item in checks):
        return host_error("GitHub pull-request checks were not an object array")
    pr_reasons = _pull_request_reasons(payload, manifest, head, lease)
    reasons.extend(pr_reasons)
    same_branch = (
        str(payload.get("state", "")).upper() == "OPEN"
        and payload.get("baseRefName") == manifest.base_branch
        and payload.get("headRefName") == lease.get("branch")
    )
    if not same_branch:
        return result(
            system_error="pull request ownership/base/state does not match the leased branch"
        )
    if payload.get("headRefOid") != head:
        publication.append("pull request is not at the current Git HEAD")
    else:
        by_name = {
            name: sorted(_check_conclusion(item) for item in checks if _check_name(item) == name)
            for name in manifest.required_checks
        }
        conclusions = [value for values in by_name.values() for value in values]
        pending_values = {"", "PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED"}
        missing = any(not values for values in by_name.values())
        pending = (
            not checks
            or any(value in pending_values for value in conclusions)
            or (missing and any(_check_conclusion(item) in pending_values for item in checks))
        )
        failing = any(
            value
            in {
                "FAILURE",
                "CANCELLED",
                "TIMED_OUT",
                "ACTION_REQUIRED",
                "ERROR",
                "SKIPPED",
                "NEUTRAL",
            }
            for value in conclusions
        )
        incomplete = any(
            not values or any(value != "SUCCESS" for value in values) for values in by_name.values()
        )
        if failing or (incomplete and not pending):
            local.append("ci_checks")
            remediation_fingerprint = _remediation_fingerprint(repo, "ci_checks", by_name)
            reasons.append(
                "Observed required CI check states: "
                + "; ".join(
                    f"{name}={','.join(values) if values else 'MISSING'}"
                    for name, values in sorted(by_name.items())
                    if not values or any(value != "SUCCESS" for value in values)
                )
            )
        elif incomplete and pending:
            return result(waiting_for_ci=True)
        else:
            # The existing completion policy also rejects hard failures on
            # additional checks. Diagnose them under their actual identity,
            # without calling them required names or hiding them behind acceptance.
            blocking: dict[str, list[str]] = {}
            for check in checks:
                name, conclusion = _check_name(check), _check_conclusion(check)
                if name not in manifest.required_checks and conclusion in _HARD_CI_FAILURES:
                    if not name:
                        return host_error("completion-blocking CI check has no observed name")
                    blocking.setdefault(name, []).append(conclusion)
            if blocking:
                facts = {name: sorted(values) for name, values in blocking.items()}
                local.append("ci_blocking_checks")
                remediation_fingerprint = _remediation_fingerprint(
                    repo, "ci_blocking_checks", facts
                )
                reasons.append(
                    "Observed additional completion-blocking CI states: "
                    + "; ".join(
                        f"{name}={','.join(values)}" for name, values in sorted(facts.items())
                    )
                )
    evidence = {
        "schema_version": 1,
        "role": role,
        "head": head,
        "lease_id": lease["lease_id"],
        "required_checks": list(manifest.required_checks),
        "pull_request": payload,
        "acceptance": acceptance_payload,
    }
    return result(ready=not reasons, evidence=evidence)


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
        if any(value in _HARD_CI_FAILURES for value in conclusions)
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
    return replace(
        gate,
        ready=not failures,
        reasons=failures,
        local_remediation=("local_validation",) if failures else (),
        remediation_fingerprint=(
            _remediation_fingerprint(_repo(config, role), "local_validation", failures)
            if failures
            else ""
        ),
    )


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
        identity = ""
        if not report.passed and not report.system_error:
            # These are host-generated diagnostic headers, not model assertions.
            # Full diagnostics remain feedback; only stable gate/command/exit facts
            # participate in obligation identity.
            header = report.feedback.split("STDOUT excerpt:", 1)[0].splitlines()
            facts = [
                line for line in header if line.startswith(("Gate: ", "Command: ", "Exit code: "))
            ]
            if len(facts) != 3:
                return CompletionGate(
                    False, system_error="final failure has no stable gate identity"
                )
            identity = _remediation_fingerprint(_repo(config, role), "local_validation", facts)
        return CompletionGate(
            False,
            (report.feedback,) if report.feedback else (),
            local_remediation=("local_validation",) if identity else (),
            remediation_fingerprint=identity,
            observation_fingerprint=gate.observation_fingerprint,
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
