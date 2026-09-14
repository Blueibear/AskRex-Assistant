from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from .lifecycle import ControlPlaneLock
from .storage import AtomicJsonStore, atomic_write_text

_ROLE_FILES = {
    "backend": "AGENT_BACKEND.md",
    "mobile": "AGENT_MOBILE.md",
}


def _bounded_read(path: Path, max_chars: int = 12000) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[truncated by supervisor]\n"


def build_coordination_context(root: Path, role: str) -> str:
    if role not in _ROLE_FILES:
        raise ValueError(f"unsupported coordination role: {role}")
    sections = [
        f"# Shared coordination context for {role}",
        "## PROTOCOL.md",
        _bounded_read(root / "PROTOCOL.md"),
        f"## {_ROLE_FILES[role]}",
        _bounded_read(root / _ROLE_FILES[role]),
    ]
    mailbox = root / "mailbox" / role
    if mailbox.is_dir():
        for path in sorted(mailbox.glob("*.md"))[-20:]:
            sections.extend((f"## mailbox/{role}/{path.name}", _bounded_read(path, 6000)))

    issues = root / "issues"
    if issues.is_dir():
        for path in sorted(issues.glob("*.md")):
            text = _bounded_read(path, 6000)
            owner = _issue_owner(text)
            active = _issue_status(text) in {"open", "fixed-needs-retest"}
            if active and owner in {role, "both"}:
                sections.extend((f"## issues/{path.name}", text))

    return "\n\n".join(section for section in sections if section)


def _issue_owner(text: str) -> str:
    for line in text.splitlines():
        if line.lower().startswith("owner:"):
            return line.split(":", 1)[1].strip().strip("`").lower()
    return ""


def _issue_status(text: str) -> str:
    for line in text.splitlines():
        if line.lower().startswith("status:"):
            return line.split(":", 1)[1].strip().strip("`").lower()
    return ""


def validate_agent_updates(
    root: Path, role: str, result, *, allow_issue_updates: bool, task_id: str = ""
) -> None:
    import re

    if result.issue_updates and not allow_issue_updates:
        raise ValueError("issue updates are not allowed for this agent phase")
    for update in result.issue_updates:
        if update.status != "fixed-needs-retest":
            raise ValueError("development agents may only request fixed-needs-retest")
        if not re.fullmatch(r"TEST-\d{3}", update.issue_id):
            raise ValueError(f"invalid live-test issue id: {update.issue_id}")
        if update.issue_id != task_id:
            raise ValueError(f"issue update {update.issue_id} does not match active task {task_id}")
        issue = root / "issues" / f"{update.issue_id}.md"
        if not issue.is_file():
            raise ValueError(f"unknown live-test issue: {update.issue_id}")
        original = issue.read_text(encoding="utf-8")
        if _issue_owner(original) not in {role, "both"}:
            raise ValueError(f"{role} does not own {update.issue_id}")
        if _issue_status(original) != "open":
            raise ValueError(f"{update.issue_id} must be open before requesting retest")


def _safe_transaction_destination(root: Path, relative: str) -> Path:
    destination = (root / relative).resolve()
    mailbox = (root / "mailbox").resolve()
    try:
        destination.relative_to(mailbox)
    except ValueError as exc:
        raise ValueError("coordination transaction destination escapes mailbox root") from exc
    return destination


def _recover_coordination_transactions_locked(root: Path) -> None:
    transaction_root = root / "transactions" / "coordination"
    if not transaction_root.is_dir():
        return
    for manifest in sorted(transaction_root.glob("TXN-*.json")):
        data = AtomicJsonStore(manifest).read(default={})
        destinations = data.get("destinations", []) if isinstance(data, dict) else []
        if not isinstance(destinations, list):
            raise ValueError(f"invalid coordination transaction manifest: {manifest.name}")
        for relative in destinations:
            _safe_transaction_destination(root, str(relative)).unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)


def recover_coordination_transactions(root: Path) -> None:
    with ControlPlaneLock(root / "coordination-publication.lock"):
        _recover_coordination_transactions_locked(root)


def _publish_coordination_transaction_locked(root: Path, writes: list[tuple[Path, str]]) -> None:
    if not writes:
        return
    transaction_root = root / "transactions" / "coordination"
    transaction_id = uuid.uuid4().hex
    manifest = transaction_root / f"TXN-{transaction_id}.json"
    relative_paths = [str(path.resolve().relative_to(root.resolve())) for path, _ in writes]
    for relative in relative_paths:
        _safe_transaction_destination(root, relative)
    AtomicJsonStore(manifest).write(
        {"transaction_id": transaction_id, "destinations": relative_paths}
    )
    try:
        for path, body in writes:
            atomic_write_text(path, body)
    except BaseException:
        rollback_ok = True
        for relative in relative_paths:
            try:
                _safe_transaction_destination(root, relative).unlink(missing_ok=True)
            except OSError:
                rollback_ok = False
        if rollback_ok:
            manifest.unlink(missing_ok=True)
        raise
    manifest.unlink(missing_ok=True)


def _publish_coordination_transaction(root: Path, writes: list[tuple[Path, str]]) -> None:
    with ControlPlaneLock(root / "coordination-publication.lock"):
        _recover_coordination_transactions_locked(root)
        _publish_coordination_transaction_locked(root, writes)


def apply_agent_updates(
    root: Path, role: str, result, *, allow_issue_updates: bool, task_id: str = ""
) -> None:
    recover_coordination_transactions(root)
    validate_agent_updates(
        root, role, result, allow_issue_updates=allow_issue_updates, task_id=task_id
    )
    has_publication = bool(result.issue_updates or result.coordination_messages)
    invocation_id = result.invocation_id.strip()
    if has_publication and not invocation_id:
        raise ValueError("coordination publication requires invocation_id")
    publication_id = hashlib.sha256(invocation_id.encode("utf-8")).hexdigest()[:20]

    writes: list[tuple[Path, str]] = []
    for index, update in enumerate(result.issue_updates):
        mailbox = root / "mailbox" / "testing"
        path = mailbox / f"RETEST-{publication_id}-{index:02d}-{update.issue_id}.md"
        body = (
            "# AskRex Retest Request\n\n"
            f"From: {role}\nTo: testing\nRelated: {update.issue_id}\nNeeds response: yes\n"
            f"Invocation: {invocation_id}\n\n"
            "## Request\n"
            f"Independent development review passed for {update.issue_id}. "
            "Testing owns the canonical issue status; please retest the real path and update the issue.\n\n"
            f"## Development note\n{update.note}\n"
        )
        writes.append((path, body))

    for index, message in enumerate(result.coordination_messages):
        mailbox = root / "mailbox" / message.to
        path = mailbox / f"MSG-{publication_id}-{index:02d}-{role}.md"
        body = (
            "# AskRex Coordination Message\n\n"
            f"From: {role}\nTo: {message.to}\nPriority: {message.priority}\n"
            f"Related: {message.related}\n"
            f"Needs response: {'yes' if message.needs_response else 'no'}\n"
            f"Invocation: {invocation_id}\n\n"
            f"## Message\n{message.body}\n"
        )
        writes.append((path, body))

    pending: list[tuple[Path, str]] = []
    for path, body in writes:
        if path.is_file():
            if path.read_text(encoding="utf-8") == body:
                continue
            raise ValueError(f"conflicting coordination replay for {path.name}")
        pending.append((path, body))
    _publish_coordination_transaction(root, pending)


def active_owned_issues(root: Path, role: str) -> list[Path]:
    active: list[Path] = []
    issues = root / "issues"
    if not issues.is_dir():
        return active
    for path in sorted(issues.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        owner = _issue_owner(text)
        status_active = _issue_status(text) in {"open", "fixed-needs-retest"}
        if status_active and owner in {role, "both"}:
            active.append(path)
    return active
