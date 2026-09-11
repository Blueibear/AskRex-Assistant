from __future__ import annotations

from pathlib import Path

from scripts.dev_orchestrator.routing import (
    ASTRA_MODEL,
    TERRA_MODEL,
    select_implementer_model,
    select_reviewer_model,
)
from scripts.dev_orchestrator.runner import (
    build_claude_command,
    build_codex_command,
    classify_cli_failure,
)
from scripts.dev_orchestrator.types import WorkerState


def test_routine_and_escalated_models_are_explicit() -> None:
    routine = WorkerState(role="backend")
    hard_impl = WorkerState(role="backend", implementation_failures=2)
    hard_review = WorkerState(role="backend", review_failures=2)

    assert select_implementer_model(routine) == "sonnet"
    assert select_implementer_model(hard_impl) == "opus"
    assert select_reviewer_model(routine) == TERRA_MODEL
    assert select_reviewer_model(hard_review) == "gpt-5.6-sol"
    assert ASTRA_MODEL == "gpt-6-astra"


def test_codex_commands_override_global_astra_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    review = build_codex_command("review", repo, "Review this task.", TERRA_MODEL)
    lead = build_codex_command("lead", repo, "Select next work.", ASTRA_MODEL)

    assert review[review.index("-m") + 1] == "gpt-5.6-terra"
    assert lead[lead.index("-m") + 1] == "gpt-6-astra"
    assert "read-only" in review
    assert "read-only" in lead
    assert not any("dangerously-bypass" in part for part in review + lead)


def test_claude_command_uses_structured_output_and_safe_permissions(tmp_path: Path) -> None:
    command = build_claude_command(tmp_path / "mobile", "Continue task.", "sonnet")

    assert command[command.index("--model") + 1] == "sonnet"
    assert command[command.index("--permission-mode") + 1] == "auto"
    assert "--output-format" in command
    assert "--json-schema" in command
    assert not any("bypassPermissions" in part for part in command)
    assert not any("dangerously" in part for part in command)


def test_usage_limit_failure_is_classified_without_becoming_success() -> None:
    kind = classify_cli_failure("You have reached your usage limit. Try again later.", 1)
    assert kind == "usage_limit"


def test_auth_and_timeout_failures_are_distinct() -> None:
    assert classify_cli_failure("Please login to continue", 1) == "auth"
    assert classify_cli_failure("process timed out", 124) == "timeout"
    assert classify_cli_failure("unexpected crash", 1) == "failed"


def test_codex_commands_require_output_schema(tmp_path: Path) -> None:
    command = build_codex_command("review", tmp_path / "repo", "Review.", TERRA_MODEL)

    assert "--output-schema" in command
    schema_path = Path(command[command.index("--output-schema") + 1])
    assert schema_path.name == "agent-result.schema.json"


def test_cli_invoker_runs_claude_in_correct_repo_with_coordination_access(tmp_path: Path) -> None:
    import json

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import OrchestratorConfig, TaskItem

    backend = tmp_path / "backend"
    backend.mkdir()
    mobile = tmp_path / "mobile"
    mobile.mkdir()
    coordination = tmp_path / "coordination"
    coordination.mkdir()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    config = OrchestratorConfig(coordination, backend, mobile, frozen, observe_only=False)
    calls = []
    payload = json.dumps(
        {
            "outcome": "continue",
            "summary": "progress",
            "next_action": "continue",
            "needs_user": False,
            "blocker_reason": "",
        }
    )

    def execute(command, cwd, timeout_seconds=1800):
        calls.append((command, cwd))
        return ProcessResult(0, payload, "")

    invoker = CliAgentInvoker(config, execute=execute)
    result = invoker.implement(
        "mobile", WorkerState("mobile"), TaskItem("M-1", "Pair phone"), "context", "sonnet"
    )
    command, cwd = calls[0]
    assert result.outcome == "continue"
    assert cwd == mobile
    assert command[command.index("--model") + 1] == "sonnet"
    assert str(coordination) in command


def test_cli_invoker_uses_codex_for_review_and_astra_for_lead(tmp_path: Path) -> None:
    import json

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import OrchestratorConfig, TaskItem

    backend = tmp_path / "backend"
    backend.mkdir()
    mobile = tmp_path / "mobile"
    mobile.mkdir()
    coordination = tmp_path / "coordination"
    coordination.mkdir()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    config = OrchestratorConfig(coordination, backend, mobile, frozen, observe_only=False)
    calls = []

    def execute(command, cwd, timeout_seconds=1800):
        calls.append((command, cwd))
        outcome = "pass" if "gpt-5.6-terra" in command else "done"
        payload = json.dumps(
            {
                "outcome": outcome,
                "summary": "ok",
                "next_action": "",
                "needs_user": False,
                "blocker_reason": "",
            }
        )
        return ProcessResult(0, payload, "")

    invoker = CliAgentInvoker(config, execute=execute)
    assert (
        invoker.review(
            "backend", WorkerState("backend"), TaskItem("B-1", "Fix"), "ctx", TERRA_MODEL
        ).outcome
        == "pass"
    )
    assert invoker.lead("mobile", WorkerState("mobile"), "ctx").outcome == "done"
    assert calls[0][0][calls[0][0].index("-m") + 1] == TERRA_MODEL
    assert calls[1][0][calls[1][0].index("-m") + 1] == ASTRA_MODEL
    assert calls[0][1] == backend
    assert calls[1][1] == mobile


def test_cli_invoker_raises_typed_usage_failure(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import AgentInvocationError, CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import OrchestratorConfig, TaskItem

    backend = tmp_path / "backend"
    backend.mkdir()
    mobile = tmp_path / "mobile"
    mobile.mkdir()
    coordination = tmp_path / "coordination"
    coordination.mkdir()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    config = OrchestratorConfig(coordination, backend, mobile, frozen, observe_only=False)
    invoker = CliAgentInvoker(
        config, execute=lambda *args, **kwargs: ProcessResult(1, "", "usage limit reached")
    )
    with __import__("pytest").raises(AgentInvocationError) as exc:
        invoker.review(
            "backend", WorkerState("backend"), TaskItem("B-1", "Fix"), "ctx", TERRA_MODEL
        )
    assert exc.value.provider == "codex"
    assert exc.value.kind == "usage_limit"
