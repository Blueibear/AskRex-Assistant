"""US-038: skipped-test inventory classification and drift checks."""

from __future__ import annotations

from scripts import check_skip_inventory as checker


def test_scanner_extracts_skip_decorators_and_runtime_calls(tmp_path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_example.py").write_text(
        """import pytest

@pytest.mark.skipif(True, reason="platform-only")
def test_guarded():
    pass

def test_optional():
    tool = "tool"
    pytest.skip(f"missing {tool}")
""",
        encoding="utf-8",
    )

    sites = checker.scan_skip_sites(tests_root)
    assert [(site.line, site.skip_type, site.reason) for site in sites] == [
        (3, "skipif", "platform-only"),
        (9, "pytest.skip", 'f"missing {tool}"'),
    ]


def test_validator_rejects_circular_temporary_follow_up() -> None:
    site = checker.SkipSite("tests/test_example.py", 3, "skipif", "broken")
    row = checker.InventoryRow(
        path=site.path,
        line=site.line,
        skip_type=site.skip_type,
        reason=site.reason,
        classification="temporary-bug-skip",
        action="fix",
        follow_up="US-038",
    )
    errors = checker.validate_inventory([site], [row])
    assert any("non-circular story ID" in error for error in errors)


def test_validator_rejects_missing_and_stale_rows() -> None:
    actual = checker.SkipSite("tests/test_current.py", 10, "skip", "current")
    stale = checker.InventoryRow(
        path="tests/test_old.py",
        line=5,
        skip_type="skip",
        reason="old",
        classification="retired-surface-skip",
        action="archive",
        follow_up="US-039",
    )
    errors = checker.validate_inventory([actual], [stale])
    assert any("missing inventory row" in error for error in errors)
    assert any("stale inventory row" in error for error in errors)


def test_current_inventory_matches_current_test_tree() -> None:
    sites = checker.scan_skip_sites()
    rows = checker.parse_inventory()
    assert len(sites) == 129
    assert len(rows) == 129
    assert checker.validate_inventory(sites, rows) == []
    assert {row.action for row in rows} == {"keep", "fix"}
    assert all(row.follow_up != "US-038" for row in rows)
