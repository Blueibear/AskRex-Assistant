from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import evidence, runner
from scripts.dev_orchestrator.types import TaskItem, WorkerState, WorkerStatus
from scripts.dev_orchestrator.validation import run_iteration_validation
from scripts.dev_orchestrator.worker_context import SuppliedContext
from tests.scripts.test_dev_orchestrator_evidence import _commit, _config, _git_repo

TASK_ID = "backend-evidence-capacity"
SECRET = "sk-capacity-fixture-only"


def _case(tmp_path, monkeypatch, *, diff_chars=70_003, protected=True):
    monkeypatch.setenv("ASKREX_PROVIDER_POLICY", "subscription_only")
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    subprocess.run(["git", "config", "core.abbrev", "40"], cwd=repo, check=True)
    prefix = "BEGIN-CAPACITY\napi_key=" + SECRET + "\n"
    suffix = "\nEND-CAPACITY\n"
    seed = prefix + "X" + suffix
    first_head = _commit(repo, seed)
    first_diff = evidence._git(repo, "diff", f"{base}..{first_head}", "--")
    first_safe = first_diff.replace("api_key=" + SECRET, "[REDACTED]")
    padding = diff_chars - len(first_safe)
    assert padding > 0
    head = _commit(repo, prefix + "X" * (padding + 1) + suffix)
    raw_diff = evidence._git(repo, "diff", f"{base}..{head}", "--")
    expected_diff = raw_diff.replace("api_key=" + SECRET, "[REDACTED]")
    assert len(expected_diff) == diff_chars

    config = _config(tmp_path, repo, [sys.executable, "-c", "print('CAPACITY-GATE-PASS')"])
    issue = "ISSUE-BEGIN\n" + "R" * 20_000 + "\nISSUE-END"
    issues = config.coordination_root / "issues"
    issues.mkdir()
    (issues / "ROADMAP-EVIDENCE-CAPACITY.md").write_text(issue, encoding="utf-8")
    task = TaskItem(TASK_ID, "Review issues/ROADMAP-EVIDENCE-CAPACITY.md without losing evidence.")
    state = WorkerState(
        role="backend",
        status=WorkerStatus.REVIEWING,
        task=task,
        task_base_head=base,
        iteration=13,
        review_failures=7,
    )
    assert run_iteration_validation(config, "backend", TASK_ID, base_head=base, head=head).passed
    shared = "COORDINATION-BEGIN\n" + "C" * 60_000 + "\nCOORDINATION-END"
    context = SuppliedContext(shared, shared) if protected else "plain coordination"
    return SimpleNamespace(
        repo=repo,
        config=config,
        task=task,
        state=state,
        base=base,
        head=head,
        expected_diff=expected_diff,
        context=context,
        issue=issue,
    )


def _bundle(case, **kwargs):
    return evidence.build_review_evidence(
        case.config,
        "backend",
        case.state,
        case.task,
        case.context,
        "capacity-review",
        **kwargs,
    )


def _assert_aggregate_bound(bundle):
    limits = evidence._DEFAULT_SECTION_LIMITS
    headings = sum(len("## " + name + "\n") for name in limits)
    separators = 2 * (len(limits) - 1)
    assert sum(limits.values()) == 228_000
    assert len(bundle.text) <= 228_000 + headings + separators


@pytest.mark.parametrize("protected", [False, True])
def test_complete_70003_character_diff_uses_existing_packet_capacity(
    tmp_path, monkeypatch, protected
):
    case = _case(tmp_path, monkeypatch, protected=protected)
    before = case.state.to_dict()
    bundle = _bundle(case)

    assert bundle.truncated is False
    assert bundle.truncation_reasons == ()
    assert case.expected_diff in bundle.text
    assert case.issue in bundle.text
    assert case.context in bundle.text
    assert "[truncated task_diff]" not in bundle.text
    assert SECRET not in bundle.text
    assert "[REDACTED]" in bundle.text
    assert bundle.base_head == case.base
    assert bundle.head == case.head
    assert case.state.to_dict() == before
    _assert_aggregate_bound(bundle)


def test_true_packet_overflow_still_blocks_review_without_model_dispatch(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, diff_chars=228_001)
    before = case.state.to_dict()
    bundle = _bundle(case)
    assert bundle.truncated is True
    assert {"task_diff", "coordination", "task_issues"} <= set(bundle.truncation_reasons)
    _assert_aggregate_bound(bundle)

    invoker = object.__new__(runner.CliAgentInvoker)
    invoker.config = case.config
    calls = []

    def reject_dispatch(*args, **kwargs):
        calls.append(args)
        pytest.fail("incomplete evidence must not dispatch a model")

    invoker._execute_agent = reject_dispatch
    with pytest.raises(runner.AgentInvocationError, match="task_diff") as caught:
        invoker.review("backend", case.state, case.task, case.context, "gpt-5.6-sol")
    assert caught.value.kind == "incomplete_evidence"
    assert calls == []
    assert case.state.to_dict() == before


def test_explicit_section_override_still_rejects_70003_character_diff(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    bundle = _bundle(case, max_section_chars=70_000)
    assert bundle.truncated is True
    assert "task_diff" in bundle.truncation_reasons
    assert "[truncated task_diff]" in bundle.text
    assert case.issue in bundle.text
    assert case.context in bundle.text
    assert bundle.base_head == case.base
    assert bundle.head == case.head
