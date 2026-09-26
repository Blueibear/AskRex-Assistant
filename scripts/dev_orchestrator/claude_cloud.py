from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .handoff import advance_handoff, validate_handoff
from .storage import AtomicJsonStore
from .types import AgentResult, OrchestratorConfig, TaskItem

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
    invocation_id: str = ""
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
    candidates = ("claude.cmd", "claude") if os.name == "nt" else ("claude",)
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise ClaudeCloudError("Claude Code CLI is not available on PATH")


_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def _parse_cloud_launch_metadata(output: str) -> tuple[str, str]:
    cleaned = _ANSI_ESCAPE.sub("", output).replace("\r", "")
    session_match = re.search(r"Session ID:\s*((?:session_|cse_)[A-Za-z0-9_-]+)", cleaned)
    if not session_match:
        session_match = re.search(
            r"Resume with:\s*claude\s+--teleport\s+((?:session_|cse_)[A-Za-z0-9_-]+)",
            cleaned,
        )
    url_match = re.search(r"View:\s*(https://claude\.ai/code/\S+)", cleaned)
    if not session_match or not url_match:
        raise ClaudeCloudError("Claude cloud launch output did not contain session metadata")
    return session_match.group(1), url_match.group(1).rstrip()


def _launch_cloud_interactive(
    *,
    coordination_root: Path,
    worktree: Path,
    prompt: str,
    timeout: int = 120,
) -> tuple[str, str]:
    del coordination_root
    if os.name != "nt":
        raise ClaudeCloudError("automatic Claude cloud launch currently requires Windows ConPTY")
    try:
        from winpty import PtyProcess
    except ImportError as exc:
        raise ClaudeCloudError(
            "Claude cloud launch requires the Windows dev dependency pywinpty"
        ) from exc

    proc = PtyProcess.spawn(
        [_claude_executable(), "--cloud", prompt],
        cwd=str(worktree),
        dimensions=(40, 160),
    )
    try:
        proc.fileobj.settimeout(0.5)
        chunks: list[str] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = proc.read(4096)
            except (EOFError, TimeoutError, OSError):
                chunk = ""
            if chunk:
                chunks.append(chunk)
            if not proc.isalive():
                break
        else:
            proc.terminate(force=True)
            raise ClaudeCloudError("Claude cloud launch timed out waiting for session metadata")

        output = "".join(chunks)
        exit_status = getattr(proc, "exitstatus", None)
        if exit_status not in (None, 0):
            cleaned = _ANSI_ESCAPE.sub("", output).replace("\r", "")
            errors = [line.strip() for line in cleaned.splitlines() if "Error:" in line]
            detail = errors[-1] if errors else "Claude cloud CLI exited unsuccessfully"
            raise ClaudeCloudError(f"Claude cloud session launch failed: {detail}")
        return _parse_cloud_launch_metadata(output)
    finally:
        try:
            proc.close(force=True)
        except Exception:
            pass


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


def _cloud_prompt(
    task: TaskItem,
    *,
    role: str,
    branch: str,
    invocation_id: str = "",
    context: str = "",
) -> str:
    completion = f"{_COMPLETION_PREFIX} {task.task_id}"
    feedback = f"\n\nCurrent reviewer/validator feedback:\n{task.feedback}" if task.feedback else ""
    bounded_context = (
        f"\n\nAuthoritative bounded coordination context supplied by Ralph:\n{context}"
        if context
        else ""
    )
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
        f"Role: {role}\nTask ID: {task.task_id}\n"
        f"AskRex-Orchestrator-Invocation-ID: {invocation_id}\n\n"
        f"Task:\n{task.prompt}{feedback}{bounded_context}"
    )


def launch_cloud_session(
    config: OrchestratorConfig,
    *,
    role: str,
    task: TaskItem,
    invocation_id: str | None = None,
    context: str = "",
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
    cloud_invocation_id = invocation_id or str(uuid4())
    branch = f"ralph/cloud/{role}/{_slug(task.task_id)}-{uuid4().hex[:8]}"
    _git_text(repo, "branch", branch, base_head)
    try:
        _git_text(repo, "push", "--set-upstream", "origin", branch, timeout=120)
        temp_root = config.coordination_root / "cloud-worktrees"
        temp_root.mkdir(parents=True, exist_ok=True)
        worktree = Path(tempfile.mkdtemp(prefix=f"{role}-", dir=temp_root))
        try:
            add = _run(["git", "worktree", "add", str(worktree), branch], cwd=repo)
            if add.returncode != 0:
                raise ClaudeCloudError(
                    "failed to create Claude cloud launch worktree: "
                    + (add.stderr or add.stdout).strip()
                )
            prompt = _cloud_prompt(
                task,
                role=role,
                branch=branch,
                invocation_id=cloud_invocation_id,
                context=context,
            )
            if execute is None:
                session_id, url = _launch_cloud_interactive(
                    coordination_root=config.coordination_root,
                    worktree=worktree,
                    prompt=prompt,
                    timeout=120,
                )
            else:
                result = execute(
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
        invocation_id=cloud_invocation_id,
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


def adopt_cloud_session(config: OrchestratorConfig, role: str) -> AgentResult:
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
    validate_handoff(config, role)
    remote_ref = f"refs/remotes/origin/{session.branch}"
    _git_text(repo, "merge", "--ff-only", remote_ref, timeout=120)
    post_head = _git_text(repo, "rev-parse", "HEAD")
    if post_head != poll.remote_head:
        raise ClaudeCloudError(
            "Claude cloud adoption HEAD did not match the verified remote result"
        )

    invocation_id = session.invocation_id or str(uuid4())
    result = AgentResult(
        outcome="ready_for_review",
        summary=f"Claude cloud session {session.session_id} completed and was adopted.",
        next_action="Run deterministic validation, then independent Codex review.",
        task_id=session.task_id,
        role=role,
        invocation_id=invocation_id,
    )
    pending_result = {
        "phase": "implement",
        "role": role,
        "task_id": session.task_id,
        "invocation_id": invocation_id,
        "pre_head": session.base_head,
        "post_head": post_head,
        "result": asdict(result),
    }
    advance_handoff(
        config,
        role,
        pre_head=session.base_head,
        post_head=post_head,
        invocation_id=invocation_id,
        provider="claude_cloud",
        pending_result=pending_result,
    )
    clear_cloud_session(config, role)
    return result
