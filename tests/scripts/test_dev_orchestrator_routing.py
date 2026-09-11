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
