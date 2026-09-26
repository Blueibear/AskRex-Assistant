from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .storage import AtomicJsonStore
from .types import OrchestratorConfig, TaskItem

_COMPLETION_PREFIX = "ralph-cloud-complete:"
_BRANCH_SAFE = re.compile(r"[^a-z0-9._-]+")
_SESSION_ID = re.compile(r"^(?:session_|cse_)[A-Za-z0-9_-]+$")


class ClaudeCloudError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaudeCloudSession:
    role: str
    task_id: str
    session_id: str
    url: str
    branch: str
    base_head: str
    launched_at: str
    status: str = "running"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ClaudeCloudPoll:
    status: str
    remote_head: str
    subject: str


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        encoding="utf-8",
        errors="replace",
    )


def _claude_executable() -> str:
    candidates = ("claude.cmd", "claude") if __import__("os").name == "nt" else ("claude",)
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise ClaudeCloudError("Claude Code CLI is not available on PATH")


def _repo_for_role(config: OrchestratorConfig, role: str) -> Path:
    if role == "backend":
        root = config.backend_root
    elif role == "mobile":
        root = config.mobile_root
    else:
        raise ClaudeCloudError(f"unsupported cloud role: {role}")
    if root is None:
        raise ClaudeCloudError(f"{role} repository root is not configured")
    return root.resolve()


def _slug(value: str, limit: int = 40) -> str:
    normalized = _BRANCH_SAFE.sub("-", value.strip().lower()).strip("-._")
    return (normalized or "task")[:limit].rstrip("-._") or "task"


def _session_store(config: OrchestratorConfig, role: str) -> AtomicJsonStore:
    return AtomicJsonStore(config.coordination_root / "cloud-sessions" / f"{role}.json")


def read_cloud_session(config: OrchestratorConfig, role: str) -> ClaudeCloudSession | None:
    payload = _session_store(config, role).read(default=None)
    if not payload:
        return None
    try:
        return ClaudeCloudSession(**payload)
    except TypeError as exc:
        raise ClaudeCloudError(f"malformed Claude cloud session record for {role}") from exc


def clear_cloud_session(config: OrchestratorConfig, role: str) -> None:
    path = _session_store(config, role).path
    path.unlink(missing_ok=True)


def _git_text(repo: Path, *args: str, timeout: int = 60) -> str:
    result = _run(["git", *args], cwd=repo, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ClaudeCloudError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _ensure_clean_repo(repo: Path) -> None:
    if _git_text(repo, "status", "--porcelain"):
        raise ClaudeCloudError("Claude cloud dispatch requires a clean role worktree")


def _ensure_github_remote(repo: Path) -> None:
    remote = _git_text(repo, "remote", "get-url", "origin")
    if "github.com" not in remote.casefold():
        raise ClaudeCloudError("Claude cloud dispatch requires a GitHub origin remote")


def _cloud_prompt(task: TaskItem, *, role: str, branch: str) -> str:
    completion = f"{_COMPLETION_PREFIX} {task.task_id}"
    feedback = f"\n\nCurrent reviewer/validator feedback:\n{task.feedback}" if task.feedback else ""
    return (
        "You are the implementation worker for the AskRex Ralph development loop. "
        "Read and follow CLAUDE.md and all repository agent instructions before editing. "
        f"Work only on branch {branch!r}; do not merge it, do not modify other branches, "
        "and do not claim physical-device verification. Preserve existing behavior outside the bounded task. "
        "Implement the task, add or update tests, run the relevant repository validation, and keep the working "
        "tree clean. Commit and push all completed work to the same branch. "
        f"When and only when the implementation and its automated validation are complete, make the FINAL commit "
        f"on the branch with subject exactly: {completion!r}. Ralph treats that exact final commit subject as the "
        "durable completion signal. If you are blocked, do not create the completion commit; leave an explanatory "
        "commit or session message instead.\n\n"
        f"Role: {role}\nTask ID: {task.task_id}\n\nTask:\n{task.prompt}{feedback}"
    )


def launch_cloud_session(
    config: OrchestratorConfig,
    *,
    role: str,
    task: TaskItem,
    execute: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> ClaudeCloudSession:
    existing = read_cloud_session(config, role)
    if existing is not None:
        raise ClaudeCloudError(
            f"{role} already has Claude cloud session {existing.session_id} in state {existing.status}"
        )
    repo = _repo_for_role(config, role)
    _ensure_clean_repo(repo)
    _ensure_github_remote(repo)
    base_head = _git_text(repo, "rev-parse", "HEAD")
    branch = f"ralph/cloud/{role}/{_slug(task.task_id)}-{uuid4().hex[:8]}"
    _git_text(repo, "branch", branch, base_head)
    try:
        _git_text(repo, "push", "--set-upstream", "origin", branch, timeout=120)
        temp_root = config.coordination_root / "cloud-worktrees"
        temp_root.mkdir(parents=True, exist_ok=True)
        worktree = Path(tempfile.mkdtemp(prefix=f"{role}-", dir=temp_root))
        try:
            add = _run(["git", "worktree", "add", "--detach", str(worktree), branch], cwd=repo)
            if add.returncode != 0:
                raise ClaudeCloudError(
                    "failed to create Claude cloud launch worktree: "
                    + (add.stderr or add.stdout).strip()
                )
            prompt = _cloud_prompt(task, role=role, branch=branch)
            runner = execute or _run
            result = runner(
                [
                    _claude_executable(),
                    "--cloud",
                    prompt,
                    "--output-format",
                    "json",
                ],
                cwd=worktree,
                timeout=120,
            )
            if result.returncode != 0:
                raise ClaudeCloudError(
                    "Claude cloud session launch failed: "
                    + (result.stderr or result.stdout).strip()
                )
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise ClaudeCloudError("Claude cloud launch did not return JSON") from exc
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                raise ClaudeCloudError(
                    "Claude cloud session launch was rejected: "
                    + str(payload.get("error", payload))
                )
            session_id = str(payload.get("session_id", "")).strip()
            url = str(payload.get("url", "")).strip()
            if not _SESSION_ID.match(session_id) or not url.startswith("https://claude.ai/code/"):
                raise ClaudeCloudError("Claude cloud launch returned invalid session metadata")
        finally:
            _run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo)
            shutil.rmtree(worktree, ignore_errors=True)
    except Exception:
        _run(["git", "push", "origin", "--delete", branch], cwd=repo)
        _run(["git", "branch", "-D", branch], cwd=repo)
        raise

    record = ClaudeCloudSession(
        role=role,
        task_id=task.task_id,
        session_id=session_id,
        url=url,
        branch=branch,
        base_head=base_head,
        launched_at=datetime.now(UTC).isoformat(),
    )
    _session_store(config, role).write(record.to_dict())
    return record


def poll_cloud_session(config: OrchestratorConfig, role: str) -> ClaudeCloudPoll:
    session = read_cloud_session(config, role)
    if session is None:
        raise ClaudeCloudError(f"{role} has no active Claude cloud session")
    repo = _repo_for_role(config, role)
    remote_ref = f"refs/remotes/origin/{session.branch}"
    _git_text(
        repo,
        "fetch",
        "--quiet",
        "origin",
        f"{session.branch}:{remote_ref}",
        timeout=120,
    )
    remote_head = _git_text(repo, "rev-parse", remote_ref)
    subject = _git_text(repo, "show", "-s", "--format=%s", remote_ref)
    expected = f"{_COMPLETION_PREFIX} {session.task_id}"
    if remote_head == session.base_head:
        status = "running"
    elif subject == expected:
        status = "ready_for_review"
    else:
        status = "working_changes_pushed"
    return ClaudeCloudPoll(status=status, remote_head=remote_head, subject=subject)


def adopt_cloud_session(config: OrchestratorConfig, role: str) -> str:
    session = read_cloud_session(config, role)
    if session is None:
        raise ClaudeCloudError(f"{role} has no active Claude cloud session")
    poll = poll_cloud_session(config, role)
    if poll.status != "ready_for_review":
        raise ClaudeCloudError(
            f"Claude cloud session {session.session_id} is not complete ({poll.status})"
        )
    repo = _repo_for_role(config, role)
    _ensure_clean_repo(repo)
    local_head = _git_text(repo, "rev-parse", "HEAD")
    if local_head != session.base_head:
        raise ClaudeCloudError(
            "role worktree moved after Claude cloud dispatch; refusing automatic adoption"
        )
    remote_ref = f"refs/remotes/origin/{session.branch}"
    _git_text(repo, "merge", "--ff-only", remote_ref, timeout=120)
    updated = ClaudeCloudSession(**{**session.to_dict(), "status": "adopted"})
    _session_store(config, role).write(updated.to_dict())
    return poll.remote_head
