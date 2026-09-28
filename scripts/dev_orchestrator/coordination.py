from __future__ import annotations

import hashlib
import re
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


def _task_context_anchors(task_text: str) -> tuple[str, ...]:
    lowered = task_text.casefold()
    anchors: set[str] = set()
    for pattern in (
        r"(?i)\bROADMAP-US\d+(?:-[A-Z0-9-]+)?\b",
        r"(?i)\bROADMAP-US\d+\b",
        r"(?i)\bUS-?\d+\b",
        r"(?i)\bTEST-\d+\b",
        r"(?i)\bSTORY-S\d+(?:-[A-Z0-9-]+)?\b",
        r"(?i)\bMSG-[A-Za-z0-9-]+(?:\.md)?\b",
        r"(?i)\b[a-f0-9]{40}\b",
    ):
        for match in re.finditer(pattern, task_text):
            anchors.add(match.group(0).casefold())
    for match in re.finditer(r"(?i)\b[A-Za-z0-9_.-]+\.(?:md|json|txt)\b", task_text):
        anchors.add(match.group(0).casefold())
    if "roadmap-us" in lowered:
        for match in re.finditer(r"(?i)\bROADMAP-US\d+\b", task_text):
            anchors.add(match.group(0).casefold())
    return tuple(sorted(anchor for anchor in anchors if anchor))


def _rank_task_files(
    paths: list[Path],
    *,
    task_text: str,
    anchors: tuple[str, ...],
    max_chars: int,
    limit: int,
) -> list[tuple[Path, str]]:
    if not paths:
        return []
    task_lower = task_text.casefold()
    ranked: list[tuple[int, int, int, Path, str]] = []
    for path in paths:
        text = _bounded_read(path, max_chars)
        if not text:
            continue
        searchable = f"{path.name.casefold()}\n{text.casefold()}"
        explicit = int(path.name.casefold() in task_lower)
        hits = sum(1 for anchor in anchors if anchor in searchable)
        if task_text and not explicit and hits == 0:
            continue
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        ranked.append((explicit, hits, mtime_ns, path, text))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [(path, text) for _explicit, _hits, _mtime, path, text in ranked[:limit]]


def build_coordination_context(
    root: Path,
    role: str,
    *,
    deferred_issue_ids: tuple[str, ...] = (),
    deferred_task_prefixes: tuple[str, ...] = (),
    task_text: str = "",
) -> str:
    if role not in _ROLE_FILES:
        raise ValueError(f"unsupported coordination role: {role}")
    sections = [
        f"# Shared coordination context for {role}",
        "## PROTOCOL.md",
        _bounded_read(root / "PROTOCOL.md"),
        f"## {_ROLE_FILES[role]}",
        _bounded_read(root / _ROLE_FILES[role]),
    ]
    if deferred_issue_ids or deferred_task_prefixes:
        constraints = [
            "## Owner scheduling constraints",
            "The following work is explicitly deferred for the current campaign. Do not assign or execute it.",
        ]
        if deferred_issue_ids:
            constraints.append("Deferred issues: " + ", ".join(deferred_issue_ids))
        if deferred_task_prefixes:
            constraints.append("Deferred task prefixes: " + ", ".join(deferred_task_prefixes))
        sections.append("\n".join(constraints))
    anchors = _task_context_anchors(task_text)
    mailbox = root / "mailbox" / role
    if mailbox.is_dir():
        mailbox_paths = list(mailbox.glob("*.md"))
        selected_mailbox: list[tuple[Path, str]] = []
        seen_mailbox: set[Path] = set()
        mailbox_limit = 20 if task_text else 6
        mailbox_max_chars = 6000 if task_text else 2500
        if task_text:
            for path, text in _rank_task_files(
                mailbox_paths,
                task_text=task_text,
                anchors=anchors,
                max_chars=mailbox_max_chars,
                limit=12,
            ):
                selected_mailbox.append((path, text))
                seen_mailbox.add(path)
        recent_mailbox = sorted(
            mailbox_paths,
            key=lambda path: path.stat().st_mtime_ns if path.exists() else 0,
            reverse=True,
        )
        for path in recent_mailbox:
            if path in seen_mailbox:
                continue
            selected_mailbox.append((path, _bounded_read(path, mailbox_max_chars)))
            seen_mailbox.add(path)
            if len(selected_mailbox) >= mailbox_limit:
                break
        for path, text in selected_mailbox[:mailbox_limit]:
            sections.extend((f"## mailbox/{role}/{path.name}", text))

    if task_text and anchors:
        for directory, pattern, limit, max_chars in (
            ("evidence", "*", 8, 5000),
            ("validation", "*.json", 4, 5000),
        ):
            task_root = root / directory
            if not task_root.is_dir():
                continue
            paths = [path for path in task_root.glob(pattern) if path.is_file()]
            for path, text in _rank_task_files(
                paths,
                task_text=task_text,
                anchors=anchors,
                max_chars=max_chars,
                limit=limit,
            ):
                sections.extend((f"## {directory}/{path.name}", text))

    deferred = {value.strip().lower() for value in deferred_issue_ids if value.strip()}
    issues = root / "issues"
    if issues.is_dir():
        for path in sorted(issues.glob("*.md")):
            if path.stem.lower() in deferred:
                continue
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
        task_match = update.issue_id == task_id or bool(
            re.search(
                rf"(?<![A-Z0-9]){re.escape(update.issue_id)}(?![A-Z0-9])",
                task_id,
                flags=re.IGNORECASE,
            )
        )
        if not task_match:
            raise ValueError(f"issue update {update.issue_id} does not match active task {task_id}")
        issue = root / "issues" / f"{update.issue_id}.md"
        if not issue.is_file():
            raise ValueError(f"unknown live-test issue: {update.issue_id}")
        original = issue.read_text(encoding="utf-8")
        if _issue_owner(original) not in {role, "both"}:
            raise ValueError(f"{role} does not own {update.issue_id}")
        if _issue_status(original) not in {"open", "fixed-needs-retest"}:
            raise ValueError(
                f"{update.issue_id} must be open or fixed-needs-retest before requesting retest"
            )


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


def active_owned_issues(
    root: Path, role: str, *, deferred_issue_ids: tuple[str, ...] = ()
) -> list[Path]:
    active: list[Path] = []
    deferred = {issue_id.strip().lower() for issue_id in deferred_issue_ids if issue_id.strip()}
    issues = root / "issues"
    if not issues.is_dir():
        return active
    for path in sorted(issues.glob("*.md")):
        if path.stem.lower() in deferred:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        owner = _issue_owner(text)
        status_active = _issue_status(text) in {"open", "fixed-needs-retest"}
        if status_active and owner in {role, "both"}:
            active.append(path)
    return active
