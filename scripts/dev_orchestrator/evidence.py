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
    "coordination": 6_000,
    "outgoing_coordination": 12_000,
    "diff_stat": 4_000,
    "task_diff": 70_000,
    "changed_file_contents": 35_000,
    "validation": 12_000,
}
_REVIEW_CRITICAL_SECTIONS = frozenset(
    {
        "identity",
        "task",
        "task_diff",
        "changed_file_contents",
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
            text=True,
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
    for match in re.finditer(r"(?i)\bS\d+\b", task.task_id):
        anchors.add(match.group(0).casefold())
    task_text = f"{task.prompt}\n{task.feedback}"
    for match in re.finditer(r"(?i)\bMSG-[A-Za-z0-9-]+", task_text):
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


def _receipt_text(receipt: ValidationReceipt) -> str:
    lines = [
        f"role: {receipt.role}",
        f"task_id: {receipt.task_id}",
        f"base_head: {receipt.base_head}",
        f"head: {receipt.head}",
        f"passed_at: {receipt.passed_at}",
    ]
    for gate in receipt.gates:
        lines.extend(
            [
                f"gate: {gate.name}",
                f"command: {subprocess.list2cmdline(list(gate.command))}",
                f"cwd: {gate.cwd}",
                f"exit_code: {gate.exit_code}",
                f"stdout:\n{gate.stdout}",
                f"stderr:\n{gate.stderr}",
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
        ("coordination", coordination_context),
        ("outgoing_coordination", _outgoing_coordination(config, role, task)),
        ("diff_stat", _git(repo, "diff", "--stat", f"{base_head}..{head}", "--")),
        ("task_diff", task_diff),
        ("changed_file_contents", changed_file_contents),
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
