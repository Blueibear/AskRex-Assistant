"""Unit tests for scripts/security_audit.py.

Covers merge-marker detection, secret scanning, and false-positive suppression
for Markdown separators, fenced code blocks, and inline backtick patterns.
Also covers --strict-markdown-secrets mode and the allowlist mechanism.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

# Import the audit module directly so we can unit-test scan_file / helpers.
from scripts import security_audit

# ---------------------------------------------------------------------------
# Merge-marker tests
# ---------------------------------------------------------------------------


class TestMergeMarkerDetection:
    """Merge marker regex should only match real Git conflict tokens."""

    def test_real_conflict_marker_detected(self, tmp_path: Path):
        """A genuine merge conflict block must be detected."""
        f = tmp_path / "conflict.py"
        f.write_text(textwrap.dedent("""\
                x = 1
                <<<<<<< HEAD
                y = 2
                =======
                y = 3
                >>>>>>> feature-branch
                z = 4
            """))
        merge, _, _ = security_audit.scan_file(f)
        assert len(merge) == 3, f"Expected 3 merge markers, got {len(merge)}: {merge}"

    def test_markdown_separator_not_flagged(self, tmp_path: Path):
        """Markdown decorative '====...' separators should NOT trigger."""
        f = tmp_path / "doc.md"
        f.write_text(textwrap.dedent("""\
                MERGE CONFLICT MARKERS
                ==================================================
                Some text here
                ========================================
            """))
        merge, _, _ = security_audit.scan_file(f)
        assert merge == [], f"Expected no merge markers, got: {merge}"

    def test_fenced_code_block_marker_not_flagged(self, tmp_path: Path):
        """Merge-like tokens inside fenced code blocks in .md should be skipped."""
        f = tmp_path / "example.md"
        f.write_text(textwrap.dedent("""\
                # Example
                ```
                <<<<<<< HEAD
                =======
                >>>>>>> branch
                ```
            """))
        merge, _, _ = security_audit.scan_file(f)
        assert merge == [], f"Expected no merge markers inside fenced block, got: {merge}"

    def test_equals_only_exact_seven(self, tmp_path: Path):
        """Exactly seven '=' at the start of a line IS a marker; more is not (if extra chars)."""
        f = tmp_path / "test.txt"
        f.write_text("=======\n========extra\n")
        merge, _, _ = security_audit.scan_file(f)
        # First line is exactly "=======" -> match
        # Second line starts with 8+ '=' -> no match (regex requires exactly 7)
        assert len(merge) == 1


# ---------------------------------------------------------------------------
# Secret detection tests
# ---------------------------------------------------------------------------


class TestSecretDetection:
    """Secret scanning should catch real secrets and skip doc patterns."""

    # Build secret-like strings dynamically so the audit script does not
    # flag *this* test file itself as containing secrets.
    _PK_HEADER = "-----BEGIN " + "PRIVATE KEY-----"
    _PK_FOOTER = "-----END " + "PRIVATE KEY-----"
    _RSA_PK_HEADER = "-----BEGIN RSA " + "PRIVATE KEY-----"

    def test_real_private_key_detected(self, tmp_path: Path):
        """An actual private key block must be flagged."""
        f = tmp_path / "key.txt"
        f.write_text(f"{self._PK_HEADER}\nMIIEvQIBADANBg...\n{self._PK_FOOTER}\n")
        _, _, secrets = security_audit.scan_file(f)
        assert len(secrets) >= 1
        assert any("Private key" in s for s in secrets)

    def test_backtick_wrapped_pattern_not_flagged(self, tmp_path: Path):
        """Pattern text wrapped in backticks in a .md file should be skipped."""
        f = tmp_path / "audit_report.md"
        f.write_text(f"- Private keys: `{self._PK_HEADER}`\n")
        _, _, secrets = security_audit.scan_file(f)
        assert secrets == [], f"Expected no secrets for backtick-wrapped pattern, got: {secrets}"

    def test_fenced_block_secret_not_flagged(self, tmp_path: Path):
        """Secret patterns inside Markdown fenced blocks should be skipped."""
        f = tmp_path / "docs.md"
        f.write_text(f"# Patterns we check\n```\n{self._RSA_PK_HEADER}\n```\n")
        _, _, secrets = security_audit.scan_file(f)
        assert secrets == [], f"Expected no secrets in fenced block, got: {secrets}"

    def test_real_openai_key_detected(self, tmp_path: Path):
        """A realistic OpenAI key (sk- followed by 48 chars) must be flagged."""
        fake_key = "sk-" + "A" * 48
        f = tmp_path / "config.py"
        f.write_text(f'OPENAI_KEY = "{fake_key}"\n')
        _, _, secrets = security_audit.scan_file(f)
        assert len(secrets) >= 1
        assert any("OpenAI" in s for s in secrets)


# ---------------------------------------------------------------------------
# Strict-markdown-secrets mode tests
# ---------------------------------------------------------------------------


class TestStrictMarkdownSecrets:
    """--strict-markdown-secrets should detect secrets inside fenced blocks."""

    _PK_HEADER = "-----BEGIN " + "PRIVATE KEY-----"
    _RSA_PK_HEADER = "-----BEGIN RSA " + "PRIVATE KEY-----"

    def test_strict_detects_secret_in_fenced_block(self, tmp_path: Path):
        """In strict mode, a secret inside a Markdown fenced block IS detected."""
        f = tmp_path / "leaked.md"
        f.write_text(f"# Oops\n```\n{self._RSA_PK_HEADER}\n```\n")
        _, _, secrets = security_audit.scan_file(f, strict_markdown_secrets=True)
        assert len(secrets) >= 1, "Strict mode should detect secret in fenced block"
        assert any("Private key" in s for s in secrets)

    def test_strict_detects_openai_key_in_fenced_block(self, tmp_path: Path):
        """In strict mode, an OpenAI key inside a fenced block IS detected."""
        fake_key = "sk-" + "B" * 48
        f = tmp_path / "readme.md"
        f.write_text(f"# Config\n```\nOPENAI_KEY={fake_key}\n```\n")
        _, _, secrets = security_audit.scan_file(f, strict_markdown_secrets=True)
        assert len(secrets) >= 1, "Strict mode should detect OpenAI key in fenced block"
        assert any("OpenAI" in s for s in secrets)

    def test_strict_detects_merge_markers_in_fenced_block(self, tmp_path: Path):
        """In strict mode, merge markers inside fenced blocks ARE detected."""
        f = tmp_path / "conflict.md"
        f.write_text(textwrap.dedent("""\
                # Example
                ```
                <<<<<<< HEAD
                =======
                >>>>>>> branch
                ```
            """))
        merge, _, _ = security_audit.scan_file(f, strict_markdown_secrets=True)
        assert len(merge) == 3, f"Expected 3 merge markers in strict mode, got {len(merge)}"

    def test_default_mode_still_skips_fenced_blocks(self, tmp_path: Path):
        """Without the flag, fenced-block secrets remain suppressed."""
        f = tmp_path / "safe.md"
        f.write_text(f"# Docs\n```\n{self._RSA_PK_HEADER}\n```\n")
        _, _, secrets = security_audit.scan_file(f, strict_markdown_secrets=False)
        assert secrets == [], "Default mode should still skip fenced-block secrets"

    def test_allowlisted_file_skips_fenced_even_in_strict(self, tmp_path: Path):
        """An allowlisted file should skip fenced blocks even in strict mode."""
        f = tmp_path / "allowed.md"
        f.write_text(f"# Docs\n```\n{self._RSA_PK_HEADER}\n```\n")
        _, _, secrets = security_audit.scan_file(
            f,
            strict_markdown_secrets=True,
            allowlisted_paths={f.resolve()},
        )
        assert (
            secrets == []
        ), "Allowlisted file should skip fenced-block secrets even in strict mode"

    def test_non_allowlisted_file_detected_in_strict(self, tmp_path: Path):
        """A non-allowlisted file is scanned inside fenced blocks in strict mode."""
        target = tmp_path / "target.md"
        other = tmp_path / "other.md"
        target.write_text(f"# Leak\n```\n{self._RSA_PK_HEADER}\n```\n")
        # allowlist only 'other', not 'target'
        _, _, secrets = security_audit.scan_file(
            target,
            strict_markdown_secrets=True,
            allowlisted_paths={other.resolve()},
        )
        assert len(secrets) >= 1, "Non-allowlisted file should be scanned in strict mode"

    def test_non_markdown_unaffected_by_strict(self, tmp_path: Path):
        """Strict mode only affects .md files; .py files behave the same."""
        fake_key = "sk-" + "C" * 48
        f = tmp_path / "config.py"
        f.write_text(f'KEY = "{fake_key}"\n')
        _, _, secrets_default = security_audit.scan_file(f, strict_markdown_secrets=False)
        _, _, secrets_strict = security_audit.scan_file(f, strict_markdown_secrets=True)
        assert secrets_default == secrets_strict


# ---------------------------------------------------------------------------
# Placeholder classification tests
# ---------------------------------------------------------------------------


class TestPlaceholderClassification:
    """Tests for the classify_placeholder_finding function."""

    def test_python_file_classified_as_source_code(self):
        finding = "src/app.py:10: # FIXME: handle edge case"
        assert security_audit.classify_placeholder_finding(finding) == "source-code"

    def test_json_file_classified_as_configuration(self):
        finding = "config/settings.json:5: TODO item"
        assert security_audit.classify_placeholder_finding(finding) == "configuration"

    def test_json_under_docs_is_informational(self):
        finding = "docs/archive/audit.json:5: TODO item"
        assert security_audit.classify_placeholder_finding(finding) == "documentation"

    def test_embedded_finding_path_does_not_replace_outer_document_path(self):
        finding = "docs/security/audit.md:57: rex/core.py:106: TODO item"
        assert security_audit.classify_placeholder_finding(finding) == "documentation"

    def test_yaml_file_classified_as_configuration(self):
        finding = "ci.yml:20: # WIP step"
        assert security_audit.classify_placeholder_finding(finding) == "configuration"

    def test_toml_file_classified_as_configuration(self):
        finding = "pyproject.toml:8: # FIXME"
        assert security_audit.classify_placeholder_finding(finding) == "configuration"

    def test_shell_script_classified_as_source_code(self):
        finding = "deploy.sh:3: # TODO finish"
        assert security_audit.classify_placeholder_finding(finding) == "source-code"

    def test_markdown_file_classified_as_documentation(self):
        finding = "README.md:42: Coming soon feature"
        assert security_audit.classify_placeholder_finding(finding) == "documentation"

    def test_txt_file_classified_as_documentation(self):
        finding = "CHANGELOG.txt:10: WIP notes"
        assert security_audit.classify_placeholder_finding(finding) == "documentation"

    def test_unknown_extension_classified_as_needs_review(self):
        finding = "data.csv:1: PLACEHOLDER"
        assert security_audit.classify_placeholder_finding(finding) == "needs-review"

    def test_no_extension_classified_as_needs_review(self):
        finding = "Makefile:5: TODO target"
        assert security_audit.classify_placeholder_finding(finding) == "needs-review"


class TestScanPathExclusions:
    """Tests for directory exclusion logic in should_scan_file."""

    def test_archived_code_is_excluded(self):
        assert not security_audit.should_scan_file(Path("archived/legacy.py"))

    def test_package_lock_skips_placeholder_package_names(self, tmp_path):
        lockfile = tmp_path / "package-lock.json"
        lockfile.write_text('{"name": "cli-truncate"}\n', encoding="utf-8")
        _, placeholders, _ = security_audit.scan_file(lockfile)
        assert placeholders == []

    def test_file_truncate_call_is_not_incomplete_code(self, tmp_path):
        source = tmp_path / "lockfile.py"
        source.write_text("handle.truncate()\n", encoding="utf-8")
        _, placeholders, _ = security_audit.scan_file(source)
        assert placeholders == []

    def test_explicit_truncated_source_marker_is_still_detected(self, tmp_path):
        source = tmp_path / "unfinished.py"
        source.write_text("# TRUNCATED: implementation omitted\n", encoding="utf-8")
        _, placeholders, _ = security_audit.scan_file(source)
        assert len(placeholders) == 1

    def test_ignores_mypy_cache_files(self):
        path = Path(".mypy_cache") / "3.12" / "module.meta.json"
        assert security_audit.should_scan_file(path) is False

    def test_ignores_pytest_cache_files(self):
        path = Path(".pytest_cache") / "v" / "cache" / "lastfailed"
        assert security_audit.should_scan_file(path) is False

    def test_ignores_ruff_cache_files(self):
        path = Path(".ruff_cache") / "0.14.11" / "example.py"
        assert security_audit.should_scan_file(path) is False

    def test_ignores_egg_info_directories(self):
        path = Path("rex_ai_assistant.egg-info") / "PKG-INFO"
        assert security_audit.should_scan_file(path) is False

    def test_ignores_dotclaude_worktrees(self):
        path = Path(".claude") / "worktrees" / "some-branch" / "main.py"
        assert security_audit.should_scan_file(path) is False


class TestPlaceholderBucketOutput:
    """Tests that the audit output shows bucket headings when placeholder findings exist."""

    def test_output_contains_bucket_headings(self):
        """Running the audit on the repo should show bucket headings if any placeholders exist."""
        result = subprocess.run(
            [sys.executable, "scripts/security_audit.py"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        # The repo is expected to have some actionable findings (TODOs etc.)
        if "actionable findings" in result.stdout:
            assert "SOURCE-CODE" in result.stdout or "source-code" in result.stdout
            assert "DOCUMENTATION" in result.stdout or "documentation" in result.stdout
            assert "NEEDS-REVIEW" in result.stdout or "needs-review" in result.stdout

    def test_output_reports_excluded_file_count(self):
        """Output must report the excluded files count separately from scanned count."""
        result = subprocess.run(
            [sys.executable, "scripts/security_audit.py"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert "Files scanned:" in result.stdout
        assert "Files excluded:" in result.stdout

    def test_output_deterministic_ordering(self):
        """Two runs should produce identical output (deterministic sorting)."""
        repo_root = str(Path(__file__).resolve().parent.parent)
        result1 = subprocess.run(
            [sys.executable, "scripts/security_audit.py"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        result2 = subprocess.run(
            [sys.executable, "scripts/security_audit.py"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        assert result1.stdout == result2.stdout


# ---------------------------------------------------------------------------
# End-to-end script execution
# ---------------------------------------------------------------------------


class TestSecurityAuditScript:
    """End-to-end: running the script on the clean repo should exit 0."""

    def test_clean_repo_exits_zero(self):
        """The script must exit 0 when run against the repository root."""
        result = subprocess.run(
            [sys.executable, "scripts/security_audit.py"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 0, (
            f"security_audit.py exited {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


class TestSecurityAuditReleaseGate:
    def test_warning_only_documentation_run_passes(self, tmp_path, monkeypatch, capsys):
        finding = tmp_path / "notes.md"
        finding.write_text("TODO document this later\n", encoding="utf-8")
        monkeypatch.setattr(security_audit, "get_tracked_files", lambda _root: ([finding], 0))

        assert security_audit.main(["--release-gate"]) == 0
        assert "informational findings" in capsys.readouterr().out

    def test_actionable_source_finding_fails_release_gate(self, tmp_path, monkeypatch, capsys):
        finding = tmp_path / "unfinished.py"
        finding.write_text("# TODO implement secure dispatch\n", encoding="utf-8")
        monkeypatch.setattr(security_audit, "get_tracked_files", lambda _root: ([finding], 0))

        assert security_audit.main(["--release-gate"]) == 1
        assert "actionable placeholder" in capsys.readouterr().out

    def test_secret_finding_fails_in_developer_mode(self, tmp_path, monkeypatch):
        finding = tmp_path / "unsafe.py"
        finding.write_text("token = 'sk-" + "A" * 45 + "'\n", encoding="utf-8")
        monkeypatch.setattr(security_audit, "get_tracked_files", lambda _root: ([finding], 0))

        assert security_audit.main([]) == 1

    def test_clean_release_gate_passes(self, monkeypatch, capsys):
        monkeypatch.setattr(security_audit, "get_tracked_files", lambda _root: ([], 0))

        assert security_audit.main(["--release-gate"]) == 0
        assert "SECURITY GATE PASSED" in capsys.readouterr().out

    def test_expired_suppression_is_rejected(self, tmp_path):
        suppression_file = tmp_path / "suppressions.json"
        suppression_file.write_text(
            json.dumps(
                {
                    "suppressions": [
                        {
                            "category": "placeholder",
                            "path": "rex/example.py",
                            "line": 10,
                            "reason": "Tracked upstream issue with an owner",
                            "expires": "2025-01-01",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        valid, errors = security_audit.load_suppressions(suppression_file, tmp_path)
        assert valid == set()
        assert any("expired" in error for error in errors)

    def test_invalid_suppression_path_is_rejected(self, tmp_path):
        suppression_file = tmp_path / "suppressions.json"
        suppression_file.write_text(
            json.dumps(
                {
                    "suppressions": [
                        {
                            "category": "placeholder",
                            "path": "../outside.py",
                            "line": 1,
                            "reason": "This path must never escape the repository",
                            "expires": "2099-01-01",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        valid, errors = security_audit.load_suppressions(suppression_file, tmp_path)
        assert valid == set()
        assert any("within the repository" in error for error in errors)

    def test_strict_mode_clean_repo_exits_zero(self):
        """The script with --strict-markdown-secrets must still exit 0 on a clean repo.

        This validates that existing docs don't contain real leaked secrets in
        fenced blocks.
        """
        result = subprocess.run(
            [sys.executable, "scripts/security_audit.py", "--strict-markdown-secrets"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 0, (
            f"security_audit.py --strict-markdown-secrets exited {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
