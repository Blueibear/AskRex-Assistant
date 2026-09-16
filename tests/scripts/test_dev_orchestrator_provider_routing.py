from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from scripts.dev_orchestrator.openai_budget import (
    OpenAIBudgetLedger,
    OpenAIBudgetPolicyError,
    OpenAIUsage,
)
from scripts.dev_orchestrator.provider_routing import (
    ProviderRoutingInvoker,
    escalation_episode_key,
)
from scripts.dev_orchestrator.routing import ASTRA_MODEL, SOL_MODEL, TERRA_MODEL
from scripts.dev_orchestrator.runner import AgentInvocationError
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.types import AgentResult, OrchestratorConfig, TaskItem, WorkerState


def _result(outcome: str, *, task_id: str = "B-1") -> AgentResult:
    return AgentResult(
        outcome=outcome,
        summary=outcome,
        next_action="next",
        task_id=task_id,
        task_prompt="Continue task" if outcome == "assign" else "",
        role="backend",
    )


class _FakeCli:
    def __init__(self, *, review=None, lead=None) -> None:
        self.calls: list[tuple] = []
        self.review_results = list(review or [_result("pass")])
        self.lead_results = list(lead or [_result("assign")])

    @staticmethod
    def _take(queue):
        value = queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def implement(self, role, state, task, context, model):
        self.calls.append(("implement", role, task.task_id, model))
        return _result("continue", task_id=task.task_id)

    def review(self, role, state, task, context, model):
        self.calls.append(("review", role, task.task_id, model))
        return self._take(self.review_results)

    def lead(self, role, state, context, task=None):
        self.calls.append(("lead", role, task.task_id if task else "", SOL_MODEL))
        return self._take(self.lead_results)


class _FakeOpenAI:
    def __init__(self, *, review=None, lead=None) -> None:
        self.calls: list[tuple] = []
        self.review_results = list(review or [_result("pass")])
        self.lead_results = list(lead or [_result("assign")])

    @staticmethod
    def _take(queue):
        value = queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def review(self, role, state, task, context, model):
        self.calls.append(("review", role, task.task_id, model))
        return self._take(self.review_results)

    def lead(
        self,
        role,
        state,
        task,
        context,
        *,
        phase,
        model,
        budget_purpose="",
        episode_key="",
    ):
        self.calls.append(
            ("lead", phase, task.task_id if task else "", model, budget_purpose, episode_key)
        )
        return self._take(self.lead_results)


def _config(tmp_path: Path, *, enabled: bool = True) -> OrchestratorConfig:
    root = tmp_path / "coordination"
    root.mkdir()
    return replace(
        OrchestratorConfig.default(root),
        openai_worker_enabled=enabled,
        openai_project_id="proj-test" if enabled else "",
        openai_project_hard_limit_confirmed=enabled,
        astra_adjudication_after=4,
    )


def _router(tmp_path: Path, *, cli=None, openai=None, enabled: bool = True):
    config = _config(tmp_path, enabled=enabled)
    cli_worker = cli or _FakeCli()
    openai_worker = openai or (_FakeOpenAI() if enabled else None)
    budget = OpenAIBudgetLedger(config.coordination_root)
    return (
        ProviderRoutingInvoker(
            config,
            cli_invoker=cli_worker,
            openai_worker=openai_worker,
            budget=budget,
        ),
        config,
        cli_worker,
        openai_worker,
        budget,
    )


def _mark_last_provider(config: OrchestratorConfig, role: str, provider: str) -> None:
    AtomicJsonStore(config.coordination_root / "handoff" / f"{role}.json").write(
        {"owner": "supervisor", "last_provider": provider}
    )


def test_implementation_stays_on_cli_path(tmp_path: Path) -> None:
    router, _config, cli, openai, _budget = _router(tmp_path)
    task = TaskItem("B-1", "Implement")

    result = router.implement("backend", WorkerState("backend"), task, "ctx", "sonnet")

    assert result.outcome == "continue"
    assert cli.calls == [("implement", "backend", "B-1", "sonnet")]
    assert openai.calls == []


def test_routine_review_uses_api_terra(tmp_path: Path) -> None:
    router, _config, cli, openai, _budget = _router(tmp_path)
    task = TaskItem("B-1", "Review")

    result = router.review("backend", WorkerState("backend"), task, "ctx", TERRA_MODEL)

    assert result.outcome == "pass"
    assert openai.calls == [("review", "backend", "B-1", TERRA_MODEL)]
    assert cli.calls == []


def test_escalated_review_uses_api_sol(tmp_path: Path) -> None:
    router, _config, cli, openai, _budget = _router(tmp_path)
    task = TaskItem("B-1", "Review")
    state = WorkerState("backend", review_failures=2)

    router.review("backend", state, task, "ctx", SOL_MODEL)

    assert openai.calls == [("review", "backend", "B-1", SOL_MODEL)]
    assert cli.calls == []


def test_codex_implemented_checkpoint_forces_api_sol_review(tmp_path: Path) -> None:
    router, config, cli, openai, _budget = _router(tmp_path)
    _mark_last_provider(config, "backend", "codex")
    task = TaskItem("B-1", "Review")

    router.review("backend", WorkerState("backend"), task, "ctx", TERRA_MODEL)

    assert openai.calls == [("review", "backend", "B-1", SOL_MODEL)]
    assert cli.calls == []


def test_api_review_failure_falls_back_to_matching_codex_model(tmp_path: Path) -> None:
    error = AgentInvocationError("openai", "transient", "temporary")
    openai = _FakeOpenAI(review=[error])
    cli = _FakeCli(review=[_result("pass")])
    router, _config, cli, openai, _budget = _router(tmp_path, cli=cli, openai=openai)
    task = TaskItem("B-1", "Review")

    result = router.review("backend", WorkerState("backend"), task, "ctx", TERRA_MODEL)

    assert result.outcome == "pass"
    assert openai.calls == [("review", "backend", "B-1", TERRA_MODEL)]
    assert cli.calls == [("review", "backend", "B-1", TERRA_MODEL)]


def test_final_verify_bypasses_api_and_forces_codex_sol(tmp_path: Path) -> None:
    router, _config, cli, openai, _budget = _router(tmp_path)
    task = TaskItem("FINAL-VERIFY-backend", "Final review")

    router.review("backend", WorkerState("backend"), task, "ctx", TERRA_MODEL)

    assert openai.calls == []
    assert cli.calls == [("review", "backend", "FINAL-VERIFY-backend", SOL_MODEL)]


def test_planning_uses_api_sol_then_codex_sol_fallback(tmp_path: Path) -> None:
    error = AgentInvocationError("openai", "timeout", "timeout")
    openai = _FakeOpenAI(lead=[error])
    cli = _FakeCli(lead=[_result("assign")])
    router, _config, cli, openai, _budget = _router(tmp_path, cli=cli, openai=openai)

    result = router.lead("backend", WorkerState("backend"), "ctx")

    assert result.outcome == "assign"
    assert openai.calls == [("lead", "plan", "", SOL_MODEL, "", "")]
    assert cli.calls == [("lead", "backend", "", SOL_MODEL)]


def test_disabled_api_uses_codex_sol_for_planning_and_no_astra(tmp_path: Path) -> None:
    cli = _FakeCli(lead=[_result("assign"), _result("failed")])
    router, _config, cli, openai, _budget = _router(tmp_path, cli=cli, enabled=False)
    task = TaskItem("B-1", "Adjudicate")
    state = WorkerState("backend", task=task, task_base_head="abc", implementation_failures=9)

    assert router.lead("backend", WorkerState("backend"), "ctx").outcome == "assign"
    assert router.lead("backend", state, "ctx", task=task).outcome == "failed"
    assert openai is None
    assert cli.calls == [
        ("lead", "backend", "", SOL_MODEL),
        ("lead", "backend", "B-1", SOL_MODEL),
    ]


def test_adjudication_exhausts_sol_chain_before_astra(tmp_path: Path) -> None:
    task = TaskItem("B-1", "Adjudicate")
    state = WorkerState(
        "backend",
        task=task,
        task_base_head="base-123",
        implementation_failures=4,
    )
    openai = _FakeOpenAI(lead=[_result("failed"), _result("assign")])
    cli = _FakeCli(lead=[_result("failed")])
    router, _config, cli, openai, _budget = _router(tmp_path, cli=cli, openai=openai)

    result = router.lead("backend", state, "ctx", task=task)

    episode = escalation_episode_key("backend", task, state)
    assert result.outcome == "assign"
    assert openai.calls == [
        ("lead", "adjudicate", "B-1", SOL_MODEL, "", ""),
        ("lead", "adjudicate", "B-1", ASTRA_MODEL, "astra_adjudication", episode),
    ]
    assert cli.calls == [("lead", "backend", "B-1", SOL_MODEL)]


@pytest.mark.parametrize(
    "outcome",
    ["assign", "done", "blocked_user", "blocked_system", "pass", "changes_required", "continue"],
)
def test_valid_sol_outcome_never_escalates_to_astra(tmp_path: Path, outcome: str) -> None:
    task = TaskItem("B-1", "Adjudicate")
    state = WorkerState("backend", task=task, task_base_head="base", implementation_failures=9)
    openai = _FakeOpenAI(lead=[_result(outcome)])
    router, _config, cli, openai, _budget = _router(tmp_path, openai=openai)

    result = router.lead("backend", state, "ctx", task=task)

    assert result.outcome == outcome
    assert len(openai.calls) == 1
    assert openai.calls[0][3] == SOL_MODEL
    assert cli.calls == []


def test_task_below_astra_threshold_never_uses_astra(tmp_path: Path) -> None:
    task = TaskItem("B-1", "Adjudicate")
    state = WorkerState("backend", task=task, task_base_head="base", implementation_failures=3)
    openai = _FakeOpenAI(lead=[_result("failed")])
    cli = _FakeCli(lead=[_result("failed")])
    router, _config, cli, openai, _budget = _router(tmp_path, cli=cli, openai=openai)

    result = router.lead("backend", state, "ctx", task=task)

    assert result.outcome == "failed"
    assert [call[3] for call in openai.calls] == [SOL_MODEL]
    assert cli.calls == [("lead", "backend", "B-1", SOL_MODEL)]


def test_sol_transport_failure_does_not_itself_justify_astra(tmp_path: Path) -> None:
    task = TaskItem("B-1", "Adjudicate")
    state = WorkerState("backend", task=task, task_base_head="base", implementation_failures=8)
    api_error = AgentInvocationError("openai", "transient", "temporary")
    cli_error = AgentInvocationError("codex", "usage_limit", "limited")
    openai = _FakeOpenAI(lead=[api_error])
    cli = _FakeCli(lead=[cli_error])
    router, _config, _cli, openai, _budget = _router(tmp_path, cli=cli, openai=openai)

    with pytest.raises(AgentInvocationError) as exc:
        router.lead("backend", state, "ctx", task=task)

    assert exc.value.provider == "codex"
    assert exc.value.kind == "usage_limit"
    assert [call[3] for call in openai.calls] == [SOL_MODEL]


def test_prior_astra_attempt_permanently_closes_episode(tmp_path: Path) -> None:
    task = TaskItem("B-1", "Adjudicate")
    state = WorkerState("backend", task=task, task_base_head="base", implementation_failures=8)
    openai = _FakeOpenAI(lead=[_result("failed")])
    cli = _FakeCli(lead=[_result("failed")])
    router, _config, cli, openai, budget = _router(tmp_path, cli=cli, openai=openai)
    episode = escalation_episode_key("backend", task, state)
    reservation = budget.reserve(
        model=ASTRA_MODEL,
        input_token_ceiling=100,
        output_token_ceiling=100,
        purpose="astra_adjudication",
        episode_key=episode,
    )
    budget.mark_uncertain(reservation.reservation_id)

    result = router.lead("backend", state, "ctx", task=task)

    assert result.outcome == "failed"
    assert [call[3] for call in openai.calls] == [SOL_MODEL]
    assert cli.calls == [("lead", "backend", "B-1", SOL_MODEL)]


def test_escalation_episode_key_is_content_free_and_revision_bound() -> None:
    task = TaskItem("B-9", "secret prompt that must not enter the episode key")
    state = WorkerState("backend", task=task, task_base_head="abc123")

    first = escalation_episode_key("backend", task, state)
    second = escalation_episode_key("backend", task, replace(state, task_base_head="def456"))

    assert len(first) == 64
    assert first != second
    assert "secret" not in first
    assert "B-9" not in first


def test_astra_attempt_is_crash_safe_across_budget_ledger_restart(tmp_path: Path) -> None:
    root = tmp_path / "coord"
    root.mkdir()
    ledger = OpenAIBudgetLedger(root)
    episode = "e" * 64
    reservation = ledger.reserve(
        model=ASTRA_MODEL,
        input_token_ceiling=100,
        output_token_ceiling=100,
        purpose="astra_adjudication",
        episode_key=episode,
    )

    assert ledger.has_astra_attempt(episode)
    restarted = OpenAIBudgetLedger(root)
    assert restarted.has_astra_attempt(episode)

    restarted.reconcile(reservation.reservation_id, OpenAIUsage(50, 25))
    assert OpenAIBudgetLedger(root).has_astra_attempt(episode)


def test_non_astra_reservation_does_not_close_episode(tmp_path: Path) -> None:
    root = tmp_path / "coord"
    root.mkdir()
    ledger = OpenAIBudgetLedger(root)
    episode = "f" * 64
    ledger.reserve(
        model=SOL_MODEL,
        input_token_ceiling=100,
        output_token_ceiling=100,
    )

    assert not ledger.has_astra_attempt(episode)


def test_api_sol_error_then_valid_failed_codex_sol_can_escalate_to_astra(tmp_path: Path) -> None:
    task = TaskItem("B-1", "Adjudicate")
    state = WorkerState("backend", task=task, task_base_head="base", implementation_failures=8)
    openai = _FakeOpenAI(
        lead=[AgentInvocationError("openai", "transient", "temporary"), _result("assign")]
    )
    cli = _FakeCli(lead=[_result("failed")])
    router, _config, cli, openai, _budget = _router(tmp_path, cli=cli, openai=openai)

    result = router.lead("backend", state, "ctx", task=task)

    assert result.outcome == "assign"
    assert [call[3] for call in openai.calls] == [SOL_MODEL, ASTRA_MODEL]
    assert cli.calls == [("lead", "backend", "B-1", SOL_MODEL)]


def test_duplicate_astra_reservation_for_episode_is_rejected_atomically(tmp_path: Path) -> None:
    root = tmp_path / "coord"
    root.mkdir()
    ledger = OpenAIBudgetLedger(root)
    episode = "a" * 64
    ledger.reserve(
        model=ASTRA_MODEL,
        input_token_ceiling=100,
        output_token_ceiling=100,
        purpose="astra_adjudication",
        episode_key=episode,
    )

    with pytest.raises(OpenAIBudgetPolicyError, match="already exists"):
        ledger.reserve(
            model=ASTRA_MODEL,
            input_token_ceiling=100,
            output_token_ceiling=100,
            purpose="astra_adjudication",
            episode_key=episode,
        )


def test_api_review_failure_then_codex_usage_limit_preserves_codex_provider(tmp_path: Path) -> None:
    openai = _FakeOpenAI(
        review=[AgentInvocationError("openai", "transient", "temporary API failure")]
    )
    cli = _FakeCli(review=[AgentInvocationError("codex", "usage_limit", "weekly limit")])
    router, _config, _cli, _openai, _budget = _router(
        tmp_path,
        cli=cli,
        openai=openai,
    )
    task = TaskItem("B-1", "Review")

    with pytest.raises(AgentInvocationError) as caught:
        router.review("backend", WorkerState("backend"), task, "ctx", TERRA_MODEL)

    assert caught.value.provider == "codex"
    assert caught.value.kind == "usage_limit"
