from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .coordination import validate_agent_updates
from .handoff import (
    HandoffRequired,
    advance_handoff,
    repo_snapshot,
    role_lease_lock_path,
    validate_handoff,
)
from .lifecycle import ControlPlaneLock
from .paths import validate_runtime_paths
from .schema import validate_agent_result
from .types import AgentResult

_SCHEMA_PATH = Path(__file__).with_name("agent-result.schema.json")
_CODEX_SCHEMA_PATH = Path(__file__).with_name("agent-result.codex.schema.json")
CLAUDE_SANDBOX_IMAGE = "askrex-claude-code:2.1.238"
CLAUDE_SANDBOX_IMAGE_ID = "sha256:dfb3f6d369cc03f97088481ad1cf01b581e6f3ae5247351d9772b76447330530"
_CLAUDE_DOCKERFILE = Path(__file__).with_name("claude_sandbox.Dockerfile")
_SCRATCH_OWNER_FILENAME = ".askrex-scratch-owner.json"


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


def _remove_scratch_tree(path: Path, *, attempts: int = 60, delay_seconds: float = 0.25) -> None:
    for attempt in range(attempts):
        try:
            tempfile.TemporaryDirectory._rmtree(str(path), ignore_errors=False)
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) != 32:
                raise
            if attempt + 1 >= attempts:
                return
            time.sleep(delay_seconds)


@contextmanager
def _temporary_scratch_root(prefix: str):
    root = Path(tempfile.mkdtemp(prefix=prefix))
    cleanup = {"remove": True}
    try:
        yield root, cleanup
    finally:
        if cleanup["remove"]:
            _remove_scratch_tree(root)


def _schema_json() -> str:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    schema.pop("$schema", None)
    schema.pop("allOf", None)
    return json.dumps(schema, separators=(",", ":"), sort_keys=True)


def build_claude_command(repo: Path, prompt: str, model: str) -> list[str]:
    return [
        "claude",
        "-p",
        "--model",
        model,
        "--permission-mode",
        "acceptEdits",
        "--safe-mode",
        "--tools",
        "Read,Write,Edit,Glob,Grep",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--json-schema",
        _schema_json(),
        prompt,
    ]


def build_claude_sandbox_command(
    repo: Path, prompt: str, model: str, *, container_name: str
) -> list[str]:
    credentials = Path.home() / ".claude" / ".credentials.json"
    if not credentials.is_file():
        raise RuntimeError("Claude sandbox credentials are unavailable")
    inner = build_claude_command(Path("/workspace"), prompt, model)
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--mount",
        f"type=bind,src={repo.resolve()},dst=/workspace",
        "--mount",
        f"type=bind,src={(repo / '.git').resolve()},dst=/workspace/.git,readonly",
        "--mount",
        f"type=bind,src={credentials.resolve()},dst=/home/node/.claude/.credentials.json,readonly",
        "--workdir",
        "/workspace",
        CLAUDE_SANDBOX_IMAGE_ID,
        *inner[1:],
    ]


def _docker_image_state() -> tuple[str, str]:
    inspected = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", CLAUDE_SANDBOX_IMAGE],
        capture_output=True,
        text=True,
        check=False,
    )
    if inspected.returncode == 0:
        image_id = inspected.stdout.strip()
        return ("present", image_id) if image_id else ("unknown", "")
    detail = f"{inspected.stdout}\n{inspected.stderr}".lower()
    if "no such image" in detail or "no such object" in detail:
        return "absent", ""
    return "unknown", ""


def _docker_image_id() -> str:
    state, image_id = _docker_image_state()
    return image_id if state == "present" else ""


def ensure_claude_sandbox_image() -> None:
    state, image_id = _docker_image_state()
    if state == "unknown":
        raise RuntimeError("Claude sandbox image state is unavailable or ambiguous")
    if state == "absent":
        built = subprocess.run(
            [
                "docker",
                "build",
                "--pull=false",
                "-t",
                CLAUDE_SANDBOX_IMAGE,
                "-f",
                str(_CLAUDE_DOCKERFILE),
                str(_CLAUDE_DOCKERFILE.parent),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if built.returncode != 0:
            raise RuntimeError(
                f"cannot build Claude sandbox image: {built.stderr.strip() or built.stdout.strip()}"
            )
        state, image_id = _docker_image_state()
        if state != "present":
            raise RuntimeError("Claude sandbox image state is unavailable after build")
    if image_id != CLAUDE_SANDBOX_IMAGE_ID:
        raise RuntimeError(
            f"Claude sandbox image identity mismatch: expected {CLAUDE_SANDBOX_IMAGE_ID}, got {image_id or 'missing'}"
        )


def build_codex_command(kind: str, repo: Path, prompt: str, model: str) -> list[str]:
    if kind not in {"review", "lead"}:
        raise ValueError(f"unsupported Codex role: {kind}")
    return [
        "codex.cmd" if os.name == "nt" else "codex",
        "-a",
        "never",
        "exec",
        "-m",
        model,
        "-s",
        "read-only",
        "-C",
        str(repo),
        "--output-schema",
        str(_CODEX_SCHEMA_PATH),
        prompt,
    ]


def _terminate_process_tree(process: subprocess.Popen[str]) -> bool:
    if process.poll() is not None:
        return True
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
        return completed.returncode == 0 or process.poll() is not None
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.wait(timeout=5)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _docker_container_state(name: str) -> str:
    if not name:
        return "absent"
    inspected = subprocess.run(
        ["docker", "container", "inspect", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if inspected.returncode == 0:
        return "present"
    detail = f"{inspected.stdout}\n{inspected.stderr}".lower()
    if "no such container" in detail or "no such object" in detail:
        return "absent"
    return "unknown"


def _docker_container_present(name: str) -> bool:
    return _docker_container_state(name) == "present"


def _remove_docker_container(name: str) -> bool:
    if not name:
        return True
    state = _docker_container_state(name)
    if state == "absent":
        return True
    if state == "unknown":
        return False
    subprocess.run(
        ["docker", "rm", "-f", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return _docker_container_state(name) == "absent"


def run_command(
    command: list[str],
    cwd: Path,
    timeout_seconds: int = 1800,
    *,
    activity_file: Path | None = None,
    activity_metadata: dict[str, Any] | None = None,
    stdin_text: str | None = None,
) -> ProcessResult:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    activity_store = None
    payload = dict(activity_metadata or {})
    preserve_activity_on_success = bool(payload.pop("_preserve_activity_on_success", False))
    container_name = str(payload.get("container_name", ""))
    if activity_file is not None:
        from .storage import AtomicJsonStore

        activity_store = AtomicJsonStore(activity_file)
        payload.update(
            {
                "status": "launching",
                "pid": None,
                "cwd": str(cwd),
                "launcher_pid": os.getpid(),
                "marker_created_at": datetime.now(UTC).isoformat(),
            }
        )
        activity_store.write(payload)
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if stdin_text is not None else None,
            text=True,
            encoding="utf-8",
            errors="strict",
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    except Exception:
        if activity_file is not None:
            activity_file.unlink(missing_ok=True)
        raise
    clear_activity = False
    if activity_store is not None:
        payload.update(
            {
                "status": "active",
                "pid": process.pid,
                "process_started_at": datetime.now(UTC).isoformat(),
            }
        )
        try:
            activity_store.write(payload)
        except Exception:
            _terminate_process_tree(process)
            if container_name:
                _remove_docker_container(container_name)
            raise
    try:
        stdout, stderr = process.communicate(input=stdin_text, timeout=timeout_seconds)
        returncode = process.returncode or 0
        if activity_store is not None and preserve_activity_on_success and returncode == 0:
            payload.update(
                {"status": "postprocessing", "process_completed_at": datetime.now(UTC).isoformat()}
            )
            activity_store.write(payload)
            clear_activity = False
        else:
            clear_activity = True
        return ProcessResult(returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        process_stopped = _terminate_process_tree(process)
        container_stopped = _remove_docker_container(container_name) if container_name else True
        clear_activity = process_stopped and container_stopped
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", "process timed out and process tree could not be confirmed stopped"
        return ProcessResult(124, stdout or "", stderr or "process timed out")
    finally:
        if clear_activity and container_name:
            clear_activity = _remove_docker_container(container_name)
        if activity_file is not None and clear_activity:
            activity_file.unlink(missing_ok=True)


def classify_cli_failure(text: str, returncode: int) -> str:
    normalized = text.lower()
    if "usage limit" in normalized or "rate limit" in normalized or "quota" in normalized:
        return "usage_limit"
    if (
        "login" in normalized
        or "not authenticated" in normalized
        or "authentication" in normalized
        or "authenticate" in normalized
    ):
        return "auth"
    if returncode == 124 or "timed out" in normalized or "timeout" in normalized:
        return "timeout"
    return "failed"


def _find_result(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if "outcome" in value and "summary" in value and "next_action" in value:
            return value
        for key in ("structured_output", "result", "message", "content"):
            nested = value.get(key)
            if isinstance(nested, str):
                try:
                    parsed = json.loads(nested)
                except json.JSONDecodeError:
                    continue
                found = _find_result(parsed)
                if found:
                    return found
            else:
                found = _find_result(nested)
                if found:
                    return found
    if isinstance(value, list):
        for item in value:
            found = _find_result(item)
            if found:
                return found
    return None


def extract_agent_result(output: str) -> AgentResult:
    candidates = [output.strip()]
    candidates.extend(line.strip() for line in output.splitlines() if line.strip())
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        found = _find_result(parsed)
        if found:
            return validate_agent_result(found)
    raise ValueError("agent output did not contain a valid structured result")


class AgentInvocationError(RuntimeError):
    def __init__(self, provider: str, kind: str, detail: str) -> None:
        super().__init__(f"{provider} {kind}: {detail}")
        self.provider = provider
        self.kind = kind
        self.detail = detail


def _append_extra_dir(command: list[str], directory: Path) -> list[str]:
    return [*command[:-1], "--add-dir", str(directory), command[-1]]


def _task_prompt(role: str, task, coordination_root: Path, context: str, invocation_id: str) -> str:
    feedback = f"\nReviewer feedback to address:\n{task.feedback}" if task.feedback else ""
    return (
        f"You are the {role} AskRex implementation worker. Work only in the current repository. "
        f"Task {task.task_id}: {task.prompt}.{feedback} Read CLAUDE.md in this repository first, then edit "
        "the implementation and tests needed for the task. Shell/Bash/Web/MCP tools are intentionally unavailable; "
        "do not attempt to run tests or create commits because the deterministic supervisor owns validation and Git checkpointing. "
        "Do not modify the other repository or the frozen "
        "rex-ai-pc-test worktree. You do not have filesystem write access to the shared coordination root; "
        "use the structured coordination_messages and issue_updates fields for requested shared-state changes. "
        f"The supervisor will create the Git checkpoint with invocation identity {invocation_id}; do not alter Git metadata. "
        f"For continue or ready_for_review, return task_id={task.task_id!r}, role={role!r}, and invocation_id={invocation_id!r} exactly. "
        "Return only the required structured result.\n\n"
        f"Coordination root reference: {coordination_root}\n"
        "Authoritative bounded coordination snapshot:\n"
        f"{context}"
    )


def _review_prompt(
    role: str, task, coordination_root: Path, context: str, invocation_id: str
) -> str:
    return (
        f"Independently review AskRex {role} task {task.task_id}: {task.prompt}. "
        "Inspect the current diff, relevant tests, security/ownership constraints, and claimed validation. "
        "Do not modify files. Return pass only when the task is actually ready; otherwise return changes_required, "
        "blocked_user, blocked_system, or failed using the required structured result. On a passing owned TEST issue, "
        "include an issue_updates request for fixed-needs-retest; never request verified. "
        f"Return task_id={task.task_id!r}, role={role!r}, and invocation_id={invocation_id!r} exactly.\n\n"
        f"Coordination root reference: {coordination_root}\n"
        "Authoritative bounded coordination snapshot:\n"
        f"{context}"
    )


def _lead_prompt(role: str, coordination_root: Path, context: str, invocation_id: str) -> str:
    return (
        f"Act as Astra, lead engineer for the AskRex {role} workstream. Select only the next highest-priority "
        "actionable task that belongs to this role and does not require James. Return assign with a stable task_id "
        "and bounded task_prompt, done only when the bounded snapshot appears exhausted, or a truthful blocker. "
        "The deterministic supervisor independently verifies any done claim. Do not modify files. Return only the "
        "required structured result. "
        f"Return role={role!r} and invocation_id={invocation_id!r} exactly.\n\n"
        f"Coordination root reference: {coordination_root}\n"
        "Authoritative bounded coordination snapshot:\n"
        f"{context}"
    )


def _git_run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=repo, capture_output=True, text=True, check=False)


def _clone_scratch_repo(repo: Path, pre_head: str, scratch: Path) -> None:
    cloned = subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(repo), str(scratch)],
        capture_output=True,
        text=True,
        check=False,
    )
    if cloned.returncode != 0:
        raise HandoffRequired(f"cannot create Claude scratch clone: {cloned.stderr.strip()}")
    checked = subprocess.run(
        ["git", "checkout", "--quiet", "--detach", pre_head],
        cwd=scratch,
        capture_output=True,
        text=True,
        check=False,
    )
    if checked.returncode != 0:
        raise HandoffRequired(
            f"cannot checkout leased HEAD in scratch clone: {checked.stderr.strip()}"
        )


def _write_scratch_owner(scratch: Path, *, role: str, invocation_id: str, pre_head: str) -> str:
    nonce = uuid.uuid4().hex
    payload = {
        "nonce": nonce,
        "role": role,
        "invocation_id": invocation_id,
        "pre_head": pre_head,
        "scratch_path": str(scratch.resolve()),
    }
    owner = scratch.parent / _SCRATCH_OWNER_FILENAME
    owner.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return nonce


def _create_scratch_commit(scratch: Path, pre_head: str, invocation_id: str) -> str | None:
    head = _git_run(scratch, "git", "rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != pre_head:
        raise HandoffRequired("Claude scratch clone changed Git HEAD")
    staged = _git_run(scratch, "git", "add", "-A")
    if staged.returncode != 0:
        raise HandoffRequired("cannot stage Claude scratch changes")
    changed = _git_run(scratch, "git", "diff", "--cached", "--quiet", pre_head, "--")
    if changed.returncode == 0:
        return None
    if changed.returncode != 1:
        raise HandoffRequired("cannot inspect Claude scratch changes")
    diff_check = _git_run(scratch, "git", "diff", "--cached", "--check")
    if diff_check.returncode != 0:
        raise HandoffRequired(
            f"Claude scratch changes failed git diff --check: {diff_check.stdout.strip()}"
        )
    commit = _git_run(
        scratch,
        "git",
        "-c",
        "user.name=AskRex Orchestrator",
        "-c",
        "user.email=orchestrator@askrex.local",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--no-verify",
        "-m",
        f"chore(orchestrator): checkpoint {invocation_id[:12]}",
        "-m",
        f"AskRex-Orchestrator-Invocation: {invocation_id}",
    )
    if commit.returncode != 0:
        raise HandoffRequired(f"cannot create Claude scratch commit: {commit.stderr.strip()}")
    new_head = _git_run(scratch, "git", "rev-parse", "HEAD")
    parent = _git_run(scratch, "git", "rev-parse", "HEAD^")
    if new_head.returncode != 0 or parent.returncode != 0 or parent.stdout.strip() != pre_head:
        raise HandoffRequired("Claude scratch commit is not a direct child of leased HEAD")
    return new_head.stdout.strip()


def _publish_scratch_commit(
    repo: Path, scratch: Path, pre_head: str, scratch_head: str | None
) -> None:
    head, dirty = repo_snapshot(repo)
    if head != pre_head or dirty:
        raise HandoffRequired("live repository changed while Claude worked in scratch clone")
    if scratch_head is None:
        return
    fetched = _git_run(repo, "git", "fetch", "--quiet", "--no-tags", str(scratch), scratch_head)
    if fetched.returncode != 0:
        raise HandoffRequired(f"cannot import Claude scratch commit: {fetched.stderr.strip()}")
    head, dirty = repo_snapshot(repo)
    if head != pre_head or dirty:
        raise HandoffRequired("live repository changed before Claude commit publication")
    merged = _git_run(repo, "git", "merge", "--ff-only", "--no-edit", scratch_head)
    if merged.returncode != 0:
        post_head, post_dirty = repo_snapshot(repo)
        if post_head != pre_head or post_dirty:
            raise HandoffRequired(
                "Claude commit publication failed and live repository is not pristine"
            )
        raise HandoffRequired(
            f"cannot fast-forward to Claude scratch commit: {merged.stderr.strip() or merged.stdout.strip()}"
        )
    post_head, post_dirty = repo_snapshot(repo)
    if post_head != scratch_head or post_dirty:
        raise HandoffRequired("live repository changed during Claude commit publication")


_CUSTOM_EXECUTOR_PROCESS_LOCK = threading.RLock()


@contextmanager
def _custom_executor_process_scope():
    with _CUSTOM_EXECUTOR_PROCESS_LOCK:
        original_popen = subprocess.Popen
        original_thread_start = threading.Thread.start
        tracked_processes: list[subprocess.Popen] = []
        patched_os: dict[str, object] = {}

        def tracking_popen(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            tracked_processes.append(process)
            return process

        def reject_thread_start(thread, *args, **kwargs):
            target = getattr(thread, "_target", None)
            owner = getattr(target, "__self__", None)
            if (
                isinstance(owner, original_popen)
                and getattr(target, "__name__", "") == "_readerthread"
            ):
                return original_thread_start(thread, *args, **kwargs)
            raise RuntimeError("custom executor callbacks cannot start asynchronous threads")

        def reject_async_process(*_args, **_kwargs):
            raise RuntimeError(
                "custom executor callbacks must use synchronous subprocess execution"
            )

        subprocess.Popen = tracking_popen
        threading.Thread.start = reject_thread_start
        for name in (
            "spawnl",
            "spawnle",
            "spawnlp",
            "spawnlpe",
            "spawnv",
            "spawnve",
            "spawnvp",
            "spawnvpe",
            "startfile",
        ):
            if hasattr(os, name):
                patched_os[name] = getattr(os, name)
                setattr(os, name, reject_async_process)
        try:
            yield
        finally:
            subprocess.Popen = original_popen
            threading.Thread.start = original_thread_start
            for name, original in patched_os.items():
                setattr(os, name, original)
            cleanup_failed = False
            for process in reversed(tracked_processes):
                if process.poll() is None and not _terminate_process_tree(process):
                    cleanup_failed = True
            if cleanup_failed:
                raise HandoffRequired("custom executor left an unterminated child process")


def _tree_state(root: Path) -> tuple[tuple[object, ...], ...]:
    root = root.resolve()
    if not root.exists():
        return (("<missing>",),)
    entries: list[tuple[object, ...]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        info = current.lstat()
        relative = "." if current == root else current.relative_to(root).as_posix()
        mode = info.st_mode
        if os.name == "nt" and (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise HandoffRequired(
                f"custom executor repository seal refuses Windows reparse point: {current}"
            )
        if stat.S_ISLNK(mode):
            entries.append(
                (
                    relative,
                    "symlink",
                    mode,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                    os.readlink(current),
                )
            )
            continue
        if stat.S_ISDIR(mode):
            entries.append(
                (
                    relative,
                    "dir",
                    mode,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )
            )
            children = sorted(
                current.iterdir(), key=lambda child: child.name.casefold(), reverse=True
            )
            stack.extend(children)
            continue
        digest = ""
        if stat.S_ISREG(mode):
            hasher = hashlib.sha256()
            with current.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
        entries.append(
            (
                relative,
                "file",
                mode,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
                digest,
            )
        )
    return tuple(entries)


def _resolve_git_metadata_path(repo: Path, raw: str) -> Path:
    path = Path(raw.strip())
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


def _custom_executor_repository_state(
    config,
) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
    roots: dict[str, Path] = {}
    for candidate in (config.backend_root, config.mobile_root, config.frozen_worktree):
        if candidate is None:
            continue
        repo = Path(candidate).resolve()
        roots[str(repo).casefold()] = repo
        for args in (("git", "rev-parse", "--git-dir"), ("git", "rev-parse", "--git-common-dir")):
            result = _git_run(repo, *args)
            if result.returncode == 0 and result.stdout.strip():
                metadata = _resolve_git_metadata_path(repo, result.stdout)
                roots[str(metadata).casefold()] = metadata
    return tuple(
        (str(root), _tree_state(root))
        for _key, root in sorted(roots.items(), key=lambda item: item[0])
    )


def _validate_custom_executor_runtime(config) -> None:
    temp_root = Path(tempfile.gettempdir()).resolve()
    for candidate in (
        config.coordination_root,
        config.backend_root,
        config.mobile_root,
        config.frozen_worktree,
    ):
        if candidate is None:
            continue
        try:
            Path(candidate).resolve().relative_to(temp_root)
        except ValueError as exc:
            raise ValueError("custom agent executors require a temporary test runtime") from exc


class CliAgentInvoker:
    def __init__(self, config, *, execute=None, allow_test_executor: bool = False) -> None:
        self.config = config
        if execute is None:
            self.execute = run_command
            self._production_executor = True
        else:
            if not allow_test_executor:
                raise ValueError("custom agent executors are test-only")
            _validate_custom_executor_runtime(config)
            self.execute = execute
            self._production_executor = False
        if config.backend_root and config.mobile_root and config.frozen_worktree:
            validate_runtime_paths(
                config.coordination_root,
                config.backend_root,
                config.mobile_root,
                config.frozen_worktree,
            )

    def _execute_agent(
        self,
        command: list[str],
        repo: Path,
        role: str,
        provider: str,
        invocation_id: str,
        *,
        phase: str = "",
        task_id: str = "",
    ) -> ProcessResult:
        with ControlPlaneLock(role_lease_lock_path(self.config, role)):
            validate_handoff(self.config, role)
            command = [
                *command[:-1],
                command[-1] + f"\nAskRex-Orchestrator-Invocation-ID: {invocation_id}",
            ]
            pre_head, pre_dirty = repo_snapshot(repo)
            if pre_dirty:
                raise HandoffRequired("leased repository must be clean before agent invocation")
            activity_file = self.config.coordination_root / "active-agents" / f"{role}.json"
            metadata = {
                "role": role,
                "provider": provider,
                "invocation_id": invocation_id,
                "pre_head": pre_head,
            }
            parsed: AgentResult | None = None
            with _temporary_scratch_root(prefix=f"askrex-{role}-{provider}-") as scratch_scope:
                temp_dir, scratch_cleanup = scratch_scope
                scratch = Path(temp_dir) / "repo"
                _clone_scratch_repo(repo, pre_head, scratch)
                scratch_nonce = ""
                if self._production_executor:
                    scratch_nonce = _write_scratch_owner(
                        scratch, role=role, invocation_id=invocation_id, pre_head=pre_head
                    )
                scratch_metadata = {
                    **metadata,
                    "scratch_path": str(scratch.resolve()),
                    "scratch_nonce": scratch_nonce,
                }
                custom_state_before = None
                if not self._production_executor:
                    custom_state_before = _custom_executor_repository_state(self.config)

                def invoke_custom_executor(custom_command: list[str]) -> ProcessResult:
                    try:
                        with _custom_executor_process_scope():
                            return self.execute(custom_command, scratch)
                    finally:
                        if _custom_executor_repository_state(self.config) != custom_state_before:
                            raise HandoffRequired("custom executor modified the leased repository")

                if provider == "claude":
                    if self._production_executor:
                        ensure_claude_sandbox_image()
                        model = command[command.index("--model") + 1]
                        container_name = f"askrex-{role}-{invocation_id.replace('-', '')[:16]}"
                        run_cmd = build_claude_sandbox_command(
                            scratch, command[-1], model, container_name=container_name
                        )
                        result = self.execute(
                            run_cmd,
                            scratch,
                            activity_file=activity_file,
                            activity_metadata={
                                **scratch_metadata,
                                "container_name": container_name,
                                "_preserve_activity_on_success": True,
                            },
                        )
                    else:
                        result = invoke_custom_executor(command)
                    if result.returncode == 0:
                        try:
                            parsed = extract_agent_result(result.stdout)
                            validate_agent_updates(
                                self.config.coordination_root,
                                role,
                                parsed,
                                allow_issue_updates=False,
                                task_id=task_id,
                            )
                            if self._production_executor:
                                if (
                                    parsed.task_id != task_id
                                    or parsed.role != role
                                    or parsed.invocation_id != invocation_id
                                ):
                                    raise ValueError("implementation result binding mismatch")
                        except (ValueError, OSError) as exc:
                            if self._production_executor:
                                activity_file.unlink(missing_ok=True)
                            raise AgentInvocationError(
                                "claude", "invalid_output", str(exc)
                            ) from exc
                        if self._production_executor:
                            scratch_cleanup["remove"] = False
                        if self._production_executor and parsed.outcome in {
                            "continue",
                            "ready_for_review",
                        }:
                            scratch_head = _create_scratch_commit(scratch, pre_head, invocation_id)
                            _publish_scratch_commit(repo, scratch, pre_head, scratch_head)
                else:
                    scratch_command = [
                        str(scratch) if part == str(repo) else part for part in command
                    ]
                    if self._production_executor:
                        stdin_text = scratch_command[-1]
                        scratch_command = [*scratch_command[:-1], "-"]
                        result = self.execute(
                            scratch_command,
                            scratch,
                            activity_file=activity_file,
                            activity_metadata=scratch_metadata,
                            stdin_text=stdin_text,
                        )
                    else:
                        result = invoke_custom_executor(scratch_command)
                    if result.returncode == 0:
                        try:
                            parsed = extract_agent_result(result.stdout)
                            if self._production_executor:
                                binding_mismatch = (
                                    parsed.role != role
                                    or parsed.invocation_id != invocation_id
                                    or (
                                        phase == "review"
                                        and bool(task_id)
                                        and parsed.task_id != task_id
                                    )
                                )
                                if binding_mismatch:
                                    raise ValueError("Codex result binding mismatch")
                        except ValueError as exc:
                            raise AgentInvocationError(
                                provider, "invalid_output", str(exc)
                            ) from exc
                if not self._production_executor:
                    if _custom_executor_repository_state(self.config) != custom_state_before:
                        raise HandoffRequired("custom executor modified the leased repository")
                else:
                    post_head, post_dirty = repo_snapshot(repo)
                    if provider != "claude" and (post_head != pre_head or post_dirty):
                        raise HandoffRequired("non-Claude model modified the leased repository")
                    pending_result = None
                    if result.returncode == 0 and parsed is not None:
                        pending_result = {
                            "phase": phase,
                            "role": role,
                            "task_id": task_id,
                            "invocation_id": invocation_id,
                            "pre_head": pre_head,
                            "post_head": post_head,
                            "result": asdict(parsed),
                        }
                    advance_handoff(
                        self.config,
                        role,
                        pre_head=pre_head,
                        post_head=post_head,
                        invocation_id=invocation_id,
                        provider=provider,
                        pending_result=pending_result,
                    )
                    activity_file.unlink(missing_ok=True)
                    scratch_cleanup["remove"] = True
                    return result
            post_head, post_dirty = repo_snapshot(repo)
            if post_head != pre_head or post_dirty:
                raise HandoffRequired("custom executor modified the leased repository")
            return result

    def _repo(self, role: str) -> Path:
        root = self.config.backend_root if role == "backend" else self.config.mobile_root
        if root is None:
            raise ValueError(f"missing repository root for {role}")
        if self.config.frozen_worktree and root.resolve() == self.config.frozen_worktree.resolve():
            raise ValueError("frozen worktree cannot be used by an agent")
        return root

    def _finish(self, provider: str, result: ProcessResult) -> AgentResult:
        if result.returncode != 0:
            detail = "\n".join(part for part in (result.stderr, result.stdout) if part).strip()
            kind = classify_cli_failure(detail, result.returncode)
            raise AgentInvocationError(provider, kind, detail[:4000])
        try:
            return extract_agent_result(result.stdout)
        except ValueError as exc:
            raise AgentInvocationError(provider, "invalid_output", str(exc)) from exc

    def implement(self, role, state, task, context, model) -> AgentResult:
        repo = self._repo(role)
        invocation_id = str(uuid.uuid4())
        prompt = _task_prompt(role, task, self.config.coordination_root, context, invocation_id)
        command = build_claude_command(repo, prompt, model)
        return self._finish(
            "claude",
            self._execute_agent(
                command,
                repo,
                role,
                "claude",
                invocation_id,
                phase="implement",
                task_id=task.task_id,
            ),
        )

    def review(self, role, state, task, context, model) -> AgentResult:
        repo = self._repo(role)
        invocation_id = str(uuid.uuid4())
        command = build_codex_command(
            "review",
            repo,
            _review_prompt(role, task, self.config.coordination_root, context, invocation_id),
            model,
        )
        return self._finish(
            "codex",
            self._execute_agent(
                command,
                repo,
                role,
                "codex",
                invocation_id,
                phase="review",
                task_id=task.task_id,
            ),
        )

    def lead(self, role, state, context, task=None) -> AgentResult:
        from .routing import ASTRA_MODEL

        repo = self._repo(role)
        invocation_id = str(uuid.uuid4())
        command = build_codex_command(
            "lead",
            repo,
            _lead_prompt(role, self.config.coordination_root, context, invocation_id),
            ASTRA_MODEL,
        )
        return self._finish(
            "codex",
            self._execute_agent(
                command,
                repo,
                role,
                "codex",
                invocation_id,
                phase="adjudicate" if task is not None else "plan",
                task_id=task.task_id if task is not None else "",
            ),
        )
