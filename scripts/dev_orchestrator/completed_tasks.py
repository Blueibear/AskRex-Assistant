from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .storage import AtomicJsonStore
from .types import TaskItem


_WORK_KEY_PATTERNS = (
    ("US", re.compile(r"(?i)(?<![A-Z0-9])US[-_ ]?(\d{1,4})(?!\d)")),
    ("TEST", re.compile(r"(?i)(?<![A-Z0-9])TEST[-_ ]?(\d{1,4})(?!\d)")),
    ("STORY-S", re.compile(r"(?i)(?<![A-Z0-9])STORY[-_ ]?S[-_ ]?(\d{1,4})(?!\d)")),
)
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def canonical_work_keys(task: TaskItem) -> tuple[str, ...]:
    text = f"{task.task_id}\n{task.prompt}"
    keys = [f"TASK:{task.task_id.strip().casefold()}"]
    for prefix, pattern in _WORK_KEY_PATTERNS:
        for match in pattern.finditer(text):
            keys.append(f"{prefix}-{int(match.group(1))}")
    return tuple(dict.fromkeys(keys))


def _is_ancestor(repo: Path, completed_head: str, current_head: str) -> bool:
    if not _FULL_SHA.fullmatch(completed_head) or not _FULL_SHA.fullmatch(current_head):
        return False
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", completed_head, current_head],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


class CompletedTaskLedger:
    """Durable reviewed-task history used to prevent stale planner resurrection."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _store(self, role: str) -> AtomicJsonStore:
        return AtomicJsonStore(self.root / "completed-tasks" / f"{role}.json")

    def records(self, role: str) -> list[dict[str, Any]]:
        raw = self._store(role).read(default=[])
        if not isinstance(raw, list):
            raise ValueError(f"completed-task ledger for {role} must be a list")
        records: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                records.append(dict(item))
        return records

    def record(
        self,
        role: str,
        task: TaskItem,
        *,
        completed_head: str,
        invocation_id: str = "",
        source: str,
    ) -> bool:
        if not _FULL_SHA.fullmatch(completed_head):
            return False
        keys = list(canonical_work_keys(task))
        record = {
            "task_id": task.task_id,
            "keys": keys,
            "completed_head": completed_head,
            "source": source,
            "invocation_id": invocation_id,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        records = self.records(role)
        keyset = set(keys)
        records = [
            existing
            for existing in records
            if not (
                str(existing.get("completed_head", "")) == completed_head
                and keyset.intersection(str(key) for key in existing.get("keys", []))
            )
        ]
        records.append(record)
        self._store(role).write(records)
        return True

    def matching_record(
        self,
        role: str,
        task: TaskItem,
        *,
        repo: Path,
        current_head: str,
    ) -> dict[str, Any] | None:
        candidate_keys = set(canonical_work_keys(task))
        for record in reversed(self.records(role)):
            completed_head = str(record.get("completed_head", ""))
            keys = {str(key) for key in record.get("keys", [])}
            if not candidate_keys.intersection(keys):
                continue
            if _is_ancestor(repo, completed_head, current_head):
                return record
        return None

    def planning_summary(self, role: str, *, repo: Path, current_head: str) -> str:
        lines: list[str] = []
        for record in self.records(role):
            completed_head = str(record.get("completed_head", ""))
            if not _is_ancestor(repo, completed_head, current_head):
                continue
            task_id = str(record.get("task_id", ""))
            keys = ", ".join(str(key) for key in record.get("keys", []))
            lines.append(f"- {task_id} [{keys}] reviewed at {completed_head}")
        if not lines:
            return ""
        return (
            "\n\n## Reviewed completed-task ledger\n"
            "Do not assign these completed work items again from stale mailbox/history evidence. "
            "A genuinely new regression must be represented by a new explicit task or issue.\n"
            + "\n".join(lines)
        )
