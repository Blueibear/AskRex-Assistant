from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import claude_cloud, cli, runner
from scripts.dev_orchestrator.claude_cloud import ClaudeCloudPoll, ClaudeCloudSession
from scripts.dev_orchestrator.openai_budget import OpenAIBudgetLedger
from scripts.dev_orchestrator.openai_transport import (
    OpenAIResponsesTransport,
    OpenAITransportError,
    PreparedOpenAIRequest,
)
from scripts.dev_orchestrator.openai_worker import OpenAIModelWorker
from scripts.dev_orchestrator.provider_routing import (
    ProviderRoutingInvoker,
    temporary_fable_policy_active,
    temporary_fable_review_active,
)
from scripts.dev_orchestrator.routing import TERRA_MODEL, validate_openai_policy
from scripts.dev_orchestrator.runner import AgentInvocationError, CliAgentInvoker
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.types import AgentResult, OrchestratorConfig, TaskItem, WorkerState
from tests.scripts.test_dev_orchestrator_provider_routing import _FakeCli, _FakeOpenAI

POLICY_ENV = "ASKREX_PROVIDER_POLICY"


def _unexpected(*_args, **_kwargs):
    raise AssertionError("forbidden provider effect was reached")


def _config(tmp_path):
    return replace(
        OrchestratorConfig.default(tmp_path / "coordination"),
        backend_root=tmp_path / "backend",
        mobile_root=tmp_path / "mobile",
        frozen_worktree=tmp_path / "frozen",
        openai_worker_enabled=True,
        openai_project_id="project-fixture",
        openai_project_hard_limit_confirmed=True,
        openai_astra_enabled=True,
        claude_cloud_mode="preferred",
        metadata={
            "temporary_fable_review": {
                "enabled": True,
                "reason": "openai_weekly_usage_limit",
                "model": "fable",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "roles": ["backend", "mobile"],
                "allow_codex_equivalent_reviews": True,
            }
        },
    )


@pytest.mark.parametrize("value", ["subscription_only", "subscription_onyl", "SUBSCRIPTION_ONLY"])
def test_paid_reservation_is_blocked_before_ledger_mutation(tmp_path, monkeypatch, value):
    monkeypatch.setenv(POLICY_ENV, value)
    budget = OpenAIBudgetLedger(tmp_path / "coordination")

    with pytest.raises(ValueError, match=POLICY_ENV):
        budget.reserve(model=TERRA_MODEL, input_token_ceiling=10, output_token_ceiling=10)

    assert not budget.coordination_root.exists()


@pytest.mark.parametrize("value", ["subscription_only", "unexpected"])
def test_api_send_is_blocked_before_credentials_or_network(monkeypatch, value):
    transport = OpenAIResponsesTransport(
        project_id="project-fixture", credential_resolver=_unexpected, opener=_unexpected
    )
    prepared = PreparedOpenAIRequest(b"{}", 2, TERRA_MODEL, 10)
    monkeypatch.setenv(POLICY_ENV, value)

    with pytest.raises((ValueError, OpenAITransportError), match=POLICY_ENV):
        transport.send(prepared)


@pytest.mark.parametrize("phase", ["review", "plan", "adjudicate", "dispatch"])
def test_existing_api_worker_cannot_reserve_send_or_prepare_under_policy(
    tmp_path, monkeypatch, phase
):
    monkeypatch.delenv(POLICY_ENV, raising=False)
    worker = OpenAIModelWorker(
        _config(tmp_path),
        transport=SimpleNamespace(prepare=_unexpected, send=_unexpected),
        budget=SimpleNamespace(reserve=_unexpected),
    )
    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    task = TaskItem("B-POLICY", "Review the existing change")
    state = WorkerState("backend", task=task, task_base_head="a" * 40)

    with pytest.raises(AgentInvocationError, match="subscription_only") as error:
        if phase == "review":
            worker.review("backend", state, task, "context", TERRA_MODEL)
        elif phase == "dispatch":
            worker._dispatch(
                prepared=SimpleNamespace(input_token_ceiling=10, max_output_tokens=10),
                model=TERRA_MODEL,
            )
        else:
            worker.lead(
                "backend",
                state,
                task if phase == "adjudicate" else None,
                "context",
                phase=phase,
                model=TERRA_MODEL,
            )

    assert error.value.kind == "policy"
    assert not worker.config.coordination_root.exists()


def test_api_worker_construction_rejects_stale_enabled_flag(tmp_path, monkeypatch):
    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    with pytest.raises((ValueError, AgentInvocationError), match="subscription_only"):
        OpenAIModelWorker(_config(tmp_path), transport=SimpleNamespace(), budget=SimpleNamespace())


@pytest.mark.parametrize("phase", ["review", "plan", "adjudicate"])
def test_stale_api_flags_do_not_enable_router_fallbacks(tmp_path, monkeypatch, phase):
    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    failure = AgentInvocationError("codex", "usage_limit", "subscription limit")
    local = _FakeCli(review=[failure], lead=[failure])
    paid = _FakeOpenAI()
    config = _config(tmp_path)
    router = ProviderRoutingInvoker(config, cli_invoker=local, openai_worker=paid)
    task = TaskItem("B-POLICY", "Review")
    state = WorkerState("backend", task=task)

    with pytest.raises(AgentInvocationError) as error:
        if phase == "review":
            router.review("backend", state, task, "context", TERRA_MODEL)
        else:
            router.lead("backend", state, "context", task=task if phase == "adjudicate" else None)

    assert error.value is failure
    assert len(local.calls) == 1
    assert paid.calls == []
    assert not config.coordination_root.exists()


def test_failed_subscription_adjudication_cannot_delegate_to_api_or_astra(tmp_path, monkeypatch):
    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    fallback = AgentResult("failed", "unresolved", "keep task")
    paid = _FakeOpenAI()
    router = ProviderRoutingInvoker(
        _config(tmp_path),
        cli_invoker=_FakeCli(lead=[fallback]),
        openai_worker=paid,
        budget=SimpleNamespace(has_astra_attempt=_unexpected),
    )
    task = TaskItem("B-POLICY", "Review")
    state = WorkerState("backend", task=task, review_failures=10)

    assert router.lead("backend", state, "context", task=task) is fallback
    assert router._maybe_astra("backend", state, task, "context", fallback) is fallback
    assert paid.calls == []


def test_stale_fable_flags_cannot_select_or_delegate_fable(tmp_path, monkeypatch):
    config = _config(tmp_path)
    task = TaskItem("B-ROUTINE", "Change the layout")
    local, paid = _FakeCli(), _FakeOpenAI()
    router = ProviderRoutingInvoker(config, cli_invoker=local, openai_worker=paid)
    monkeypatch.setenv(POLICY_ENV, "subscription_only")

    assert temporary_fable_policy_active(config, "backend") is False
    assert temporary_fable_review_active(config, "backend", task, "claude") is False
    with pytest.raises(AgentInvocationError, match="subscription_only"):
        router.review("backend", WorkerState("backend"), task, "context", "fable")
    assert local.calls == []
    assert paid.calls == []


@pytest.mark.parametrize(
    "entry", ["review", "claude_command", "sandbox_command", "codex_command", "execute"]
)
def test_direct_fable_entries_are_blocked_before_invocation(tmp_path, monkeypatch, entry):
    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    invoker = CliAgentInvoker(_config(tmp_path))
    monkeypatch.setattr(runner, "run_command", _unexpected)
    monkeypatch.setattr(runner, "build_review_evidence", _unexpected)

    with pytest.raises((ValueError, AgentInvocationError), match="subscription_only"):
        if entry == "review":
            invoker.review(
                "backend", WorkerState("backend"), TaskItem("B-1", "Review"), "", "fable"
            )
        elif entry == "claude_command":
            runner.build_claude_command(tmp_path, "prompt", "fable")
        elif entry == "sandbox_command":
            runner.build_claude_sandbox_command(
                tmp_path, "prompt", "fable", container_name="fixture", use_oauth_token_env=True
            )
        elif entry == "codex_command":
            runner.build_codex_command("review", tmp_path, "prompt", "fable")
        else:
            invoker._execute_agent(
                ["claude", "--model", "fable", "prompt"],
                tmp_path,
                "backend",
                "claude",
                "inv-fixture",
            )


def test_subscription_factory_ignores_unused_api_prerequisites_and_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    config = replace(
        _config(tmp_path), openai_project_id="", openai_project_hard_limit_confirmed=False
    )
    monkeypatch.setattr(cli, "CredentialManager", _unexpected)
    monkeypatch.setattr(cli, "OpenAIModelWorker", _unexpected)

    validate_openai_policy(config)
    cli.save_config(config)
    loaded = cli.load_config(config.coordination_root)
    assert loaded.openai_worker_enabled is True
    invoker = cli._build_active_invoker(loaded)
    assert isinstance(invoker, CliAgentInvoker)


@pytest.mark.parametrize("value", ["typo", "configured", " subscription_only "])
def test_unknown_deployment_policy_fails_closed_at_factory(tmp_path, monkeypatch, value):
    monkeypatch.setenv(POLICY_ENV, value)
    with pytest.raises(ValueError, match=POLICY_ENV):
        cli._build_active_invoker(replace(_config(tmp_path), openai_worker_enabled=False))


def test_api_enable_command_cannot_write_or_load_credentials_under_policy(tmp_path, monkeypatch):
    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    monkeypatch.setattr(cli, "load_config", _unexpected)
    with pytest.raises(ValueError, match="subscription_only"):
        cli.set_openai_worker_enabled(tmp_path / "coordination", True)
    assert not (tmp_path / "coordination").exists()


@pytest.mark.parametrize("entry", ["launch", "interactive", "context", "dispatcher"])
def test_cloud_fresh_work_is_blocked_before_git_or_worker_dispatch(tmp_path, monkeypatch, entry):
    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    config = _config(tmp_path)
    task = TaskItem("B-CLOUD", "Implement the change")
    monkeypatch.setattr(claude_cloud, "_repo_for_role", _unexpected)
    monkeypatch.setattr(claude_cloud, "_claude_executable", _unexpected)
    monkeypatch.setattr(runner, "launch_cloud_session", _unexpected)

    with pytest.raises(
        (ValueError, claude_cloud.ClaudeCloudError, AgentInvocationError), match="subscription_only"
    ):
        if entry == "launch":
            claude_cloud.launch_cloud_session(
                config, role="backend", task=task, execute=_unexpected
            )
        elif entry == "interactive":
            claude_cloud._launch_cloud_interactive(
                coordination_root=config.coordination_root, worktree=tmp_path, prompt="prompt"
            )
        elif entry == "context":
            claude_cloud.deliver_cloud_context(
                config, "backend", task, "new instructions", execute=_unexpected
            )
        else:
            CliAgentInvoker(config)._dispatch_cloud_implementation("backend", task, "context")
    assert not config.coordination_root.exists()


@pytest.mark.parametrize("status", ["running", "awaiting_context", "context_delivery_failed"])
@pytest.mark.parametrize("completed", [False, True])
def test_existing_cloud_poll_and_adoption_remain_available_without_new_messages(
    tmp_path, monkeypatch, status, completed
):
    config = _config(tmp_path)
    task = TaskItem("B-CLOUD", "Implement the change")
    session = ClaudeCloudSession(
        "backend",
        task.task_id,
        "session_fixture",
        "https://claude.ai/code/session_fixture",
        "ralph/cloud/backend/fixture",
        "a" * 40,
        datetime.now(UTC).isoformat(),
        status=status,
    )
    path = config.coordination_root / "cloud-sessions" / "backend.json"
    AtomicJsonStore(path).write(session.to_dict())
    before = path.read_bytes()
    calls = []

    def poll(*_args):
        calls.append("poll")
        return ClaudeCloudPoll("ready_for_review" if completed else "running", "b" * 40, "subject")

    def adopt(*_args):
        calls.append("adopt")
        return AgentResult("ready_for_review", "adopted", "validate")

    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    monkeypatch.setattr(runner, "deliver_cloud_context", _unexpected)
    monkeypatch.setattr(runner, "launch_cloud_session", _unexpected)
    monkeypatch.setattr(runner, "poll_cloud_session", poll)
    monkeypatch.setattr(runner, "adopt_cloud_session", adopt)
    result = CliAgentInvoker(config).poll_existing_cloud(
        "backend", task, "do not send this context"
    )
    assert result.outcome == ("ready_for_review" if completed else "continue")
    assert calls == (["poll", "adopt"] if completed else ["poll"])
    assert path.read_bytes() == before


def test_unset_policy_keeps_authorized_configured_routing(tmp_path, monkeypatch):
    monkeypatch.delenv(POLICY_ENV, raising=False)
    config = _config(tmp_path)
    local, paid = _FakeCli(), _FakeOpenAI()
    router = ProviderRoutingInvoker(config, cli_invoker=local, openai_worker=paid)
    task = TaskItem("B-ROUTINE", "Change layout")
    assert temporary_fable_policy_active(config, "backend") is True
    assert router.review("backend", WorkerState("backend"), task, "", "fable").outcome == "pass"
    assert local.calls[0][-1] == "fable"

    budget = OpenAIBudgetLedger(config.coordination_root)
    reservation = budget.reserve(model=TERRA_MODEL, input_token_ceiling=1, output_token_ceiling=1)
    assert reservation.reservation_id


def test_subscription_policy_keeps_normal_cli_work_available(tmp_path, monkeypatch):
    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    local, paid = _FakeCli(), _FakeOpenAI()
    router = ProviderRoutingInvoker(_config(tmp_path), cli_invoker=local, openai_worker=paid)
    task = TaskItem("B-ROUTINE", "Implement the change")
    state = WorkerState("backend", task=task)

    assert router.implement("backend", state, task, "", "sonnet").outcome == "continue"
    assert router.review("backend", state, task, "", TERRA_MODEL).outcome == "pass"
    assert router.lead("backend", state, "").outcome == "assign"
    assert paid.calls == []


def test_subscription_policy_preserves_expired_mismatched_cloud_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    config = _config(tmp_path)
    session = ClaudeCloudSession(
        "backend",
        "B-OLD",
        "session_fixture",
        "https://claude.ai/code/session_fixture",
        "ralph/cloud/backend/fixture",
        "a" * 40,
        (datetime.now(UTC) - timedelta(days=2)).isoformat(),
    )
    path = config.coordination_root / "cloud-sessions" / "backend.json"
    AtomicJsonStore(path).write(session.to_dict())
    before = path.read_bytes()
    invoker = CliAgentInvoker(config)
    monkeypatch.setattr(invoker, "_execute_agent", _unexpected)

    with pytest.raises(AgentInvocationError, match="belongs to B-OLD"):
        invoker.implement(
            "backend", WorkerState("backend"), TaskItem("B-NEW", "Implement new work"), "", "sonnet"
        )

    assert path.read_bytes() == before


def test_retired_temporary_overrides_cannot_replace_codex_planning_or_review(tmp_path, monkeypatch):
    from scripts.dev_orchestrator import review_fallback
    from scripts.dev_orchestrator.provider_routing import temporary_claude_planning_active

    policy = {
        "enabled": True,
        "reason": "openai_weekly_usage_limit",
        "roles": ["backend", "mobile"],
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    base = _config(tmp_path)
    config = replace(
        base,
        metadata={
            **base.metadata,
            "temporary_claude_planning": policy,
            "review_fallback": policy,
        },
    )
    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    monkeypatch.setattr(review_fallback, "load_validation_receipt", _unexpected)
    local, paid = _FakeCli(), _FakeOpenAI()
    router = ProviderRoutingInvoker(config, cli_invoker=local, openai_worker=paid)
    task = TaskItem("B-ROUTINE", "Review layout")
    state = WorkerState("backend", task=task, task_base_head="a" * 40)

    assert temporary_claude_planning_active(config, "backend") is False
    assert review_fallback.fallback_active(config) is False
    assert (
        review_fallback.load_hourly_review(
            config, "backend", state, head="a" * 40, implementation_provider="claude"
        )
        is None
    )
    assert router.lead("backend", state, "context").outcome == "assign"
    assert [call[0] for call in local.calls] == ["lead"]
    assert paid.calls == []


@pytest.mark.parametrize("entry", ["claude_plan", "execute_plan", "execute_review"])
def test_retired_direct_claude_delegation_is_blocked_before_invocation(
    tmp_path, monkeypatch, entry
):
    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    invoker = CliAgentInvoker(_config(tmp_path))
    monkeypatch.setattr(runner, "run_command", _unexpected)

    with pytest.raises((ValueError, AgentInvocationError), match="subscription_only"):
        if entry == "claude_plan":
            invoker.claude_plan("backend", WorkerState("backend"), "context")
        else:
            invoker._execute_agent(
                ["claude", "--model", "sonnet", "prompt"],
                tmp_path,
                "backend",
                "claude",
                "inv-fixture",
                phase=entry.removeprefix("execute_"),
            )
    assert not invoker.config.coordination_root.exists()


@pytest.mark.parametrize("kind", ["usage_limit", "auth"])
def test_subscription_policy_preserves_claude_implementation_fallback(tmp_path, monkeypatch, kind):
    monkeypatch.setenv(POLICY_ENV, "subscription_only")
    invoker = CliAgentInvoker(_config(tmp_path))
    providers = []

    def execute(_command, _repo, _role, provider, _invocation_id, **kwargs):
        providers.append(provider)
        assert kwargs["phase"] == "implement"
        if provider == "codex":
            raise AgentInvocationError("codex", kind, "subscription limit")
        return runner.ProcessResult(
            0,
            '{"outcome":"continue","summary":"working","next_action":"continue","needs_user":false,"blocker_reason":""}',
            "",
        )

    monkeypatch.setattr(invoker, "_execute_agent", execute)
    task = TaskItem("B-IMPLEMENT", "Implement the change")
    result = invoker.implement("backend", WorkerState("backend"), task, "", "sonnet")
    assert result.outcome == "continue"
    assert providers == ["codex", "claude"]
