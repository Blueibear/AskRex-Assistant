#!/usr/bin/env python3
"""Security audit script for Rex AI Assistant repository.

Scans source files for:
- Merge conflict markers
- Placeholder/incomplete code markers
- Exposed secrets and API keys

Excludes: .git, .venv, venv, source, node_modules, dist, build, __pycache__,
          .mypy_cache, .ruff_cache, .pytest_cache, *.egg-info, backups, logs,
          .claude (worktrees/settings), htmlcov

When run inside a git repository, only git-tracked files are scanned so that
generated caches and untracked files are automatically excluded.

Flags:
    --strict-markdown-secrets  Also scan inside Markdown fenced code blocks for
                               secrets (and merge markers). Default behavior
                               skips fenced blocks to reduce false positives.
    --allowlist FILE           Path(s) exempt from strict-mode fenced-block
                               scanning (may be repeated).
    --release-gate             Fail on merge markers, secrets, invalid
                               suppressions, and actionable source/config
                               placeholder findings.
    --suppressions FILE        JSON file containing narrow, documented,
                               expiring placeholder suppressions.
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Paths excluded from self-scan: this script and its dedicated test file, both of
# which contain the marker patterns intentionally (pattern definitions / test fixtures).
_SELF_EXCLUDED: set[Path] = {
    Path(__file__).resolve(),
    (Path(__file__).parent.parent / "tests" / "test_security_audit.py").resolve(),
}


def _configure_text_io() -> None:
    """Avoid UnicodeEncodeError on Windows consoles by forcing UTF-8 with safe fallback."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
        except Exception:
            pass


# Directories to exclude
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".cache",
    ".claude",
    "archived",
    "venv",
    "source",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    "htmlcov",
    "backups",
    "logs",
    "outputs",
}

# File extensions to scan
INCLUDE_EXTENSIONS = {
    ".py",
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".json",
    ".ini",
    ".cfg",
    ".ps1",
    ".bat",
    ".sh",
    ".txt",
}

# Patterns to detect - strict Git conflict tokens only (must be exactly 7 chars,
# optionally followed by a space and branch name)
MERGE_MARKERS = re.compile(r"^(<{7}|={7}|>{7})( .*)?$")
PLACEHOLDER_MARKERS = re.compile(
    r"TRUNCAT|TBD|TODO|FIXME|PLACEHOLDER|INSERT HERE|REPLACE ME|\bWIP\b|COMING SOON|CUT HERE",
    re.IGNORECASE,
)

# Real secret patterns (not documentation placeholders)
SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{40,}"), "OpenAI API key"),  # Real keys are longer
    (
        re.compile(
            r"""["']?api[_-]?key["']?\s*[:=]\s*["'](?!sk-\.\.\.|your|example|test|dummy|xxx)[A-Za-z0-9]{20,}["']""",
            re.IGNORECASE,
        ),
        "Generic API key",
    ),
    (re.compile(r"-----BEGIN (RSA |DSA )?PRIVATE KEY-----"), "Private key"),
    (re.compile(r"aws_secret_access_key\s*=\s*[A-Za-z0-9/+]{40}"), "AWS secret key"),
]

# Extensions considered source code (findings here are critical/actionable)
SOURCE_EXTENSIONS = {".py", ".ps1", ".bat", ".sh"}

# Extensions considered configuration (findings here are actionable)
CONFIG_EXTENSIONS = {".json", ".yml", ".yaml", ".toml", ".ini", ".cfg"}

# Extensions considered documentation (findings here are informational)
DOC_EXTENSIONS = {".md", ".txt"}

# Findings are printed as "filepath:line: message". On Windows, the filepath can
# contain a ":" (drive letter). This regex greedily captures the filepath up to
# the final ":<digits>:" delimiter.
FINDING_PREFIX = re.compile(r"^(.*?):(\d+):\s")

_SUPPRESSIBLE_CATEGORIES = {"placeholder"}


@dataclass(frozen=True)
class FindingSuppression:
    """One narrow, documented, expiring security-audit exclusion."""

    category: str
    path: str
    line: int


def load_suppressions(
    suppression_path: Path,
    repo_root: Path,
    *,
    today: date | None = None,
) -> tuple[set[FindingSuppression], list[str]]:
    """Load validated suppressions and return ``(valid, errors)``."""
    current_date = today or date.today()
    try:
        raw = json.loads(suppression_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return set(), [f"Cannot read suppression file {suppression_path}: {exc}"]

    entries = raw.get("suppressions") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return set(), ["Suppression file must contain a 'suppressions' list"]

    valid: set[FindingSuppression] = set()
    errors: list[str] = []
    root = repo_root.resolve()
    for index, entry in enumerate(entries, 1):
        prefix = f"Suppression #{index}"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} must be an object")
            continue

        category = entry.get("category")
        raw_path = entry.get("path")
        line = entry.get("line")
        reason = entry.get("reason")
        expires = entry.get("expires")

        if category not in _SUPPRESSIBLE_CATEGORIES:
            errors.append(f"{prefix} has invalid category {category!r}")
            continue
        if not isinstance(raw_path, str) or not raw_path.strip():
            errors.append(f"{prefix} requires a repository-relative path")
            continue
        relative_path = Path(raw_path)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            errors.append(f"{prefix} path must stay within the repository")
            continue
        resolved_path = (root / relative_path).resolve()
        try:
            normalized_path = resolved_path.relative_to(root).as_posix()
        except ValueError:
            errors.append(f"{prefix} path must stay within the repository")
            continue
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            errors.append(f"{prefix} requires a positive integer line")
            continue
        if not isinstance(reason, str) or len(reason.strip()) < 10:
            errors.append(f"{prefix} requires a specific reason of at least 10 characters")
            continue
        try:
            expiry = date.fromisoformat(expires) if isinstance(expires, str) else None
        except ValueError:
            expiry = None
        if expiry is None:
            errors.append(f"{prefix} requires an ISO date in 'expires'")
            continue
        if expiry < current_date:
            errors.append(f"{prefix} expired on {expiry.isoformat()}")
            continue

        valid.add(FindingSuppression(category, normalized_path, line))

    return valid, errors


def _finding_suppression(
    finding: str,
    category: str,
    repo_root: Path,
) -> FindingSuppression | None:
    match = FINDING_PREFIX.match(finding)
    if match is None:
        return None
    try:
        relative_path = Path(match.group(1)).resolve().relative_to(repo_root.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    return FindingSuppression(category, relative_path, int(match.group(2)))


def classify_placeholder_finding(finding: str) -> str:
    """Classify a placeholder finding into a bucket based on file extension.

    Returns one of: "source-code", "configuration", "documentation", "needs-review".
    """
    m = FINDING_PREFIX.match(finding)
    if not m:
        return "needs-review"

    filepath_str = m.group(1)
    finding_path = Path(filepath_str)
    suffix = finding_path.suffix.lower()
    parts = {part.lower() for part in finding_path.parts}

    if parts.intersection({"docs", "tests", "archive", "archived"}):
        return "documentation"

    if suffix in SOURCE_EXTENSIONS:
        return "source-code"
    if suffix in CONFIG_EXTENSIONS:
        return "configuration"
    if suffix in DOC_EXTENSIONS:
        return "documentation"
    return "needs-review"


def should_scan_file(filepath: Path) -> bool:
    """Check if file should be scanned based on path and extension."""
    for part in filepath.parts:
        if part in EXCLUDE_DIRS:
            return False
        # Exclude generated egg-info directories (e.g. rex_ai_assistant.egg-info)
        if part.endswith(".egg-info"):
            return False
    return filepath.suffix.lower() in INCLUDE_EXTENSIONS


def _fenced_line_set(lines: list[str]) -> set[int]:
    """Return the set of 0-based line indices that are inside Markdown fenced code blocks."""
    inside: set[int] = set()
    in_fence = False

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                inside.add(idx)
                in_fence = False
            else:
                in_fence = True
                inside.add(idx)
        elif in_fence:
            inside.add(idx)

    return inside


def scan_file(
    filepath: Path,
    *,
    strict_markdown_secrets: bool = False,
    allowlisted_paths: set[Path] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Scan a file and return lists of issues found.

    Parameters
    ----------
    filepath:
        The file to scan.
    strict_markdown_secrets:
        When True, secrets (and merge markers) inside Markdown fenced code
        blocks are not skipped unless allowlisted.
    allowlisted_paths:
        Set of resolved file paths exempt from strict-mode fenced-block scanning.
    """
    merge_issues: list[str] = []
    placeholder_issues: list[str] = []
    secret_issues: list[str] = []

    if allowlisted_paths is None:
        allowlisted_paths = set()

    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
        lines = content.split("\n")

        is_markdown = filepath.suffix.lower() == ".md"
        fenced = _fenced_line_set(lines) if is_markdown else set()

        # In strict mode, scan fenced blocks unless file is allowlisted.
        skip_fenced = True
        if strict_markdown_secrets and is_markdown and filepath.resolve() not in allowlisted_paths:
            skip_fenced = False

        # Merge markers
        for i, line in enumerate(lines):
            if MERGE_MARKERS.match(line):
                if is_markdown and i in fenced and skip_fenced:
                    continue
                merge_issues.append(f"{filepath}:{i + 1}: {line[:50]}")

        # Placeholders (with false-positive filtering). Dependency lockfiles
        # contain package names such as ``cli-truncate`` and are generated,
        # so their prose markers are not incomplete application code.
        placeholder_lines = [] if filepath.name == "package-lock.json" else lines
        for i, line in enumerate(placeholder_lines, 1):
            if PLACEHOLDER_MARKERS.search(line):
                lowered = line.lower()

                # Skip: lines referencing the scanner's own patterns/scan logic
                if "scan" in lowered or "pattern" in lowered:
                    continue

                # Skip: placeholder used as part of a variable/path name
                if "placeholder" in lowered and ("_path" in lowered or "voice" in lowered):
                    continue

                # Skip legitimate HTML form placeholder attributes and
                # redaction/sentinel terminology.
                if "placeholder" in lowered and any(
                    token in lowered
                    for token in [
                        "placeholder=",
                        "_placeholder",
                        "placeholder.split",
                        "redact",
                        "sentinel",
                        "substitution token",
                        "abbreviation",
                    ]
                ):
                    continue

                # Skip process instructions that intentionally refer to a
                # task's TODO state rather than leaving this source incomplete.
                if "todo" in lowered and any(
                    token in lowered for token in ["mark the task", "task back to"]
                ):
                    continue

                # Skip: function/class definitions whose name contains the marker word
                # (e.g. `def test_placeholder`, `class TestOutputTruncation`)
                if re.match(r"\s*(def|class)\s+\w*(placeholder|truncat)\w*", lowered):
                    continue

                # Skip legitimate file-handle truncation calls. The broad TRUNCAT
                # marker exists to catch explicit incomplete-source markers, not
                # Python's FileIO/TextIOBase.truncate() operation.
                if "truncat" in lowered and re.search(r"\.\s*truncate\s*\(", lowered):
                    continue

                # Skip: truncation as a legitimate output/data-size limiting feature
                if "truncat" in lowered and any(
                    w in lowered
                    for w in [
                        "output",
                        "file",
                        "query",
                        "bytes",
                        "chars",
                        "size",
                        "limit",
                        "body",
                        "bodies",
                    ]
                ):
                    continue

                placeholder_issues.append(f"{filepath}:{i}: {line.strip()[:80]}")

        # Secrets
        for pattern, secret_type in SECRET_PATTERNS:
            for match in pattern.finditer(content):
                line_idx = content[: match.start()].count("\n")
                line_text = lines[line_idx] if line_idx < len(lines) else ""

                if is_markdown and line_idx in fenced and skip_fenced:
                    continue

                # Skip backtick-wrapped literal examples
                if "`" in line_text and match.group() in line_text:
                    before_match = line_text[: line_text.index(match.group())]
                    if before_match.count("`") % 2 == 1:
                        continue

                secret_issues.append(f"{filepath}:{line_idx + 1}: Potential {secret_type}")

    except Exception as e:
        print(f"Error scanning {filepath}: {e}", file=sys.stderr)

    return merge_issues, placeholder_issues, secret_issues


def get_tracked_files(repo_root: Path) -> tuple[list[Path], int]:
    """Return (scannable_files, excluded_count) for the repository.

    Uses ``git ls-files`` when available so that only tracked files are
    considered (untracked artefacts, caches, and worktree copies are
    automatically excluded).  Falls back to a full filesystem walk when git
    is not available.
    """
    excluded = 0
    files: list[Path] = []

    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            for rel in result.stdout.splitlines():
                if not rel:
                    continue
                fp = (repo_root / rel).resolve()
                if fp in _SELF_EXCLUDED:
                    excluded += 1
                    continue
                if fp.is_file() and should_scan_file(Path(rel)):
                    files.append(fp)
                else:
                    excluded += 1
            return sorted(files), excluded
    except Exception:
        pass

    # Fallback: full filesystem walk
    for filepath in sorted(repo_root.rglob("*")):
        if not filepath.is_file():
            continue
        if filepath.resolve() in _SELF_EXCLUDED:
            excluded += 1
            continue
        if should_scan_file(filepath):
            files.append(filepath)
        else:
            excluded += 1
    return files, excluded


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Security audit for Rex AI Assistant repository.")
    parser.add_argument(
        "--strict-markdown-secrets",
        action="store_true",
        default=False,
        help=(
            "Scan inside Markdown fenced code blocks for secrets and merge "
            "markers. Default behavior skips fenced blocks to reduce false "
            "positives."
        ),
    )
    parser.add_argument(
        "--allowlist",
        action="append",
        default=[],
        metavar="FILE",
        help=(
            "File path exempt from strict-mode fenced-block scanning. May be "
            "repeated. Paths are resolved relative to the repo root."
        ),
    )
    parser.add_argument(
        "--release-gate",
        action="store_true",
        help="Exit nonzero for merge markers, secrets, or actionable source/config findings",
    )
    parser.add_argument(
        "--suppressions",
        metavar="FILE",
        help=(
            "JSON file of narrowly scoped placeholder suppressions with path, line, "
            "reason, and expiry"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run security audit."""
    _configure_text_io()
    args = _parse_args(argv)

    repo_root = Path(__file__).parent.parent

    # Build the allowlist as a set of resolved Paths.
    allowlisted_paths: set[Path] = set()
    for raw in args.allowlist:
        p = Path(raw)
        if not p.is_absolute():
            p = repo_root / p
        allowlisted_paths.add(p.resolve())

    strict = args.strict_markdown_secrets or args.release_gate

    suppressions: set[FindingSuppression] = set()
    suppression_errors: list[str] = []
    if args.suppressions:
        suppression_path = Path(args.suppressions)
        if not suppression_path.is_absolute():
            suppression_path = repo_root / suppression_path
        suppressions, suppression_errors = load_suppressions(suppression_path, repo_root)

    merge_findings: list[str] = []
    placeholder_findings: list[str] = []
    secret_findings: list[str] = []
    files_scanned = 0

    print("Starting security audit...")
    print(f"Scanning: {repo_root}")
    print(f"Excluding dirs: {', '.join(sorted(EXCLUDE_DIRS))}")
    if strict:
        print("Mode: --strict-markdown-secrets enabled")
        if allowlisted_paths:
            print(f"Allowlisted: {', '.join(str(p) for p in sorted(allowlisted_paths))}")
    if args.release_gate:
        print("Release gate: enabled")
    if args.suppressions:
        print(f"Validated suppressions: {len(suppressions)}")
    print()

    scan_files, excluded_count = get_tracked_files(repo_root)

    for filepath in scan_files:
        files_scanned += 1
        merge, placeholder, secret = scan_file(
            filepath,
            strict_markdown_secrets=strict,
            allowlisted_paths=allowlisted_paths,
        )
        merge_findings.extend(merge)
        placeholder_findings.extend(placeholder)
        secret_findings.extend(secret)

    # Report file counts
    print(f"Files scanned:  {files_scanned}")
    print(f"Files excluded: {excluded_count}  (cache dirs, egg-info, untracked, self-excluded)")
    print()

    suppressed_placeholders = 0
    if suppressions:
        unsuppressed: list[str] = []
        for finding in placeholder_findings:
            key = _finding_suppression(finding, "placeholder", repo_root)
            if key in suppressions:
                suppressed_placeholders += 1
            else:
                unsuppressed.append(finding)
        placeholder_findings = unsuppressed

    print("=" * 70)
    print("MERGE CONFLICT MARKERS")
    print("=" * 70)
    if merge_findings:
        print(f"FOUND {len(merge_findings)} issues:")
        for issue in merge_findings[:20]:
            print(f"  {issue}")
        if len(merge_findings) > 20:
            print(f"  ... and {len(merge_findings) - 20} more")
    else:
        print("CLEAN - No merge markers found")
    print()

    print("=" * 70)
    print("PLACEHOLDER/INCOMPLETE CODE MARKERS")
    print("=" * 70)
    actionable = 0
    if placeholder_findings:
        placeholder_findings.sort()
        buckets: dict[str, list[str]] = {
            "source-code": [],
            "configuration": [],
            "documentation": [],
            "needs-review": [],
        }
        for finding in placeholder_findings:
            bucket = classify_placeholder_finding(finding)
            buckets[bucket].append(finding)

        actionable = len(buckets["source-code"]) + len(buckets["configuration"])
        informational = len(buckets["documentation"]) + len(buckets["needs-review"])
        print(
            f"Found {actionable} actionable findings "
            f"({len(buckets['source-code'])} source-code, "
            f"{len(buckets['configuration'])} configuration)"
        )
        print(
            f"Plus {informational} informational findings "
            f"({len(buckets['documentation'])} documentation, "
            f"{len(buckets['needs-review'])} needs-review) — shown below"
        )
        print()

        for heading, label in [
            ("source-code", "SOURCE-CODE (actionable)"),
            ("configuration", "CONFIGURATION (actionable)"),
            ("needs-review", "NEEDS-REVIEW (unknown extension)"),
            ("documentation", "DOCUMENTATION (informational)"),
        ]:
            items = buckets[heading]
            print(f"  --- {label}: {len(items)} ---")
            for issue in items[:20]:
                print(f"    {issue}")
            if len(items) > 20:
                print(f"    ... and {len(items) - 20} more")
            print()
    else:
        print("CLEAN - No placeholder markers found")
    if suppressed_placeholders:
        print(f"Suppressed {suppressed_placeholders} validated placeholder finding(s)")
    print()

    print("=" * 70)
    print("EXPOSED SECRETS")
    print("=" * 70)
    if secret_findings:
        print(f"FOUND {len(secret_findings)} potential secrets:")
        for issue in secret_findings:
            print(f"  {issue}")
    else:
        print("CLEAN - No exposed secrets found")
    print()

    if suppression_errors:
        print("INVALID SUPPRESSIONS:")
        for error in suppression_errors:
            print(f"  {error}")
        print()

    blockers: list[str] = []
    if merge_findings:
        blockers.append(f"{len(merge_findings)} merge marker(s)")
    if secret_findings:
        blockers.append(f"{len(secret_findings)} potential secret(s)")
    if suppression_errors:
        blockers.append(f"{len(suppression_errors)} invalid suppression(s)")
    if args.release_gate and actionable:
        blockers.append(f"{actionable} actionable placeholder finding(s)")

    if blockers:
        print(f"SECURITY GATE FAILED: {', '.join(blockers)}")
        return 1

    if args.release_gate:
        print("SECURITY GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
