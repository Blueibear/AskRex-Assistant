"""Host-owned publication of an independently reviewed draft checkpoint.

Push and create have separate fresh pause/policy admissions. An admitted action
may drain after pause; its journal never authorizes a later mutation. Unknown
outcomes allow reads only. No merge, ready, or acceptance operation is exposed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from .completed_tasks import CompletedTaskLedger
from .coordination_checks import read_check_summary
from .handoff import role_lease_lock_path, validate_handoff, validate_worker_checkout
from .lifecycle import ControlPlaneLock, _process_started_filetime
from .model_blockers import read_supplied_input
from .paths import validate_runtime_paths
from .storage import AtomicJsonStore
from .types import OrchestratorConfig, TaskItem
from .validation import load_validation_receipt

_REPOSITORIES = {"backend": "Blueibear/AskRex-Assistant", "mobile": "Blueibear/AskRex"}
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_TASK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z")
_FIELDS = "number,url,state,isDraft,headRefName,headRefOid,baseRefName,isCrossRepository,headRepository,headRepositoryOwner"
Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class PublicationResult:
    status: str
    reason: str = ""
    head: str = ""
    task_id: str = ""
    branch: str = ""
    url: str = ""
    journal_path: str = ""


class _Stop(RuntimeError):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("publication evidence timestamp is missing")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("publication evidence timestamp is not timezone-aware")
    return parsed


def _trusted(root: Path, path: Path) -> Path:
    root, path = root.resolve(), path.absolute()
    if path != root and root not in path.parents:
        raise ValueError("publication path escapes coordination root")
    if path.resolve() != path:
        raise ValueError("publication path traverses a redirected namespace")
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if (
                item.is_symlink()
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError("publication path is a reparse point")
        if item == root:
            break
    return path


def _read(root: Path, path: Path) -> dict:
    _trusted(root, path)
    if path.stat().st_size > 2_000_000:
        raise ValueError("publication evidence exceeds its bound")
    value = AtomicJsonStore(path).read()
    if not isinstance(value, dict):
        raise ValueError("publication evidence must be an object")
    return value


def _write(root: Path, path: Path, value: dict) -> None:
    _trusted(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _trusted(root, path)
    if len(json.dumps(value).encode("utf-8")) > 2_000_000:
        raise ValueError("publication journal exceeds its bound")
    AtomicJsonStore(path).write(value)


def _active(config: OrchestratorConfig, role: str) -> dict:
    raw = _read(config.coordination_root, config.coordination_root / "orchestrator-config.json")
    metadata = raw.get("metadata")
    policy = metadata.get("host_publication") if isinstance(metadata, dict) else None
    if raw.get("observe_only") is not False:
        raise _Stop("waiting", "Host publication is paused.")
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        raise _Stop("waiting", "Host draft publication is disabled.")
    expected = config.backend_root if role == "backend" else config.mobile_root
    if (
        expected is None
        or not raw.get(f"{role}_root")
        or Path(raw[f"{role}_root"]).resolve() != expected.resolve()
    ):
        raise ValueError("publication repository configuration changed")
    heartbeat = _read(
        config.coordination_root, config.coordination_root / "supervisor-heartbeat.json"
    )
    age = (datetime.now(UTC) - _stamp(heartbeat.get("timestamp"))).total_seconds()
    if (
        type(heartbeat.get("pid")) is not int
        or heartbeat["pid"] != os.getpid()
        or type(heartbeat.get("process_started_filetime")) is not int
        or heartbeat["process_started_filetime"] != _process_started_filetime()
        or not 0 <= age <= 120
    ):
        raise ValueError("publication caller is not the fresh active supervisor")
    state = _read(config.coordination_root, config.coordination_root / "state" / f"{role}.json")
    if state.get("status") == "blocked_user" and state.get("blocker_kind") in {
        "github_auth",
        "auth",
        "human",
    }:
        raise _Stop(
            "auth_required", "Publication is held for the documented explicit human resume."
        )
    return heartbeat


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise ValueError("cannot verify local publication Git metadata")
    return result.stdout.strip()


def _url_matches(value: str, repository: str) -> bool:
    if value.startswith("git@github.com:"):
        path = value[len("git@github.com:") :]
    else:
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"https", "ssh"}
            or parsed.hostname != "github.com"
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
            or parsed.password is not None
            or parsed.username != ("git" if parsed.scheme == "ssh" else None)
        ):
            return False
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path.casefold() == repository.casefold()


def _proof(config: OrchestratorConfig, role: str) -> tuple[Path, dict, list[str]]:
    root = config.coordination_root.resolve()
    validate_runtime_paths(
        config.coordination_root, config.backend_root, config.mobile_root, config.frozen_worktree
    )
    validate_handoff(config, role)
    checkout = validate_worker_checkout(config, role)
    repository = _REPOSITORIES[role]
    urls = {}
    for name, options in (("fetch_url", ()), ("push_url", ("--push",))):
        values = _git(checkout.root, "remote", "get-url", *options, "--all", "origin").splitlines()
        if len(values) != 1 or not _url_matches(values[0], repository):
            raise ValueError(
                "publication requires exactly one allowlisted effective fetch and push URL"
            )
        urls[name] = values[0]
    if (
        checkout.branch.startswith("-")
        or checkout.branch.casefold() in {"main", "master"}
        or checkout.branch == checkout.base_branch
    ):
        raise ValueError("publication requires the leased non-base branch")
    lease = _read(root, root / "handoff" / f"{role}.json")
    if lease.get("pending_result") is not None:
        raise ValueError("publication cannot consume an unhandled model result")
    for directory in ("active-agents", "cloud-sessions"):
        if (root / directory / f"{role}.json").exists():
            raise ValueError("publication has unresolved worker activity")
    ledger_path = _trusted(root, root / "completed-tasks" / f"{role}.json")
    if ledger_path.stat().st_size > 2_000_000:
        raise ValueError("completed task ledger exceeds its bound")
    records = CompletedTaskLedger(root).records(role)
    matches = [
        r
        for r in records
        if r.get("completed_head") == checkout.head and r.get("source") == "independent_review"
    ]
    if not matches:
        raise ValueError("publication requires an accepted independent review at current HEAD")
    record = max(matches, key=lambda r: _stamp(r.get("completed_at")))
    task_id, invocation = record.get("task_id"), record.get("invocation_id")
    if not isinstance(task_id, str) or not _TASK.fullmatch(task_id):
        raise ValueError("publication reviewed task identity is invalid")
    if str(uuid.UUID(str(invocation))) != invocation:
        raise ValueError("publication review invocation is invalid")
    digest = _sha(invocation.encode("utf-8"))
    input_path = root / "model-inputs" / f"{role}-{digest}" / "manifest.json"
    supplied = _read(root, input_path)
    admission = supplied.get("admission")
    if not isinstance(admission, dict) or admission.get("head") != checkout.head:
        raise ValueError("accepted review did not receive this exact HEAD")
    supplied, _ = read_supplied_input(
        config,
        role,
        "review",
        task_id,
        invocation,
        {**admission, "supplied_input": supplied.get("supplied_input")},
    )
    receipt_path = root / "validation" / f"{role}-{task_id}.json"
    payload = _read(root, receipt_path)
    base = payload.get("base_head")
    if not isinstance(base, str) or not _SHA.fullmatch(base):
        raise ValueError("publication validation base is invalid")
    receipt = load_validation_receipt(config, role, task_id, base_head=base, head=checkout.head)
    if not receipt.gates or any(not gate.command for gate in receipt.gates):
        raise ValueError("publication requires actual successful canonical gates")
    checks_path = root / "evidence/supervisor-checks" / f"{role}-{task_id}.json"
    checks = _read(root, checks_path)
    if not isinstance(checks.get("initial"), dict) or not isinstance(checks.get("final"), dict):
        raise ValueError("publication requires complete opening and closing evidence")
    read_check_summary(config, role, TaskItem(task_id, ""), base, checkout.head, strict=True)
    script_hash = _sha(_trusted(root, root / "scripts/check-coordination.ps1").read_bytes())
    for capture in (checks["initial"], checks["final"]):
        if capture.get("script_sha256") != script_hash:
            raise ValueError("publication coordination script changed since validation")
        if capture.get("lease", {}).get("lease_id") != lease["lease_id"]:
            raise ValueError("publication review belongs to a different lease")
    if not (
        _stamp(checks["initial"]["observed_at"])
        <= _stamp(receipt.passed_at)
        <= _stamp(checks["final"]["observed_at"])
        <= _stamp(supplied["captured_at"])
        <= _stamp(record["completed_at"])
        <= datetime.now(UTC)
    ):
        raise ValueError("publication review/validation timing is inconsistent")
    proof = {
        "role": role,
        "repository": repository,
        "branch": checkout.branch,
        "base_branch": checkout.base_branch,
        "head": checkout.head,
        "task_id": task_id,
        "base_head": base,
        "lease_id": lease["lease_id"],
        "worktree": str(checkout.root),
        "review": record,
        "review_input_sha256": _sha(input_path.read_bytes()),
        "validation_sha256": _sha(receipt_path.read_bytes()),
        "coordination_checks_sha256": _sha(checks_path.read_bytes()),
        **urls,
    }
    gates = [f"{gate.name}: exit 0" for gate in receipt.gates]
    return checkout.root, proof, gates


def _run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(
        os.environ,
        GH_HOST="github.com",
        GH_PROMPT_DISABLED="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_PAGER="cat",
        GH_PAGER="cat",
    )
    env.pop("GH_REPO", None)
    for name in tuple(env):
        if name.startswith("GIT_TRACE") or name in {"GH_DEBUG", "GIT_CURL_VERBOSE"}:
            env.pop(name, None)
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
        env=env,
    )


def _diagnostic(result: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    text = ((result.stdout or "") + "\n" + (result.stderr or "")).replace("\r\n", "\n")
    plain = text.casefold().replace(chr(96), "").replace("'", "").replace('"', "")
    if result.returncode == 0:
        category = "success"
    elif result.returncode is None:
        category = "unknown_outcome"
    elif "without workflow scope" in plain:
        category = "workflow_scope_denied"
    elif result.returncode == 4 or any(
        marker in plain
        for marker in (
            "authentication failed",
            "authentication required",
            "http 401",
            "gh auth login",
            "permission denied (publickey)",
        )
    ):
        category = "authentication_required"
    elif "non-fast-forward" in plain or "[rejected]" in plain and "fetch first" in plain:
        category = "non_fast_forward"
    elif "repository rule" in plain or "[remote rejected]" in plain:
        category = "remote_rejected"
    else:
        category = "operational_error"
    # Credential formats and credential-labelled lines are not persisted.
    text = re.sub(r"(?i)(https?://)[^\s/@]+(?::[^\s/@]+)?@", r"\1[REDACTED]@", text)
    text = re.sub(
        r"(?i)(api[_-]?key|token|authorization|password|credential)\s*[:=][^\r\n]*",
        "[REDACTED CREDENTIAL LINE]",
        text,
    )
    text = re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{8,})\b",
        "[REDACTED]",
        text,
    )
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]*", r"\1?[REDACTED]", text)
    if len(text) > 1500:
        text = text[:650] + "\n[bounded diagnostic]\n" + text[-800:]
    return category, text.strip()


def _denied_push(result: subprocess.CompletedProcess[str], command: list[str]) -> bool:
    # Only a complete, single-ref porcelain rejection proves no branch update.
    # Authentication-looking text alone, fatal errors and timeouts are unknown.
    if (
        result.returncode != 1
        or _diagnostic(result)[0] != "workflow_scope_denied"
        or len(result.stdout or "") + len(result.stderr or "") > 64_000
    ):
        return False
    rows = []
    for line in (result.stdout or "").splitlines():
        if line.startswith("!\t"):
            rows.append(line.split("\t"))
        elif line and not line.startswith("To ") and line != "Done":
            return False
    return (
        len(rows) == 1
        and len(rows[0]) == 3
        and rows[0][1] == command[-1]
        and rows[0][2].startswith("[remote rejected] (")
        and rows[0][2].endswith(")")
        and "fatal:" not in (result.stderr or "").casefold()
    )


def _outcome(result: subprocess.CompletedProcess[str], command: list[str]) -> dict:
    stdout, stderr = result.stdout or "", result.stderr or ""
    category, diagnostic = _diagnostic(result)
    return {
        "exit_code": result.returncode,
        "stream_encoding": "utf-8 decoded with replacement",
        "stdout_sha256": _sha(stdout.encode("utf-8")),
        "stderr_sha256": _sha(stderr.encode("utf-8")),
        "stdout_chars": len(stdout),
        "stderr_chars": len(stderr),
        "category": category,
        "diagnostic": diagnostic,
        "definitively_denied": _denied_push(result, command),
        "observed_at": datetime.now(UTC).isoformat(),
    }


def _error(result: subprocess.CompletedProcess[str]) -> _Stop:
    category, diagnostic = _diagnostic(result)
    auth = category in {"authentication_required", "workflow_scope_denied"}
    return _Stop(
        "auth_required" if auth else "system_error",
        f"Publication {category} (exit {result.returncode}): {diagnostic}",
    )


def _query(runner: Runner, args: list[str], repo: Path) -> str:
    result = runner(args, cwd=repo)
    if result.returncode != 0:
        raise _error(result)
    if len(result.stdout) > 1_000_000:
        raise ValueError("publication query exceeds its response bound")
    return result.stdout


def _prs(runner: Runner, repo: Path, proof: dict) -> list[dict]:
    raw = _query(
        runner,
        [
            "gh",
            "pr",
            "list",
            "--repo",
            "github.com/" + proof["repository"],
            "--head",
            proof["branch"],
            "--state",
            "all",
            "--limit",
            "100",
            "--json",
            _FIELDS,
        ],
        repo,
    )
    values = json.loads(raw)
    if not isinstance(values, list) or len(values) >= 100:
        raise ValueError("PR query is malformed or incomplete")
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("PR query item is malformed")
        number = item.get("number")
        expected_url = f'https://github.com/{proof["repository"]}/pull/{number}'
        owner, name = proof["repository"].split("/")
        if (
            type(number) is not int
            or number <= 0
            or item.get("url") != expected_url
            or item.get("isCrossRepository") is not False
            or item.get("headRefName") != proof["branch"]
            or item.get("baseRefName") != proof["base_branch"]
            or item.get("state") not in {"OPEN", "CLOSED", "MERGED"}
            or type(item.get("isDraft")) is not bool
            or not _SHA.fullmatch(str(item.get("headRefOid", "")))
            or not isinstance(item.get("headRepository"), dict)
            or not isinstance(item.get("headRepositoryOwner"), dict)
            or str(item["headRepository"].get("name", "")).casefold() != name.casefold()
            or str(item["headRepositoryOwner"].get("login", "")).casefold() != owner.casefold()
        ):
            raise ValueError("PR identity does not match the authorized repository and branch")
    if len([p for p in values if p["state"] == "OPEN"]) > 1:
        raise ValueError("multiple open PRs require explicit reconciliation")
    return values


def _remote_head(runner: Runner, repo: Path, proof: dict) -> str:
    raw = _query(
        runner, ["git", "ls-remote", "--heads", "origin", "refs/heads/" + proof["branch"]], repo
    ).strip()
    if not raw:
        return ""
    fields = raw.split()
    if (
        len(fields) != 2
        or not _SHA.fullmatch(fields[0])
        or fields[1] != "refs/heads/" + proof["branch"]
    ):
        raise ValueError("remote branch query is ambiguous")
    return fields[0]


def _retryable_denial(action: object) -> bool:
    if not isinstance(action, dict):
        return False
    outcome = action.get("outcome")
    return (
        isinstance(outcome, dict)
        and outcome.get("exit_code") == 1
        and outcome.get("category") == "workflow_scope_denied"
        and outcome.get("definitively_denied") is True
    )


def _denial_resolved(action: dict, proof: dict) -> bool:
    resolution = action.get("resolution")
    if resolution is None:
        return False
    historical = resolution.get("proof") if isinstance(resolution, dict) else None
    heartbeat = resolution.get("supervisor_heartbeat") if isinstance(resolution, dict) else None
    if (
        not isinstance(resolution, dict)
        or resolution.get("kind") != "explicit_resume_remote_confirmation"
        or resolution.get("denied_admission_id") != action.get("admission_id")
        or not isinstance(historical, dict)
        or any(historical.get(k) != proof[k] for k in ("role", "repository", "branch"))
        or not _SHA.fullmatch(str(historical.get("head", "")))
        or resolution.get("remote_head") != historical["head"]
        or not isinstance(heartbeat, dict)
        or type(resolution.get("pid")) is not int
        or type(resolution.get("process_started_filetime")) is not int
        or heartbeat.get("pid") != resolution["pid"]
        or heartbeat.get("process_started_filetime") != resolution["process_started_filetime"]
    ):
        raise ValueError("denied push resolution is malformed")
    observed = _stamp(resolution.get("remote_observed_at"))
    resolved = _stamp(resolution.get("resolved_at"))
    if (
        not _stamp(action["outcome"].get("observed_at"))
        <= observed
        <= resolved
        <= datetime.now(UTC)
        or not 0 <= (resolved - _stamp(heartbeat.get("timestamp"))).total_seconds() <= 120
    ):
        raise ValueError("denied push resolution timing is invalid")
    return True


def _check_consumed_resume(config: OrchestratorConfig, role: str, proof: dict) -> None:
    caller = _read(config.coordination_root, config.coordination_root / "state" / f"{role}.json")
    if (
        caller.get("status") != "blocked_system"
        or caller.get("blocker_kind") != "host_publication"
        or caller.get("completion_host_action") != "publication"
        or caller.get("task_base_head") != proof["head"]
        or (caller.get("task") or {}).get("task_id") != f"FINAL-VERIFY-{role}"
        or caller.get("resume_status") is not None
    ):
        raise ValueError("publication auth resume was not durably consumed")


def _check_body(root: Path, journal: dict) -> None:
    body, proof = journal.get("body"), journal.get("proof")
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("path"), str)
        or not isinstance(proof, dict)
        or proof.get("role") not in _REPOSITORIES
        or proof.get("repository") != _REPOSITORIES[proof["role"]]
        or not isinstance(proof.get("branch"), str)
        or not _SHA.fullmatch(str(proof.get("head", "")))
    ):
        raise ValueError("draft body descriptor is missing or malformed")
    review = proof.get("review")
    if not isinstance(review, dict):
        raise ValueError("draft body review is malformed")
    invocation = review.get("invocation_id")
    if str(uuid.UUID(str(invocation))) != invocation:
        raise ValueError("draft body review identity is invalid")
    digest = _sha((proof["repository"] + "\n" + proof["branch"]).encode("utf-8"))
    expected = (
        root.resolve()
        / "github-publication"
        / f"{proof['role']}-{digest}"
        / (proof["head"] + "-" + invocation + "-" + str(body.get("sha256", ""))[:16] + "-body.md")
    )
    path = _trusted(root, Path(body["path"]))
    if (
        path != expected
        or path.stat().st_size > 20_000
        or _sha(path.read_bytes()) != body.get("sha256")
    ):
        raise ValueError("immutable draft body integrity mismatch")


def _admit(
    config: OrchestratorConfig,
    role: str,
    proof: dict,
    path: Path,
    journal: dict,
    action: str,
    args: list[str],
    *,
    resumed_denial_id: str = "",
) -> None:
    _, current, _ = _proof(config, role)
    if current != proof:
        raise ValueError("publication proof changed before action admission")
    with ControlPlaneLock(config.coordination_root / "control-plane.lock", timeout_seconds=10):
        heartbeat = _active(config, role)
        if resumed_denial_id:
            _check_consumed_resume(config, role, proof)
        if journal.get(action) is not None:
            if action != "push" or not resumed_denial_id or not _retryable_denial(journal[action]):
                raise ValueError("publication action was already admitted")
            history = journal.setdefault("push_history", [])
            if not isinstance(history, list):
                raise ValueError("publication prior attempts are malformed")
            history.append(journal[action])
        journal[action] = {
            "admission_id": str(uuid.uuid4()),
            "admitted_at": datetime.now(UTC).isoformat(),
            "pid": os.getpid(),
            "process_started_filetime": _process_started_filetime(),
            "command": args,
            "head": proof["head"],
            "lease_id": proof["lease_id"],
            "review_invocation_id": proof["review"]["invocation_id"],
            "proof": proof,
            "supervisor_heartbeat": heartbeat,
            "resumed_denial_admission_id": resumed_denial_id,
            "outcome": None,
        }
        _write(config.coordination_root, path, journal)
        if action == "push":
            # Written before execution, so a crash can never hide a dispatched
            # push behind a later HEAD's separate checkpoint journal.
            _write(
                config.coordination_root,
                path.parent / "latest-push.json",
                {
                    "schema_version": 1,
                    "head": proof["head"],
                    "admission_id": journal[action]["admission_id"],
                },
            )


def _act(
    config: OrchestratorConfig, path: Path, journal: dict, action: str, runner: Runner, repo: Path
) -> subprocess.CompletedProcess[str]:
    args = journal[action]["command"]
    if action == "create":
        _check_body(config.coordination_root, journal)
    try:
        result = runner(args, cwd=repo)
    except (OSError, subprocess.TimeoutExpired) as exc:

        def decoded(value):
            return (
                value.decode("utf-8", errors="replace")
                if isinstance(value, bytes)
                else (value or "")
            )

        partial = subprocess.CompletedProcess(
            args, None, decoded(getattr(exc, "stdout", "")), decoded(getattr(exc, "stderr", ""))
        )
        observed = _outcome(partial, args)
        observed.update(
            unknown=True,
            error_type=type(exc).__name__,
            diagnostic=observed["diagnostic"] or "No complete response; outcome is unknown.",
        )
        journal[action]["outcome"] = observed
        _write(config.coordination_root, path, journal)
        raise _Stop(
            "waiting", f"{action} outcome is uncertain; only read-only reconciliation is permitted."
        ) from exc
    journal[action]["outcome"] = _outcome(result, args)
    _write(config.coordination_root, path, journal)
    if result.returncode != 0:
        raise _error(result)
    return result


def _body(proof: dict, gates: list[str], unresolved: tuple[str, ...]) -> str:
    if len(unresolved) > 30 or any(not isinstance(s, str) or len(s) > 2000 for s in unresolved):
        raise ValueError("unresolved acceptance description exceeds its bound")
    reasons = list(unresolved) or [
        "External acceptance and repository CI must be verified separately."
    ]
    return (
        "## Reviewed checkpoint at draft creation\n\n"
        f"Task: {proof['task_id']}\n\nCommit: {proof['head']}\n\n"
        f"Independent review: {proof['review']['invocation_id']} at {proof['review']['completed_at']}\n\n"
        f"Canonical validation receipt SHA-256: {proof['validation_sha256']}\n\n"
        "Host validation at that commit:\n\n"
        + "\n".join("- " + g for g in gates)
        + "\n\n## Unresolved acceptance\n\n"
        + "\n".join("- " + s for s in reasons)
        + "\n\nNo merge, release, or physical acceptance is claimed. Created as a draft; no readiness transition is requested."
        " Later branch updates require their own reviewed checkpoints; this section records creation evidence.\n"
    )


def _publish(
    config: OrchestratorConfig,
    role: str,
    unresolved: tuple[str, ...],
    runner: Runner,
    info: dict,
    resume_denied: bool,
) -> PublicationResult:
    root = config.coordination_root.resolve()
    with ControlPlaneLock(root / "control-plane.lock", timeout_seconds=10):
        _active(config, role)
    repo, proof, gates = _proof(config, role)
    body = _body(proof, gates, unresolved)
    if len(body) > 20_000:
        raise ValueError("draft body exceeds its complete evidence bound")
    info.update(head=proof["head"], task_id=proof["task_id"], branch=proof["branch"])
    digest = _sha((proof["repository"] + "\n" + proof["branch"]).encode("utf-8"))
    directory = root / "github-publication" / f"{role}-{digest}"
    _trusted(root, directory)
    path = directory / (proof["head"] + ".json")
    create_path = directory / "create.json"
    info["journal_path"] = str(path)
    state = (
        _read(root, path)
        if path.exists()
        else {
            "schema_version": 1,
            "kind": "reviewed_draft_publication",
            "proof": proof,
            "push": None,
            "observation": None,
        }
    )
    if (
        state.get("schema_version") != 1
        or state.get("kind") != "reviewed_draft_publication"
        or not isinstance(state.get("proof"), dict)
        or any(state["proof"].get(k) != proof[k] for k in ("role", "repository", "branch", "head"))
    ):
        raise ValueError("publication checkpoint journal identity mismatch")
    create = _read(root, create_path) if create_path.exists() else None
    if create is not None and (
        create.get("schema_version") != 1
        or create.get("kind") != "draft_creation"
        or create.get("repository") != proof["repository"]
        or create.get("branch") != proof["branch"]
        or not isinstance(create.get("create"), dict)
    ):
        raise ValueError("draft creation journal identity mismatch")
    if create is not None:
        _check_body(root, create)
    prs = _prs(runner, repo, proof)
    opened = next((p for p in prs if p["state"] == "OPEN"), None)
    remote_head = _remote_head(runner, repo, proof)
    remote_observed_at = datetime.now(UTC).isoformat()
    common = dict(info)
    resumed_denial_id = ""
    latest_path = directory / "latest-push.json"
    if latest_path.exists():
        latest = _read(root, latest_path)
        old_head = latest.get("head")
        if (
            latest.get("schema_version") != 1
            or not isinstance(old_head, str)
            or not _SHA.fullmatch(old_head)
        ):
            raise ValueError("prior push pointer is malformed")
        previous_path = directory / (old_head + ".json")
        previous = state if old_head == proof["head"] else _read(root, previous_path)
        previous_proof = previous.get("proof")
        history = previous.get("push_history", [])
        if not isinstance(history, list):
            raise ValueError("prior push history is malformed")
        attempts = [previous.get("push"), *history]
        push = next(
            (
                a
                for a in attempts
                if isinstance(a, dict) and a.get("admission_id") == latest.get("admission_id")
            ),
            None,
        )
        if (
            not isinstance(previous_proof, dict)
            or not isinstance(push, dict)
            or previous_proof.get("head") != old_head
            or previous_proof.get("repository") != proof["repository"]
            or previous_proof.get("branch") != proof["branch"]
            or push.get("admission_id") != latest.get("admission_id")
        ):
            raise ValueError("prior push journal differs from its pointer")
        denied = _retryable_denial(push)
        if denied and not _denial_resolved(push, proof):
            if not resume_denied:
                return PublicationResult(
                    "auth_required",
                    "Prior workflow-scope denial requires the documented explicit human resume.",
                    **common,
                )
            if remote_head == proof["head"]:
                # An authorized external push may already have resolved the
                # missing ref. Preserve the denied outcome and record only the
                # explicit resume plus this confirmed remote observation.
                _, current_proof, _ = _proof(config, role)
                if current_proof != proof:
                    raise ValueError("publication proof changed before denial reconciliation")
                with ControlPlaneLock(root / "control-plane.lock", timeout_seconds=10):
                    heartbeat = _active(config, role)
                    _check_consumed_resume(config, role, proof)
                    push["resolution"] = {
                        "kind": "explicit_resume_remote_confirmation",
                        "denied_admission_id": push["admission_id"],
                        "resolved_at": datetime.now(UTC).isoformat(),
                        "pid": os.getpid(),
                        "process_started_filetime": _process_started_filetime(),
                        "proof": proof,
                        "supervisor_heartbeat": heartbeat,
                        "remote_head": remote_head,
                        "remote_observed_at": remote_observed_at,
                    }
                    _write(root, previous_path, previous)
            else:
                resumed_denial_id = push["admission_id"]
        if (
            old_head != proof["head"]
            and remote_head not in {old_head, proof["head"]}
            and not denied
        ):
            return PublicationResult(
                "waiting",
                "Prior-head push is unresolved; only reconciliation is permitted.",
                **common,
            )
    if not opened and (prs or create is not None):
        return PublicationResult(
            "waiting", "Prior draft creation or closed PR needs read-only reconciliation.", **common
        )
    if remote_head != proof["head"]:
        if state.get("push") is not None and (
            not _retryable_denial(state["push"]) or not resumed_denial_id
        ):
            return PublicationResult(
                "waiting", "Prior push is not confirmed at this HEAD; no repeat mutation.", **common
            )
        command = [
            "git",
            "push",
            "--porcelain",
            "--no-force",
            "--no-mirror",
            "--no-follow-tags",
            "--recurse-submodules=no",
            "origin",
            proof["head"] + ":refs/heads/" + proof["branch"],
        ]
        _admit(
            config, role, proof, path, state, "push", command, resumed_denial_id=resumed_denial_id
        )
        _act(config, path, state, "push", runner, repo)
        remote_head = _remote_head(runner, repo, proof)
        remote_observed_at = datetime.now(UTC).isoformat()
        if remote_head != proof["head"]:
            return PublicationResult(
                "waiting", "Push returned but the exact remote HEAD is not yet confirmed.", **common
            )
    if opened:
        prs = _prs(runner, repo, proof)
        current = next((p for p in prs if p["state"] == "OPEN"), None)
        if not current or current["headRefOid"] != proof["head"]:
            return PublicationResult(
                "waiting", "Existing PR has not reached the reviewed HEAD.", **common
            )
        state["observation"] = {
            "observed_at": datetime.now(UTC).isoformat(),
            "pr": current,
            "remote_head": remote_head,
            "remote_observed_at": remote_observed_at,
        }
        _write(root, path, state)
        return PublicationResult(
            "already_published",
            "Matching open PR observed; draft/readiness unchanged.",
            url=current["url"],
            **common,
        )
    if _prs(runner, repo, proof):
        return PublicationResult(
            "waiting", "A PR appeared before creation; reconcile on the next read.", **common
        )
    body_path = directory / (
        proof["head"]
        + "-"
        + proof["review"]["invocation_id"]
        + "-"
        + _sha(body.encode("utf-8"))[:16]
        + "-body.md"
    )
    _trusted(root, body_path)
    directory.mkdir(parents=True, exist_ok=True)
    _trusted(root, body_path)
    data = body.encode("utf-8")
    if body_path.exists():
        if body_path.read_bytes() != data:
            raise ValueError("immutable draft body changed")
    else:
        with body_path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    create = {
        "schema_version": 1,
        "kind": "draft_creation",
        "repository": proof["repository"],
        "branch": proof["branch"],
        "proof": proof,
        "body": {"path": str(body_path), "sha256": _sha(data)},
        "create": None,
    }
    args = [
        "gh",
        "pr",
        "create",
        "--repo",
        "github.com/" + proof["repository"],
        "--base",
        proof["base_branch"],
        "--head",
        proof["branch"],
        "--draft",
        "--title",
        f"Draft: reviewed {role} checkpoint {proof['head'][:12]}",
        "--body-file",
        str(body_path),
    ]
    _admit(config, role, proof, create_path, create, "create", args)
    _act(config, create_path, create, "create", runner, repo)
    prs = _prs(runner, repo, proof)
    current = next((p for p in prs if p["state"] == "OPEN"), None)
    if not current or current["headRefOid"] != proof["head"] or current["isDraft"] is not True:
        return PublicationResult(
            "waiting", "Draft creation is not yet confirmed; no repeat create.", **common
        )
    state["observation"] = {
        "observed_at": datetime.now(UTC).isoformat(),
        "pr": current,
        "remote_head": remote_head,
        "remote_observed_at": remote_observed_at,
    }
    _write(root, path, state)
    return PublicationResult(
        "published",
        "Reviewed checkpoint and draft PR confirmed by read-only queries.",
        url=current["url"],
        **common,
    )


def publish_reviewed_draft(
    config: OrchestratorConfig,
    role: str,
    *,
    unresolved_acceptance: tuple[str, ...] = (),
    runner: Runner | None = None,
    resume_denied: bool = False,
) -> PublicationResult:
    """Publish only a normal accepted checkpoint; never change task/acceptance state."""
    if type(resume_denied) is not bool:
        return PublicationResult("system_error", "Invalid publication resume request.")
    if role not in _REPOSITORIES:
        return PublicationResult("system_error", "Unsupported publication role.")
    info = {}
    try:
        with ControlPlaneLock(role_lease_lock_path(config, role)):
            return _publish(
                config, role, unresolved_acceptance, runner or _run, info, resume_denied
            )
    except _Stop as exc:
        return PublicationResult(exc.status, str(exc), **info)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return PublicationResult("system_error", f"Host publication blocked: {exc}", **info)
