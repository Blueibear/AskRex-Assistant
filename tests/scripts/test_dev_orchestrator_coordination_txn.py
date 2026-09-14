from __future__ import annotations

from pathlib import Path

import pytest

from scripts.dev_orchestrator.coordination import apply_agent_updates, build_coordination_context
from scripts.dev_orchestrator.schema import validate_agent_result


def _issue(root: Path, issue_id: str = "TEST-009", owner: str = "backend") -> Path:
    (root / "issues").mkdir(parents=True, exist_ok=True)
    path = root / "issues" / f"{issue_id}.md"
    path.write_text(
        f"# {issue_id}\n\nStatus: open\nOwner: {owner}\n\n## Retest\nPending.\n",
        encoding="utf-8",
    )
    return path


def _result(issue_id: str) -> object:
    return validate_agent_result(
        {
            "outcome": "pass",
            "summary": "ready",
            "next_action": "retest",
            "needs_user": False,
            "blocker_reason": "",
            "invocation_id": "coordination-test-invocation",
            "coordination_messages": [
                {
                    "to": "mobile",
                    "priority": "high",
                    "related": issue_id,
                    "needs_response": True,
                    "body": "Contract ready.",
                }
            ],
            "issue_updates": [
                {
                    "issue_id": issue_id,
                    "status": "fixed-needs-retest",
                    "note": "Reviewed and ready for retest.",
                }
            ],
        }
    )


def test_coordination_context_strips_utf8_bom_from_external_markdown(tmp_path: Path) -> None:
    root = tmp_path / "coord"
    (root / "mailbox" / "backend").mkdir(parents=True)
    (root / "PROTOCOL.md").write_text("# Protocol\n", encoding="utf-8")
    (root / "AGENT_BACKEND.md").write_text("Role: backend\n", encoding="utf-8")
    (root / "mailbox" / "backend" / "MSG-bom.md").write_text(
        "# Message\n\nBody.\n",
        encoding="utf-8-sig",
    )

    context = build_coordination_context(root, "backend")

    assert "\ufeff" not in context
    assert "# Message" in context


def test_issue_update_must_match_reviewed_task_and_prevalidate_all_effects(tmp_path: Path) -> None:
    root = tmp_path / "coord"
    issue = _issue(root)
    (root / "mailbox" / "mobile").mkdir(parents=True)
    result = _result("TEST-009")

    with pytest.raises(ValueError, match="active task"):
        apply_agent_updates(root, "backend", result, allow_issue_updates=True, task_id="TEST-008")

    assert list((root / "mailbox" / "mobile").glob("*.md")) == []
    assert "Status: open" in issue.read_text(encoding="utf-8")


def test_issue_update_emits_testing_retest_request_without_mutating_canonical_issue(
    tmp_path: Path,
) -> None:
    root = tmp_path / "coord"
    issue = _issue(root, owner="both")
    (root / "mailbox" / "mobile").mkdir(parents=True)
    result = _result("TEST-009")

    original = issue.read_text(encoding="utf-8")
    apply_agent_updates(root, "backend", result, allow_issue_updates=True, task_id="TEST-009")

    assert issue.read_text(encoding="utf-8") == original
    retests = list((root / "mailbox" / "testing").glob("RETEST-*.md"))
    assert len(retests) == 1
    text = retests[0].read_text(encoding="utf-8")
    assert "TEST-009" in text
    assert "Testing owns the canonical issue status" in text


def test_issue_updates_are_rejected_when_review_phase_did_not_authorize_them(
    tmp_path: Path,
) -> None:
    root = tmp_path / "coord"
    _issue(root)
    (root / "mailbox" / "mobile").mkdir(parents=True)
    result = _result("TEST-009")

    with pytest.raises(ValueError, match="not allowed"):
        apply_agent_updates(root, "backend", result, allow_issue_updates=False, task_id="TEST-009")
    assert list((root / "mailbox" / "mobile").glob("*.md")) == []


def test_active_owned_issues_uses_exact_status_values(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.coordination import active_owned_issues

    root = tmp_path / "coord"
    issue = _issue(root, "TEST-010", owner="backend")
    issue.write_text(
        "# TEST-010\n\nStatus: opened\nOwner: backend\n",
        encoding="utf-8",
    )
    assert active_owned_issues(root, "backend") == []

    issue.write_text(
        "# TEST-010\n\nStatus: fixed-needs-retest\nOwner: backend\n",
        encoding="utf-8",
    )
    assert active_owned_issues(root, "backend") == [issue]


def test_agent_updates_roll_back_partial_mailbox_publication(tmp_path: Path, monkeypatch) -> None:
    from scripts.dev_orchestrator import coordination

    root = tmp_path / "coord"
    _issue(root, owner="both")
    (root / "mailbox" / "mobile").mkdir(parents=True)
    result = _result("TEST-009")
    real_write = coordination.atomic_write_text
    calls = 0

    def fail_second(path: Path, body: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated mailbox failure")
        real_write(path, body)

    monkeypatch.setattr(coordination, "atomic_write_text", fail_second)
    with pytest.raises(OSError, match="simulated mailbox failure"):
        apply_agent_updates(root, "backend", result, allow_issue_updates=True, task_id="TEST-009")

    assert list((root / "mailbox").rglob("RETEST-*.md")) == []
    assert list((root / "mailbox").rglob("MSG-*.md")) == []


def test_supervisor_startup_recovers_abandoned_coordination_transaction(tmp_path: Path) -> None:
    import json

    from scripts.dev_orchestrator.supervisor import Supervisor
    from scripts.dev_orchestrator.types import OrchestratorConfig

    root = tmp_path / "coord"
    destination = root / "mailbox" / "mobile" / "MSG-abandoned.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("partial\n", encoding="utf-8")
    manifest = root / "transactions" / "coordination" / "TXN-abandoned.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {"transaction_id": "abandoned", "destinations": ["mailbox/mobile/MSG-abandoned.md"]}
        ),
        encoding="utf-8",
    )

    Supervisor(OrchestratorConfig(coordination_root=root), object())

    assert not destination.exists()
    assert not manifest.exists()


def test_agent_update_replay_is_idempotent_for_same_invocation(tmp_path: Path) -> None:
    root = tmp_path / "coord"
    _issue(root, owner="both")
    (root / "mailbox" / "mobile").mkdir(parents=True)
    result = _result("TEST-009")

    apply_agent_updates(root, "backend", result, allow_issue_updates=True, task_id="TEST-009")
    apply_agent_updates(root, "backend", result, allow_issue_updates=True, task_id="TEST-009")

    retests = list((root / "mailbox" / "testing").glob("RETEST-*.md"))
    messages = list((root / "mailbox" / "mobile").glob("MSG-*.md"))
    assert len(retests) == 1
    assert len(messages) == 1
    assert "Invocation: coordination-test-invocation" in retests[0].read_text(encoding="utf-8")
    assert "Invocation: coordination-test-invocation" in messages[0].read_text(encoding="utf-8")
