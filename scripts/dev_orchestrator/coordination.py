from __future__ import annotations

import hashlib
import re
import stat
import uuid
from pathlib import Path

from .lifecycle import ControlPlaneLock
from .result_contract import task_bound_test_issue_ids
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
    first_line = task_text.splitlines()[0].strip() if task_text.splitlines() else ""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", first_line):
        anchors.add(first_line.casefold())
    for pattern in (
        r"(?i)\bROADMAP-US\d+(?:-[A-Z0-9-]+)?\b",
        r"(?i)\bROADMAP-US\d+\b",
        r"(?i)\bUS-?\d+\b",
        r"(?i)\bTEST-\d+\b",
        r"(?i)\bR\d+-\d+\b",
        r"(?i)\b(?:ROADMAP|SECURITY)-[A-Z0-9-]+\b",
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


def _header_field(text: str, field: str) -> str:
    """Read the first canonical header, never quoted sender/Related lines in a body."""
    for line in text.splitlines():
        if line.lstrip().startswith("## "):
            break
        name, separator, value = line.partition(":")
        if separator and name.strip().casefold() == field.casefold():
            return value.strip()
    return ""


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
    first_line = task_text.splitlines()[0].strip() if task_text.splitlines() else ""
    task_id = (
        first_line.casefold()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", first_line)
        else ""
    )
    ranked: list[tuple[int, int, int, int, Path, str]] = []
    for path in paths:
        text = _bounded_read(path, max_chars)
        if not text:
            continue
        searchable = f"{path.name.casefold()}\n{text.casefold()}"
        explicit = int(path.name.casefold() in task_lower)
        exact_related = int(
            bool(task_id)
            and bool(
                re.search(
                    r"(?<![a-z0-9_.-])" + re.escape(task_id) + r"(?![a-z0-9_.-])",
                    _header_field(text, "Related").casefold(),
                )
            )
        )
        hits = sum(1 for anchor in anchors if anchor in searchable)
        if task_text and not explicit and hits == 0:
            continue
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        ranked.append((explicit, exact_related, hits, mtime_ns, path, text))
    ranked.sort(key=lambda item: item[:4], reverse=True)
    return [(path, text) for _explicit, _related, _hits, _mtime, path, text in ranked[:limit]]


def _trusted_issue_path(root: Path, path: Path) -> None:
    issues = root / "issues"
    expected = root.resolve() / "issues"
    for candidate in (issues, path):
        metadata = candidate.lstat()
        if (
            candidate.is_symlink()
            or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("trusted issue source must not be a reparse point: " + candidate.name)
    if issues.resolve() != expected or (path != issues and path.resolve().parent != expected):
        raise ValueError("issue source escapes the trusted coordination issues directory")


def _complete_task_message(root: Path, path: Path) -> str:
    expected = root.resolve() / path.relative_to(root)
    metadata = path.lstat()
    if (
        path.resolve() != expected
        or path.is_symlink()
        or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError("task correspondence escapes its trusted coordination path")
    if metadata.st_size > 480_000:
        raise ValueError("task correspondence exceeds bounded context capacity: " + path.name)
    return path.read_text(encoding="utf-8-sig")


def coordination_sources(
    root: Path,
    role: str,
    *,
    deferred_issue_ids: tuple[str, ...] = (),
    deferred_task_prefixes: tuple[str, ...] = (),
    task_text: str = "",
) -> list[tuple[str, str, str]]:
    """Read each selected source once; outgoing text is not an external wake event."""
    if role not in _ROLE_FILES:
        raise ValueError(f"unsupported coordination role: {role}")
    sources: list[tuple[str, str, str]] = [
        ("PROTOCOL.md", _bounded_read(root / "PROTOCOL.md"), "required"),
        (_ROLE_FILES[role], _bounded_read(root / _ROLE_FILES[role]), "required"),
    ]
    if deferred_issue_ids or deferred_task_prefixes:
        constraints = [
            "The following work is explicitly deferred. Do not assign or execute it.",
            "Deferred issues: " + ", ".join(deferred_issue_ids),
            "Deferred task prefixes: " + ", ".join(deferred_task_prefixes),
        ]
        sources.append(("Owner scheduling constraints", "\n".join(constraints), "required"))
    anchors = _task_context_anchors(task_text)
    issues = root / "issues"
    explicit_names: set[str] = set()
    for match in re.finditer(r"(?i)\b(?:ROADMAP|SECURITY|TEST|STORY)-[A-Z0-9_-]+\.md\b", task_text):
        prefix = task_text[: match.start()].replace("\\", "/")
        if prefix.endswith("/") and not prefix.casefold().endswith("issues/"):
            # Repository-qualified audit/output paths are not shared issue files.
            continue
        explicit_names.add(match.group(0).casefold())
    if issues.exists():
        _trusted_issue_path(root, issues)
    paths = sorted(issues.glob("*.md")) if issues.is_dir() else []
    by_name = {path.name.casefold(): path for path in paths}
    missing = explicit_names - by_name.keys()
    if missing:
        raise ValueError("explicit shared requirement is missing: " + ", ".join(sorted(missing)))
    deferred = {value.strip().casefold() for value in deferred_issue_ids if value.strip()}
    for path in paths:
        explicit = path.name.casefold() in explicit_names or path.stem.casefold() in anchors
        if explicit:
            # A referenced requirement is evidence even when its status is not an
            # active defect. Reading it grants no ownership or mutation authority.
            _trusted_issue_path(root, path)
            if path.stat().st_size > 480_000:
                raise ValueError(
                    "explicit shared requirement exceeds context capacity: " + path.name
                )
            text = path.read_text(encoding="utf-8-sig")
            _trusted_issue_path(root, path)
            sources.append((f"issues/{path.name}", text, "required"))
        elif path.stem.casefold() not in deferred:
            text = _bounded_read(path, 6000)
            if _issue_status(text) in {"open", "fixed-needs-retest"} and _issue_owner(text) in {
                role,
                "both",
            }:
                sources.append((f"issues/{path.name}", text, "history"))

    mailbox = root / "mailbox" / role
    if mailbox.is_dir():
        mailbox_paths = [
            path
            for path in mailbox.glob("*.md")
            if _header_field(_bounded_read(path, 6000), "From").casefold() != role
        ]
        selected: list[tuple[Path, str]] = []
        seen: set[Path] = set()
        matching: set[Path] = set()
        limit = 20 if task_text else 6
        max_chars = 6000 if task_text else 2500
        if task_text:
            selected = _rank_task_files(
                mailbox_paths,
                task_text=task_text,
                anchors=anchors,
                max_chars=max_chars,
                limit=12,
            )
            seen.update(path for path, _text in selected)
            matching.update(seen)
        for path in sorted(mailbox_paths, key=lambda item: item.stat().st_mtime_ns, reverse=True):
            if path in seen:
                continue
            selected.append((path, _bounded_read(path, max_chars)))
            seen.add(path)
            if len(selected) >= limit:
                break
        for path, text in selected[:limit]:
            authored_by_role = _header_field(text, "From").casefold() == role
            category = (
                "outgoing" if authored_by_role else ("incoming" if path in matching else "history")
            )
            if category in {"incoming", "outgoing"}:
                text = _complete_task_message(root, path)
            sources.append((f"mailbox/{role}/{path.name}", text, category))

    if task_text and anchors:
        outgoing: list[Path] = []
        for recipient in ("backend", "mobile", "testing", "supervisor"):
            for path in (root / "mailbox" / recipient).glob("*.md"):
                text = _bounded_read(path, 6000)
                if _header_field(text, "From").casefold() == role:
                    outgoing.append(path)
        for path, _text in _rank_task_files(
            outgoing, task_text=task_text, anchors=anchors, max_chars=6000, limit=12
        ):
            sources.append(
                (path.relative_to(root).as_posix(), _complete_task_message(root, path), "outgoing")
            )
        for directory, pattern, limit, max_chars in (
            ("evidence", "*", 8, 5000),
            ("validation", "*.json", 4, 5000),
        ):
            task_root = root / directory
            if not task_root.is_dir():
                continue
            for path, text in _rank_task_files(
                [item for item in task_root.glob(pattern) if item.is_file()],
                task_text=task_text,
                anchors=anchors,
                max_chars=max_chars,
                limit=limit,
            ):
                sources.append((f"{directory}/{path.name}", text, "history"))
    return [source for source in sources if source[1]]


def render_coordination_source(name: str, text: str, category: str) -> str:
    label = {
        "required": "Canonical shared requirement (ownership and deferral rules still apply)",
        "history": "Historical coordination (claims may be superseded by matching host evidence)",
        "incoming": "Task-matched incoming coordination (agreement must be explicit and within authority)",
        "outgoing": "Existing outgoing coordination (proposal or report, not approval)",
        "host": "Host-observed task and revision evidence",
    }[category]
    return f"## {name}\n[{label}]\n{text}"


def build_coordination_context(
    root: Path,
    role: str,
    *,
    deferred_issue_ids: tuple[str, ...] = (),
    deferred_task_prefixes: tuple[str, ...] = (),
    task_text: str = "",
) -> str:
    return "\n\n".join(
        render_coordination_source(*source)
        for source in coordination_sources(
            root,
            role,
            deferred_issue_ids=deferred_issue_ids,
            deferred_task_prefixes=deferred_task_prefixes,
            task_text=task_text,
        )
    )


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
        task_match = update.issue_id in task_bound_test_issue_ids(task_id)
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
