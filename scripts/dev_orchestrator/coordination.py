from __future__ import annotations

from pathlib import Path

_ROLE_FILES = {
    "backend": "AGENT_BACKEND.md",
    "mobile": "AGENT_MOBILE.md",
}


def _bounded_read(path: Path, max_chars: int = 12000) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
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
            normalized = text.lower()
            owns_role = f"owner: `{role}`" in normalized or f"owner: {role}" in normalized
            owns_both = "owner: `both`" in normalized or "owner: both" in normalized
            active = "status: `open`" in normalized or "status: open" in normalized
            active = active or "fixed-needs-retest" in normalized
            if active and (owns_role or owns_both):
                sections.extend((f"## issues/{path.name}", text))

    return "\n\n".join(section for section in sections if section)
