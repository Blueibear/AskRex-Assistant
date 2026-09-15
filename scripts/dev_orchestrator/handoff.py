from __future__ import annotations

import getpass
import secrets
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .lifecycle import ControlPlaneLock
from .storage import AtomicJsonStore
from .types import OrchestratorConfig


class HandoffRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class CheckoutSnapshot:
    root: Path
    branch: str
    base_branch: str
    upstream: str
    repository: str
    head: str
    dirty: str


_BASE_BRANCH = {"backend": "master", "mobile": "main"}
_EXPECTED_REPOSITORY = {
    "backend": "blueibear/askrex-assistant",
    "mobile": "blueibear/askrex",
}


def _repo(config: OrchestratorConfig, role: str) -> Path:
    if role == "backend":
        root = config.backend_root
    elif role == "mobile":
        root = config.mobile_root
    else:
        raise ValueError(f"unsupported handoff role: {role}")
    if root is None:
        raise HandoffRequired(f"{role} repository root is not configured")
    return root.resolve()


def _git_result(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)


def _git(repo: Path, *args: str) -> str:
    result = _git_result(repo, *args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise HandoffRequired(f"cannot inspect {repo}: {detail}")
    return result.stdout.strip()


def _resolved_git_path(repo: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


def _repository_slug(url: str) -> str:
    raw = url.strip().replace("\\", "/")
    path = ""
    if raw.lower().startswith("git@github.com:"):
        path = raw.split(":", 1)[1]
    else:
        parsed = urlparse(raw)
        if (parsed.hostname or "").lower() != "github.com":
            return ""
        path = parsed.path
    normalized = path.strip("/")
    if normalized.lower().endswith(".git"):
        normalized = normalized[:-4]
    parts = [part for part in normalized.split("/") if part]
    if len(parts) != 2:
        return ""
    return "/".join(parts).lower()


def repo_snapshot(repo: Path) -> tuple[str, str]:
    head = _git(repo, "rev-parse", "HEAD")
    dirty = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    return head, dirty


def validate_worker_checkout(config: OrchestratorConfig, role: str) -> CheckoutSnapshot:
    repo = _repo(config, role)
    top = Path(_git(repo, "rev-parse", "--show-toplevel")).resolve()
    if top != repo:
        raise HandoffRequired(f"{role} root is not the Git worktree root")
    branch_result = _git_result(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch_result.returncode != 0:
        raise HandoffRequired(f"{role} checkout is detached; a task branch is required")
    branch = branch_result.stdout.strip()
    base_branch = _BASE_BRANCH[role]
    if branch.lower() in {"master", "main"}:
        raise HandoffRequired(f"{role} checkout is on protected branch {branch}")
    git_dir = _resolved_git_path(repo, _git(repo, "rev-parse", "--git-dir"))
    common_dir = _resolved_git_path(repo, _git(repo, "rev-parse", "--git-common-dir"))
    if git_dir == common_dir:
        raise HandoffRequired(f"{role} must use a linked worktree, not the primary checkout")
    origin = _git(repo, "remote", "get-url", "origin")
    repository = _repository_slug(origin)
    expected = _EXPECTED_REPOSITORY[role]
    if repository != expected:
        raise HandoffRequired(
            f"{role} origin repository mismatch: expected {expected}, got {repository}"
        )
    upstream_result = _git_result(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream_result.returncode != 0 or not upstream_result.stdout.strip():
        raise HandoffRequired(f"{role} branch has no configured upstream")
    upstream = upstream_result.stdout.strip()
    expected_upstream = f"origin/{branch}"
    if upstream != expected_upstream:
        raise HandoffRequired(
            f"{role} upstream must be the matching task branch {expected_upstream}"
        )
    head, dirty = repo_snapshot(repo)
    return CheckoutSnapshot(
        root=repo,
        branch=branch,
        base_branch=base_branch,
        upstream=upstream,
        repository=repository,
        head=head,
        dirty=dirty,
    )


def _handoff_dir(config: OrchestratorConfig) -> Path:
    path = config.coordination_root / "handoff"
    path.mkdir(parents=True, exist_ok=True)
    return path


def role_lease_lock_path(config: OrchestratorConfig, role: str) -> Path:
    return _handoff_dir(config) / f"{role}.lease.lock"


def _handoff_locked(function):
    def wrapped(config: OrchestratorConfig, *args, **kwargs):
        role = str(args[0] if args else kwargs["role"])
        with ControlPlaneLock(role_lease_lock_path(config, role)):
            return function(config, *args, **kwargs)

    return wrapped


@_handoff_locked
def request_handoff(config: OrchestratorConfig, role: str, *, worker_session: str) -> Path:
    repo = _repo(config, role)
    if not worker_session.strip():
        raise HandoffRequired("worker session identity is required for handoff")
    nonce = secrets.token_hex(24)
    request_id = str(uuid.uuid4())
    handoff_dir = _handoff_dir(config)
    path = handoff_dir / f"{role}.request.json"
    (handoff_dir / f"{role}.ack.json").unlink(missing_ok=True)
    (handoff_dir / f"{role}.json").unlink(missing_ok=True)
    AtomicJsonStore(path).write(
        {
            "role": role,
            "worktree": str(repo),
            "nonce": nonce,
            "request_id": request_id,
            "worker_session": worker_session.strip(),
            "requested_at": datetime.now(UTC).isoformat(),
            "requested_by": "supervisor",
        }
    )
    return path


@_handoff_locked
def worker_ack_handoff(
    config: OrchestratorConfig,
    role: str,
    *,
    nonce: str,
    source: str,
    worker_session: str,
    attested_by: str | None = None,
) -> Path:
    request_path = _handoff_dir(config) / f"{role}.request.json"
    request = AtomicJsonStore(request_path).read(default=None)
    if not request or str(request.get("nonce", "")) != nonce:
        raise HandoffRequired(f"{role} handoff nonce does not match the supervisor request")
    if source not in {"trusted-operator", "test"}:
        raise HandoffRequired(f"{role} handoff source must be the trusted operator or test harness")
    operator = (attested_by or getpass.getuser()).strip()
    if not operator:
        raise HandoffRequired(f"{role} handoff requires an operator identity")
    if str(request.get("worker_session", "")) != worker_session.strip():
        raise HandoffRequired(f"{role} worker session does not match the supervisor request")
    checkout = validate_worker_checkout(config, role)
    if checkout.dirty:
        raise HandoffRequired(f"{role} worktree must be clean before worker acknowledgment")
    path = _handoff_dir(config) / f"{role}.ack.json"
    AtomicJsonStore(path).write(
        {
            "role": role,
            "nonce": nonce,
            "request_id": str(request.get("request_id", "")),
            "requested_at": str(request.get("requested_at", "")),
            "source": source,
            "attested_by": operator,
            "worker_session": worker_session.strip(),
            "worktree": str(checkout.root),
            "branch": checkout.branch,
            "upstream": checkout.upstream,
            "repository": checkout.repository,
            "head": checkout.head,
            "stopped": True,
            "acknowledged_at": datetime.now(UTC).isoformat(),
        }
    )
    return path


@_handoff_locked
def accept_handoff(
    config: OrchestratorConfig,
    role: str,
    *,
    nonce: str,
    source: str | None = None,
) -> Path:
    handoff_dir = _handoff_dir(config)
    request_path = handoff_dir / f"{role}.request.json"
    ack_path = handoff_dir / f"{role}.ack.json"
    request = AtomicJsonStore(request_path).read(default=None)
    ack = AtomicJsonStore(ack_path).read(default=None)
    if not request or str(request.get("nonce", "")) != nonce:
        raise HandoffRequired(f"{role} handoff nonce is not the current supervisor request")
    if not ack or str(ack.get("nonce", "")) != nonce:
        raise HandoffRequired(f"{role} handoff nonce has not been acknowledged by the worker")
    for key in ("role", "worktree", "request_id", "requested_at", "worker_session"):
        if str(ack.get(key, "")) != str(request.get(key, "")):
            raise HandoffRequired(f"{role} handoff acknowledgment does not match current request")
    if not ack.get("stopped"):
        raise HandoffRequired(f"{role} worker did not acknowledge that it stopped writing")
    ack_source = str(ack.get("source", ""))
    if ack_source not in {"trusted-operator", "test"}:
        raise HandoffRequired(f"{role} handoff acknowledgment is not operator-attested")
    if ack_source == "trusted-operator":
        stamp = datetime.fromisoformat(str(ack.get("acknowledged_at", "")))
        remaining = 2.0 - (datetime.now(UTC) - stamp).total_seconds()
        if remaining > 0:
            time.sleep(remaining)
    if source is not None and ack_source != source:
        raise HandoffRequired(f"{role} handoff source does not match the worker acknowledgment")
    checkout = validate_worker_checkout(config, role)
    if checkout.dirty:
        raise HandoffRequired(f"{role} worktree must be clean before supervisor lease acceptance")
    for key, expected in (
        ("worktree", str(checkout.root)),
        ("branch", checkout.branch),
        ("upstream", checkout.upstream),
        ("repository", checkout.repository),
        ("head", checkout.head),
    ):
        if str(ack.get(key, "")) != expected:
            raise HandoffRequired(f"{role} worker acknowledgment changed before lease acceptance")
    lease_path = _handoff_dir(config) / f"{role}.json"
    AtomicJsonStore(lease_path).write(
        {
            "role": role,
            "owner": "supervisor",
            "lease_id": str(uuid.uuid4()),
            "handoff_nonce": nonce,
            "handoff_request_id": str(request.get("request_id", "")),
            "source": ack_source,
            "attested_by": str(ack.get("attested_by", "")),
            "worker_session": str(ack.get("worker_session", "")),
            "worktree": str(checkout.root),
            "branch": checkout.branch,
            "base_branch": checkout.base_branch,
            "upstream": checkout.upstream,
            "repository": checkout.repository,
            "head": checkout.head,
            "accepted_at": datetime.now(UTC).isoformat(),
        }
    )
    request_path.unlink(missing_ok=True)
    ack_path.unlink(missing_ok=True)
    return lease_path


@_handoff_locked
def validate_handoff(config: OrchestratorConfig, role: str) -> None:
    checkout = validate_worker_checkout(config, role)
    handoff_dir = _handoff_dir(config)
    if (handoff_dir / f"{role}.request.json").exists() or (
        handoff_dir / f"{role}.ack.json"
    ).exists():
        raise HandoffRequired(f"{role} handoff request is pending; prior lease is invalid")
    path = handoff_dir / f"{role}.json"
    record = AtomicJsonStore(path).read(default=None)
    if not record:
        raise HandoffRequired(f"{role} supervisor lease is missing")
    if record.get("owner") != "supervisor" or not record.get("lease_id"):
        raise HandoffRequired(f"{role} handoff is not an active supervisor lease")
    expected = {
        "worktree": str(checkout.root),
        "branch": checkout.branch,
        "base_branch": checkout.base_branch,
        "upstream": checkout.upstream,
        "repository": checkout.repository,
    }
    for key, value in expected.items():
        if str(record.get(key, "")) != value:
            raise HandoffRequired(f"{role} supervisor lease no longer matches {key}")
    if checkout.dirty:
        raise HandoffRequired(f"{role} worktree changed after handoff and is not clean")
    if str(record.get("head", "")) != checkout.head:
        raise HandoffRequired(f"{role} HEAD changed after supervisor handoff")


def validate_handoffs(config: OrchestratorConfig) -> None:
    for role in ("backend", "mobile"):
        validate_handoff(config, role)


def verify_invocation_commits(
    repo: Path,
    pre_head: str,
    post_head: str,
    invocation_id: str,
) -> None:
    if post_head == pre_head:
        return
    ancestor = _git_result(repo, "merge-base", "--is-ancestor", pre_head, post_head)
    if ancestor.returncode != 0:
        raise HandoffRequired("agent result is not a descendant of the leased pre-invocation HEAD")
    commits = _git(repo, "rev-list", "--reverse", f"{pre_head}..{post_head}").splitlines()
    if not commits:
        raise HandoffRequired("HEAD changed but no invocation commits were found")
    trailer = f"AskRex-Orchestrator-Invocation: {invocation_id}"
    for commit in commits:
        body = _git(repo, "show", "-s", "--format=%B", commit)
        if trailer not in body.splitlines():
            raise HandoffRequired(
                f"commit {commit[:12]} lacks the required orchestrator invocation trailer"
            )


@_handoff_locked
def advance_handoff(
    config: OrchestratorConfig,
    role: str,
    *,
    pre_head: str,
    post_head: str,
    invocation_id: str,
    provider: str,
    pending_result: dict[str, Any] | None = None,
) -> Path:
    path = _handoff_dir(config) / f"{role}.json"
    record = AtomicJsonStore(path).read(default=None)
    if not record or record.get("owner") != "supervisor":
        raise HandoffRequired(f"{role} supervisor lease is missing")
    if str(record.get("head", "")) != pre_head:
        raise HandoffRequired(f"{role} lease HEAD changed before invocation completion")
    existing_pending = record.get("pending_result")
    if existing_pending and str(existing_pending.get("invocation_id", "")) != invocation_id:
        raise HandoffRequired(f"{role} has an unconsumed pending result")
    checkout = validate_worker_checkout(config, role)
    if checkout.dirty:
        raise HandoffRequired(f"{role} worker left the worktree dirty; refusing trusted checkpoint")
    if checkout.head != post_head:
        raise HandoffRequired(f"{role} HEAD changed again after invocation")
    if provider == "codex":
        codex_implementation = bool(
            pending_result and str(pending_result.get("phase", "")) == "implement"
        )
        if post_head != pre_head:
            if not codex_implementation:
                raise HandoffRequired("read-only Codex invocation changed repository HEAD")
            verify_invocation_commits(checkout.root, pre_head, post_head, invocation_id)
    elif provider == "claude":
        verify_invocation_commits(checkout.root, pre_head, post_head, invocation_id)
    elif provider == "openai":
        if post_head != pre_head:
            raise HandoffRequired("read-only OpenAI invocation changed repository HEAD")
    else:
        raise HandoffRequired(f"unsupported invocation provider: {provider}")
    record["head"] = post_head
    record["advanced_at"] = datetime.now(UTC).isoformat()
    record["last_invocation_id"] = invocation_id
    record["last_provider"] = provider
    if pending_result is not None:
        record["pending_result"] = pending_result
    AtomicJsonStore(path).write(record)
    return path


def read_pending_result(config: OrchestratorConfig, role: str) -> dict[str, Any] | None:
    path = _handoff_dir(config) / f"{role}.json"
    record = AtomicJsonStore(path).read(default=None)
    if not record or record.get("owner") != "supervisor":
        return None
    pending = record.get("pending_result")
    return dict(pending) if isinstance(pending, dict) else None


@_handoff_locked
def clear_pending_result(config: OrchestratorConfig, role: str, *, invocation_id: str) -> None:
    path = _handoff_dir(config) / f"{role}.json"
    store = AtomicJsonStore(path)
    record = store.read(default=None)
    if not record or record.get("owner") != "supervisor":
        raise HandoffRequired(f"{role} supervisor lease is missing")
    pending = record.get("pending_result")
    if not pending:
        return
    if str(pending.get("invocation_id", "")) != invocation_id:
        raise HandoffRequired(f"{role} pending result invocation mismatch")
    record.pop("pending_result", None)
    store.write(record)


def acknowledge_handoff(config: OrchestratorConfig, role: str, *, source: str) -> Path:
    """Test-only compatibility helper; production handoff is request/worker-ack/accept."""
    if source != "test":
        raise HandoffRequired("direct handoff acknowledgment is test-only")
    request = request_handoff(config, role, worker_session="test-session")
    nonce = str(AtomicJsonStore(request).read()["nonce"])
    worker_ack_handoff(config, role, nonce=nonce, source="test", worker_session="test-session")
    return accept_handoff(config, role, nonce=nonce, source="test")
