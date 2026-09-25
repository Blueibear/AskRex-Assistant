from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .types import OrchestratorConfig, TaskItem, WorkerState
from .validation import ValidationReceipt, load_validation_receipt


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewEvidenceBundle:
    text: str
    truncated: bool
    truncation_reasons: tuple[str, ...]
    base_head: str
    head: str


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|authorization|password)\s*[:=]\s*[^\s]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)

_DEFAULT_SECTION_LIMITS = {
    "identity": 2_000,
    "task": 8_000,
    "task_issues": 12_000,
    "coordination": 6_000,
    "outgoing_coordination": 12_000,
    "diff_stat": 4_000,
    "task_diff": 70_000,
    "changed_file_contents": 35_000,
    "validated_artifacts": 45_000,
    "validation": 12_000,
}
_REVIEW_CRITICAL_SECTIONS = frozenset(
    {
        "identity",
        "task",
        "task_issues",
        "task_diff",
        "validated_artifacts",
        "validation",
    }
)


def _repo_root(config: OrchestratorConfig, role: str) -> Path:
    root = config.backend_root if role == "backend" else config.mobile_root
    if root is None:
        raise EvidenceError(f"missing repository root for {role}")
    return root.resolve()


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=repo,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise EvidenceError(f"read-only git evidence command failed: {' '.join(args)}") from exc


def _redact(text: str, config: OrchestratorConfig) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    if config.frozen_worktree:
        frozen = str(config.frozen_worktree.resolve())
        redacted = redacted.replace(frozen, "[FROZEN-WORKTREE]")
    return redacted


def _bound(name: str, text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0:
        raise EvidenceError("evidence section bound must be positive")
    if len(text) <= max_chars:
        return text, False
    marker = f"\n...[truncated {name}]...\n"
    remaining = max_chars - len(marker)
    if remaining <= 2:
        return marker[:max_chars], True
    head_chars = remaining // 2
    tail_chars = remaining - head_chars
    return text[:head_chars] + marker + text[-tail_chars:], True


def _message_field(text: str, field: str) -> str:
    prefix = field.casefold() + ":"
    for line in text.splitlines():
        if line.casefold().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def _task_coordination_anchors(task: TaskItem) -> tuple[str, ...]:
    anchors = {task.task_id.casefold()}
    task_text = f"{task.task_id}\n{task.prompt}\n{task.feedback}"
    for pattern in (
        r"(?i)\bS\d+\b",
        r"(?i)\bTEST-\d+\b",
        r"(?i)\bUS-\d+\b",
        r"(?i)\bSTORY-S\d+(?:-[A-Za-z0-9-]+)?\b",
        r"(?i)\bMSG-[A-Za-z0-9-]+",
    ):
        for match in re.finditer(pattern, task_text):
            anchors.add(match.group(0).casefold())
    return tuple(sorted(anchor for anchor in anchors if anchor))


def _outgoing_coordination(config: OrchestratorConfig, role: str, task: TaskItem) -> str:
    mailbox_root = config.coordination_root / "mailbox"
    if not mailbox_root.is_dir():
        return "No task-relevant outgoing coordination messages were recorded."

    anchors = _task_coordination_anchors(task)
    messages: list[tuple[int, Path, str]] = []
    try:
        recipients = [path for path in mailbox_root.iterdir() if path.is_dir()]
        for recipient in recipients:
            for path in recipient.glob("*.md"):
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                if _message_field(text, "From").casefold() != role.casefold():
                    continue
                related = _message_field(text, "Related").casefold()
                searchable = f"{related}\n{text.casefold()}"
                if not any(anchor in searchable for anchor in anchors):
                    continue
                messages.append((path.stat().st_mtime_ns, path, text))
    except OSError as exc:
        raise EvidenceError("cannot read task-relevant coordination evidence") from exc

    if not messages:
        return "No task-relevant outgoing coordination messages were recorded."

    messages.sort(key=lambda item: item[0], reverse=True)
    rendered: list[str] = []
    for _mtime_ns, path, text in messages[:8]:
        relative = path.relative_to(config.coordination_root).as_posix()
        rendered.append(f"### {relative}\n{text}")
    return "\n\n".join(rendered)


def _task_issue_contents(config: OrchestratorConfig, task: TaskItem) -> str:
    issues_root = config.coordination_root / "issues"
    if not issues_root.is_dir():
        return "No task-matching canonical issue files were found."

    anchors = _task_coordination_anchors(task)
    rendered: list[str] = []
    try:
        for path in sorted(issues_root.glob("*.md")):
            stem = path.stem.casefold()
            if stem not in anchors:
                continue
            body = path.read_text(encoding="utf-8-sig", errors="replace")
            relative = path.relative_to(config.coordination_root).as_posix()
            rendered.append(f"### {relative}\n{body}")
    except OSError as exc:
        raise EvidenceError("cannot read task-matching canonical issue evidence") from exc

    return "\n\n".join(rendered) or "No task-matching canonical issue files were found."


def _changed_file_contents(
    repo: Path,
    base_head: str,
    head: str,
    task_diff: str,
) -> str:
    """Return current text for changed files when the incremental diff is small."""

    if len(task_diff) > 20_000:
        return (
            "Current changed-file contents omitted because the task diff is already "
            "large enough to provide substantial review context."
        )

    names = [
        line.strip()
        for line in _git(
            repo,
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            f"{base_head}..{head}",
            "--",
        ).splitlines()
        if line.strip()
    ]
    if not names:
        return "No changed files were present for the validated task revision."

    repo_root = repo.resolve()
    rendered: list[str] = []
    for relative in names[:20]:
        candidate = (repo_root / relative).resolve()
        try:
            candidate.relative_to(repo_root)
        except ValueError as exc:
            raise EvidenceError("changed-file evidence escaped repository root") from exc
        if not candidate.is_file():
            continue
        try:
            raw = candidate.read_bytes()
        except OSError as exc:
            raise EvidenceError("cannot read changed-file review evidence") from exc
        if b"\x00" in raw:
            rendered.append(f"### {relative}\n[binary file omitted]")
            continue
        rendered.append(f"### {relative}\n{raw.decode('utf-8', errors='replace')}")

    if len(names) > 20:
        rendered.append(f"[{len(names) - 20} additional changed files omitted]")
    return "\n\n".join(rendered) or "No readable changed-file contents were available."


def _validated_artifact_contents(
    config: OrchestratorConfig,
    receipt: ValidationReceipt,
) -> str:
    """Include supervisor-validated coordination artifacts named by successful gates.

    Only existing regular files under the coordination mailbox/evidence roots may be
    followed. Gate output is evidence, not filesystem authority, so every candidate
    is canonicalized and confined before reading.
    """

    root = config.coordination_root.resolve()
    allowed_roots = tuple(
        path.resolve() for path in (root / "mailbox", root / "evidence") if path.exists()
    )
    if not allowed_roots:
        return "No validated coordination artifacts were referenced by gate output."

    candidates: list[Path] = []
    seen: set[Path] = set()
    for gate in receipt.gates:
        for raw_line in f"{gate.stdout}\n{gate.stderr}".splitlines():
            line = raw_line.strip().strip('"')
            if not line:
                continue
            path: Path | None = None
            try:
                raw_path = Path(line)
                if raw_path.is_absolute():
                    path = raw_path.resolve()
                elif line.replace("\\", "/").startswith(("mailbox/", "evidence/")):
                    path = (root / raw_path).resolve()
            except (OSError, ValueError):
                continue
            if path is None or path in seen:
                continue
            if not any(path == allowed or allowed in path.parents for allowed in allowed_roots):
                continue
            try:
                if not path.is_file() or path.stat().st_size > 128_000:
                    continue
            except OSError:
                continue
            seen.add(path)
            candidates.append(path)

    if not candidates:
        return "No validated coordination artifacts were referenced by gate output."

    rendered: list[str] = []
    for path in candidates[:12]:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            raise EvidenceError("cannot read validated coordination artifact") from exc
        relative = path.relative_to(root).as_posix()
        rendered.append(f"### {relative}\n{text}")
    if len(candidates) > 12:
        rendered.append(f"[{len(candidates) - 12} additional validated artifacts omitted]")
    return "\n\n".join(rendered)


def _receipt_text(receipt: ValidationReceipt) -> str:
    """Render a concise validation receipt suitable for bounded review evidence.

    Validation command output can be extremely verbose (for example pytest warning
    summaries). Preserve every gate identity/exit code plus bounded command/stdout/
    stderr excerpts so a noisy but successful gate cannot make the entire review
    bundle critically truncated.
    """

    lines = [
        f"role: {receipt.role}",
        f"task_id: {receipt.task_id}",
        f"base_head: {receipt.base_head}",
        f"head: {receipt.head}",
        f"passed_at: {receipt.passed_at}",
    ]
    gate_count = max(1, len(receipt.gates))
    payload_budget = max(320, 8_000 // gate_count)
    command_budget = max(120, payload_budget // 3)
    stdout_budget = max(140, payload_budget // 2)
    stderr_budget = max(80, payload_budget - command_budget - stdout_budget)

    for gate in receipt.gates:
        command, _ = _bound(
            "gate_command",
            subprocess.list2cmdline(list(gate.command)),
            command_budget,
        )
        stdout, _ = _bound("gate_stdout", gate.stdout or "", stdout_budget)
        stderr, _ = _bound("gate_stderr", gate.stderr or "", stderr_budget)
        lines.extend(
            [
                f"gate: {gate.name}",
                f"command: {command}",
                f"cwd: {gate.cwd}",
                f"exit_code: {gate.exit_code}",
                f"stdout:\n{stdout}",
                f"stderr:\n{stderr}",
            ]
        )
    return "\n".join(lines)


def build_review_evidence(
    config: OrchestratorConfig,
    role: str,
    state: WorkerState,
    task: TaskItem,
    coordination_context: str,
    invocation_id: str,
    *,
    max_section_chars: int | None = None,
) -> ReviewEvidenceBundle:
    repo = _repo_root(config, role)
    base_head = state.task_base_head.strip()
    if not base_head:
        raise EvidenceError("review evidence requires a captured task base revision")
    head = _git(repo, "rev-parse", "HEAD").strip()
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=normal")
    if dirty.strip():
        raise EvidenceError("review evidence rejected a dirty repository")

    try:
        _git(repo, "cat-file", "-e", f"{base_head}^{{commit}}")
        _git(repo, "cat-file", "-e", f"{head}^{{commit}}")
    except EvidenceError as exc:
        raise EvidenceError("review evidence revision is unavailable") from exc
    try:
        receipt = load_validation_receipt(
            config, role, task.task_id, base_head=base_head, head=head
        )
    except (FileNotFoundError, ValueError) as exc:
        raise EvidenceError("matching validation receipt is required") from exc

    task_diff = _git(repo, "diff", f"{base_head}..{head}", "--")
    changed_file_contents = _changed_file_contents(
        repo,
        base_head,
        head,
        task_diff,
    )

    validated_artifacts = _validated_artifact_contents(config, receipt)

    sections = (
        (
            "identity",
            "\n".join(
                [
                    f"role: {role}",
                    f"task_id: {task.task_id}",
                    f"invocation_id: {invocation_id}",
                    f"base_head: {base_head}",
                    f"head: {head}",
                ]
            ),
        ),
        ("task", f"prompt:\n{task.prompt}\n\nhistorical_feedback_context_only:\n{task.feedback}"),
        ("task_issues", _task_issue_contents(config, task)),
        ("coordination", coordination_context),
        ("outgoing_coordination", _outgoing_coordination(config, role, task)),
        ("diff_stat", _git(repo, "diff", "--stat", f"{base_head}..{head}", "--")),
        ("task_diff", task_diff),
        ("changed_file_contents", changed_file_contents),
        ("validated_artifacts", validated_artifacts),
        ("validation", _receipt_text(receipt)),
    )
    rendered: list[str] = []
    reasons: list[str] = []
    for name, raw in sections:
        safe = _redact(raw, config)
        limit = (
            max_section_chars if max_section_chars is not None else _DEFAULT_SECTION_LIMITS[name]
        )
        bounded, truncated = _bound(name, safe, limit)
        if truncated and (max_section_chars is not None or name in _REVIEW_CRITICAL_SECTIONS):
            reasons.append(name)
        rendered.append(f"## {name}\n{bounded}")
    return ReviewEvidenceBundle(
        text="\n\n".join(rendered),
        truncated=bool(reasons),
        truncation_reasons=tuple(reasons),
        base_head=base_head,
        head=head,
    )
