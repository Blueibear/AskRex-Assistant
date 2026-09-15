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
    max_section_chars: int = 12_000,
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
        ("task", f"prompt:\n{task.prompt}\n\nfeedback:\n{task.feedback}"),
        ("coordination", coordination_context),
        ("diff_stat", _git(repo, "diff", "--stat", f"{base_head}..{head}", "--")),
        ("task_diff", _git(repo, "diff", f"{base_head}..{head}", "--")),
        ("validation", _receipt_text(receipt)),
    )
    rendered: list[str] = []
    reasons: list[str] = []
    for name, raw in sections:
        safe = _redact(raw, config)
        bounded, truncated = _bound(name, safe, max_section_chars)
        if truncated:
            reasons.append(name)
        rendered.append(f"## {name}\n{bounded}")
    return ReviewEvidenceBundle(
        text="\n\n".join(rendered),
        truncated=bool(reasons),
        truncation_reasons=tuple(reasons),
        base_head=base_head,
        head=head,
    )
