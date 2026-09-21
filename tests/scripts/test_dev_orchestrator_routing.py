from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.dev_orchestrator.routing import (
    ASTRA_MODEL,
    SOL_MODEL,
    TERRA_MODEL,
    select_implementer_model,
    select_reviewer_model,
)
from scripts.dev_orchestrator.runner import (
    _codex_task_prompt,
    _lead_prompt,
    _review_prompt,
    build_claude_command,
    build_codex_command,
    classify_cli_failure,
)
from scripts.dev_orchestrator.types import TaskItem, WorkerState


def _safe_config(tmp_path: Path):
    from scripts.dev_orchestrator.cli import initialize_runtime
    from scripts.dev_orchestrator.handoff import acknowledge_handoff
    from tests.scripts.test_dev_orchestrator_safety import _git_repo

    root = tmp_path / "coordination"
    root.mkdir()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(root, backend, mobile, frozen)
    acknowledge_handoff(config, "backend", source="test")
    acknowledge_handoff(config, "mobile", source="test")
    return config, backend, mobile


def test_codex_task_prompt_defers_supervisor_owned_evidence() -> None:
    task = TaskItem(
        "MOBILE-1",
        "Finish mobile contract sync",
        feedback=(
            "Supply complete git diff, shared coordination check output, and validation command output."
        ),
    )
    prompt = _codex_task_prompt(
        "mobile",
        task,
        Path(r"C:\coordination"),
        "bounded coordination context",
        "inv-1",
    )

    assert "supervisor-owned artifacts" in prompt
    assert "Git diff" in prompt
    assert "shared coordination" in prompt
    assert "untracked dependencies" in prompt
    assert "return ready_for_review" in prompt
    assert "do not report blocked_system" in prompt


def test_routine_and_escalated_models_are_explicit() -> None:
    routine = WorkerState(role="backend")
    hard_impl = WorkerState(role="backend", implementation_failures=2)
    hard_review = WorkerState(role="backend", review_failures=2)

    assert select_implementer_model(routine) == "sonnet"
    assert select_implementer_model(hard_impl) == "opus"
    assert select_reviewer_model(routine) == TERRA_MODEL
    assert select_reviewer_model(hard_review) == "gpt-5.6-sol"
    assert ASTRA_MODEL == "gpt-6-astra"


def test_openai_policy_validator_fails_closed() -> None:
    from dataclasses import replace
    from decimal import Decimal

    from scripts.dev_orchestrator.routing import validate_openai_policy
    from scripts.dev_orchestrator.types import OrchestratorConfig

    config = OrchestratorConfig.default(Path("."))
    validate_openai_policy(config)

    invalid = (
        replace(config, openai_monthly_budget_usd=Decimal("30.01")),
        replace(config, openai_review_model="gpt-unknown"),
        replace(config, openai_timeout_seconds=0),
        replace(config, openai_max_input_chars=0),
        replace(config, openai_max_output_tokens=0),
        replace(config, openai_max_calls_per_cycle=0),
        replace(config, openai_max_astra_calls_per_escalation=2),
        replace(config, openai_worker_enabled=True),
        replace(
            config,
            openai_worker_enabled=True,
            openai_project_id="proj-ralph",
            openai_project_hard_limit_confirmed=False,
        ),
    )
    for candidate in invalid:
        with pytest.raises(ValueError):
            validate_openai_policy(candidate)

    validate_openai_policy(
        replace(
            config,
            openai_worker_enabled=True,
            openai_project_id="proj-ralph",
            openai_project_hard_limit_confirmed=True,
        )
    )


def test_codex_commands_override_global_astra_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    review = build_codex_command("review", repo, "Review this task.", TERRA_MODEL)
    lead = build_codex_command("lead", repo, "Select next work.", ASTRA_MODEL)

    assert review[review.index("-m") + 1] == "gpt-5.6-terra"
    assert lead[lead.index("-m") + 1] == "gpt-6-astra"
    assert "read-only" in review
    assert "read-only" in lead
    assert not any("dangerously-bypass" in part for part in review + lead)


def test_review_prompt_keeps_validation_execution_outside_read_only_reviewer(
    tmp_path: Path,
) -> None:
    task = TaskItem("S35-TEST", "Validate the current checkpoint.")

    prompt = _review_prompt("backend", task, tmp_path, "bounded context", "inv-123")

    assert "deterministic supervisor validation" in prompt.lower()
    assert "authoritative bounded review evidence" in prompt.lower()
    assert "current matching validation receipt" in prompt.lower()
    assert "do not demand unrelated gates" in prompt.lower()
    assert "do not use terminal commands merely to re-read facts" in prompt.lower()
    assert "do not rerun" in prompt.lower()
    assert "pytest" in prompt.lower()
    assert "read-only" in prompt.lower()
    assert "already shows fixed-needs-retest" in prompt.lower()


def test_claude_command_uses_structured_output_and_safe_permissions(tmp_path: Path) -> None:
    command = build_claude_command(tmp_path / "mobile", "Continue task.", "sonnet")

    assert command[command.index("--model") + 1] == "sonnet"
    assert command[command.index("--permission-mode") + 1] == "acceptEdits"
    assert "--output-format" in command
    assert "--json-schema" in command
    assert not any("bypassPermissions" in part for part in command)
    assert not any("dangerously" in part for part in command)


def test_claude_schema_payload_omits_meta_schema_declaration(tmp_path: Path) -> None:
    import json

    from scripts.dev_orchestrator import runner

    command = build_claude_command(tmp_path / "mobile", "Continue task.", "sonnet")
    payload = json.loads(command[command.index("--json-schema") + 1])
    canonical = json.loads(runner._SCHEMA_PATH.read_text(encoding="utf-8"))

    assert "$schema" in canonical
    assert "$schema" not in payload
    assert payload["type"] == canonical["type"]
    assert payload["properties"] == canonical["properties"]
    assert "allOf" not in payload
    assert "allOf" in canonical


def test_usage_limit_failure_is_classified_without_becoming_success() -> None:
    kind = classify_cli_failure("You have reached your usage limit. Try again later.", 1)
    assert kind == "usage_limit"


def test_claude_session_limit_429_is_classified_as_usage_limit() -> None:
    output = (
        '{"is_error":true,"api_error_status":429,'
        '"result":"You have hit your session limit; resets 3:50pm (UTC)"}'
    )
    assert classify_cli_failure(output, 1) == "usage_limit"


def test_auth_and_timeout_failures_are_distinct() -> None:
    assert classify_cli_failure("Please login to continue", 1) == "auth"
    assert (
        classify_cli_failure(
            "Failed to authenticate. API Error: 401 OAuth access token has expired. Re-authenticate to continue.",
            1,
        )
        == "auth"
    )
    assert classify_cli_failure("process timed out", 124) == "timeout"
    assert classify_cli_failure("unexpected crash", 1) == "failed"


def test_runner_pipe_timeout_takes_precedence_over_incidental_auth_noise() -> None:
    output = (
        "skill loader warning mentions authentication metadata\n"
        "CreateProcess: Failed to create unified exec process: "
        "timed out after 15000ms connecting runner pipe-in"
    )

    assert classify_cli_failure(output, 1) == "timeout"


def test_codex_runner_pipe_failure_resets_windows_sandbox_before_retry(monkeypatch) -> None:
    from scripts.dev_orchestrator import runner

    resets: list[bool] = []
    monkeypatch.setattr(
        runner,
        "recover_codex_windows_terminal_runner",
        lambda: resets.append(True) or True,
    )
    invoker = object.__new__(runner.CliAgentInvoker)
    result = runner.ProcessResult(
        1,
        "",
        "CreateProcess: Failed to create unified exec process: "
        "timed out after 15000ms connecting runner pipe-in",
    )

    with pytest.raises(runner.AgentInvocationError) as exc:
        invoker._finish("codex", result)

    assert exc.value.kind == "timeout"
    assert resets == [True]


def test_windows_runner_recovery_resets_current_and_legacy_helpers(monkeypatch) -> None:
    from scripts.dev_orchestrator import runner

    calls: list[tuple[str, ...]] = []

    class Completed:
        returncode = 0

    monkeypatch.setattr(runner.os, "name", "nt")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(tuple(command)) or Completed(),
    )
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    assert runner.recover_codex_windows_terminal_runner() is True
    assert ("taskkill", "/IM", "codex-command-runner.exe", "/F") in calls
    assert ("taskkill", "/IM", "codex-windows-sandbox-service.exe", "/F") in calls


def test_codex_commands_require_output_schema(tmp_path: Path) -> None:
    command = build_codex_command("review", tmp_path / "repo", "Review.", TERRA_MODEL)

    assert "--output-schema" in command
    schema_path = Path(command[command.index("--output-schema") + 1])
    assert schema_path.name == "agent-result.codex.schema.json"


def test_codex_output_schema_avoids_unsupported_conditionals(tmp_path: Path) -> None:
    import json

    command = build_codex_command("review", tmp_path / "repo", "Review.", TERRA_MODEL)
    schema_path = Path(command[command.index("--output-schema") + 1])
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert "allOf" not in schema
    assert set(schema["required"]) == set(schema["properties"])


def test_cli_invoker_runs_claude_in_correct_repo_with_coordination_access(tmp_path: Path) -> None:
    import json

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    config, backend, mobile = _safe_config(tmp_path)
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

    invoker = CliAgentInvoker(config, execute=execute, allow_test_executor=True)
    result = invoker.implement(
        "mobile", WorkerState("mobile"), TaskItem("M-1", "Pair phone"), "context", "sonnet"
    )
    command, cwd = calls[0]
    assert result.outcome == "continue"
    assert cwd != mobile
    assert cwd.name == "repo"
    assert command[command.index("--model") + 1] == "sonnet"
    assert "--add-dir" not in command
    assert "context" in command[-1]


def test_cli_invoker_uses_codex_for_review_and_sol_for_lead(tmp_path: Path) -> None:
    import json

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem

    config, backend, mobile = _safe_config(tmp_path)
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

    invoker = CliAgentInvoker(config, execute=execute, allow_test_executor=True)
    assert (
        invoker.review(
            "backend", WorkerState("backend"), TaskItem("B-1", "Fix"), "ctx", TERRA_MODEL
        ).outcome
        == "pass"
    )
    assert invoker.lead("mobile", WorkerState("mobile"), "ctx").outcome == "done"
    assert calls[0][0][calls[0][0].index("-m") + 1] == TERRA_MODEL
    assert calls[1][0][calls[1][0].index("-m") + 1] == SOL_MODEL
    assert calls[0][1].resolve() != backend.resolve()
    assert calls[1][1].resolve() != mobile.resolve()
    assert str(backend.resolve()) not in calls[0][0]
    assert str(mobile.resolve()) not in calls[1][0]


def test_cli_invoker_raises_typed_usage_failure(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import AgentInvocationError, CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem

    config, backend, mobile = _safe_config(tmp_path)
    invoker = CliAgentInvoker(
        config,
        execute=lambda *args, **kwargs: ProcessResult(1, "", "usage limit reached"),
        allow_test_executor=True,
    )
    with __import__("pytest").raises(AgentInvocationError) as exc:
        invoker.review(
            "backend", WorkerState("backend"), TaskItem("B-1", "Fix"), "ctx", TERRA_MODEL
        )
    assert exc.value.provider == "codex"
    assert exc.value.kind == "usage_limit"


def test_implementation_falls_back_to_codex_when_claude_is_usage_limited(tmp_path: Path) -> None:
    import json

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    config, _backend, _mobile = _safe_config(tmp_path)
    calls: list[list[str]] = []

    def execute(command, cwd, timeout_seconds=1800):
        calls.append(command)
        if len(calls) == 1:
            return ProcessResult(1, "", "You've hit your session limit")
        return ProcessResult(
            0,
            json.dumps(
                {
                    "outcome": "continue",
                    "summary": "codex continued implementation",
                    "next_action": "continue",
                    "needs_user": False,
                    "blocker_reason": "",
                }
            ),
            "",
        )

    invoker = CliAgentInvoker(config, execute=execute, allow_test_executor=True)
    result = invoker.implement(
        "backend", WorkerState("backend"), TaskItem("B-2", "Continue backend"), "ctx", "sonnet"
    )

    assert result.outcome == "continue"
    assert len(calls) == 2
    assert calls[1][0].lower().startswith("codex")
    assert calls[1][calls[1].index("-s") + 1] == "workspace-write"
    assert "Shell/Bash/Web/MCP tools are intentionally unavailable" not in calls[1][-1]
    assert "workspace-local" in calls[1][-1]




def test_implementation_falls_back_to_codex_on_claude_zero_exit_monthly_spend_limit(
    tmp_path: Path,
) -> None:
    import json

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    config, _backend, _mobile = _safe_config(tmp_path)
    calls: list[list[str]] = []

    def execute(command, cwd, timeout_seconds=1800):
        calls.append(command)
        if len(calls) == 1:
            return ProcessResult(
                0,
                json.dumps(
                    {
                        "is_error": True,
                        "terminal_reason": "api_error",
                        "api_error_status": 429,
                        "result": (
                            "You've hit your monthly spend limit; "
                            "raise it at claude.ai/settings/usage"
                        ),
                        "type": "result",
                    }
                ),
                "",
            )
        return ProcessResult(
            0,
            json.dumps(
                {
                    "outcome": "continue",
                    "summary": "codex continued implementation",
                    "next_action": "continue",
                    "needs_user": False,
                    "blocker_reason": "",
                }
            ),
            "",
        )

    result = CliAgentInvoker(
        config, execute=execute, allow_test_executor=True
    ).implement(
        "backend",
        WorkerState("backend"),
        TaskItem("B-SPEND-LIMIT", "Continue backend"),
        "ctx",
        "sonnet",
    )

    assert result.outcome == "continue"
    assert len(calls) == 2
    assert calls[1][0].lower().startswith("codex")


def test_implementation_falls_back_to_codex_when_claude_auth_is_revoked(tmp_path: Path) -> None:
    import json

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    config, _backend, _mobile = _safe_config(tmp_path)
    calls: list[list[str]] = []

    def execute(command, cwd, timeout_seconds=1800):
        calls.append(command)
        if len(calls) == 1:
            return ProcessResult(
                1,
                "",
                "Failed to authenticate. API Error: 401 OAuth access token has been revoked.",
            )
        return ProcessResult(
            0,
            json.dumps(
                {
                    "outcome": "continue",
                    "summary": "codex continued implementation",
                    "next_action": "continue",
                    "needs_user": False,
                    "blocker_reason": "",
                }
            ),
            "",
        )

    invoker = CliAgentInvoker(config, execute=execute, allow_test_executor=True)
    result = invoker.implement(
        "backend",
        WorkerState("backend"),
        TaskItem("B-AUTH", "Continue backend"),
        "ctx",
        "sonnet",
    )

    assert result.outcome == "continue"
    assert len(calls) == 2
    assert calls[1][0].lower().startswith("codex")
    assert calls[1][calls[1].index("-s") + 1] == "workspace-write"


def test_implementation_pauses_when_claude_and_codex_are_both_usage_limited(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import AgentInvocationError, CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    config, _backend, _mobile = _safe_config(tmp_path)
    calls = 0

    def execute(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return ProcessResult(1, "", "usage limit reached")

    invoker = CliAgentInvoker(config, execute=execute, allow_test_executor=True)
    with pytest.raises(AgentInvocationError) as exc:
        invoker.implement(
            "backend", WorkerState("backend"), TaskItem("B-3", "Continue backend"), "ctx", "sonnet"
        )

    assert calls == 2
    assert exc.value.provider == "codex"
    assert exc.value.kind == "usage_limit"


def test_codex_approval_flag_precedes_exec_subcommand(tmp_path: Path) -> None:
    command = build_codex_command("review", tmp_path / "repo", "Review.", TERRA_MODEL)

    expected_launcher = "codex.cmd" if os.name == "nt" else "codex"
    assert command[:4] == [expected_launcher, "-a", "never", "exec"]
    assert command.index("-a") < command.index("exec")


def test_codex_orchestrator_command_ignores_user_config(tmp_path: Path) -> None:
    command = build_codex_command("implement", tmp_path / "repo", "Implement.", TERRA_MODEL)

    assert "--ignore-user-config" in command
    assert command.index("--ignore-user-config") > command.index("exec")


def test_codex_orchestrator_command_ignores_user_exec_rules(tmp_path: Path) -> None:
    command = build_codex_command("implement", tmp_path / "repo", "Implement.", TERRA_MODEL)

    assert "--ignore-rules" in command
    assert command.index("--ignore-rules") > command.index("exec")


def test_codex_orchestrator_uses_elevated_windows_sandbox(tmp_path: Path) -> None:
    command = build_codex_command("implement", tmp_path / "repo", "Implement.", TERRA_MODEL)

    config_values = [
        command[index + 1] for index, value in enumerate(command[:-1]) if value == "-c"
    ]
    assert 'windows.sandbox="elevated"' in config_values
    assert "sandbox_workspace_write.exclude_tmpdir_env_var=true" in config_values
    assert "sandbox_workspace_write.exclude_slash_tmp=true" in config_values
    assert "allow_login_shell=false" in config_values


def test_codex_windows_launcher_uses_executable_cmd_shim(tmp_path: Path) -> None:
    command = build_codex_command("review", tmp_path / "repo", "Review.", TERRA_MODEL)

    if os.name == "nt":
        assert command[0] == "codex.cmd"
    else:
        assert command[0] == "codex"


def test_claude_production_launcher_is_docker_isolated(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import build_claude_sandbox_command

    repo = tmp_path / "backend"
    repo.mkdir()
    command = build_claude_sandbox_command(repo, "Do work", "sonnet", container_name="askrex-test")

    assert command[:2] == ["docker", "run"]
    assert command[command.index("--name") + 1] == "askrex-test"
    assert "--cap-drop" in command and "ALL" in command
    assert "--security-opt" in command and "no-new-privileges" in command
    joined = " ".join(command)
    assert f"src={repo}" in joined and "dst=/workspace" in joined
    from scripts.dev_orchestrator.runner import CLAUDE_SANDBOX_IMAGE_ID

    assert CLAUDE_SANDBOX_IMAGE_ID in command
    assert "askrex-claude-code:2.1.238" not in command
    assert "askrex-coordination" not in joined
    assert "rex-ai-pc-test" not in joined
    assert "--tools" in command
    assert command[command.index("--tools") + 1] == "Read,Write,Edit,Glob,Grep"


def test_claude_sandbox_keeps_stdin_open_for_prompt_streaming(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import build_claude_sandbox_command

    repo = tmp_path / "backend"
    repo.mkdir()
    command = build_claude_sandbox_command(repo, "Do work", "sonnet", container_name="askrex-test")

    assert "-i" in command[: command.index("--name")]


def test_claude_sandbox_prompt_is_delivered_over_stdin_not_argv(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import build_claude_sandbox_command

    repo = tmp_path / "backend"
    repo.mkdir()
    prompt = "PROMPT-BOUNDARY-" + ("X" * 40000)

    command = build_claude_sandbox_command(repo, prompt, "sonnet", container_name="askrex-test")

    assert prompt not in command
    assert not any("PROMPT-BOUNDARY-" in part for part in command)


def test_claude_sandbox_image_is_pinned() -> None:
    from scripts.dev_orchestrator.runner import CLAUDE_SANDBOX_IMAGE, CLAUDE_SANDBOX_IMAGE_ID

    assert CLAUDE_SANDBOX_IMAGE == "askrex-claude-code:2.1.238"
    assert CLAUDE_SANDBOX_IMAGE_ID.startswith("sha256:")
    assert len(CLAUDE_SANDBOX_IMAGE_ID) == 71


def test_production_claude_invocation_routes_through_docker(tmp_path: Path, monkeypatch) -> None:
    import json

    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.cli import initialize_runtime
    from scripts.dev_orchestrator.handoff import acknowledge_handoff
    from scripts.dev_orchestrator.types import TaskItem, WorkerState
    from tests.scripts.test_dev_orchestrator_safety import _git_repo

    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    coord = tmp_path / "coord"
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(coord, backend, mobile, frozen)
    acknowledge_handoff(config, "backend", source="test")

    observed = {}

    def fake_run(
        command,
        cwd,
        timeout_seconds=1800,
        *,
        activity_file=None,
        activity_metadata=None,
        stdin_text=None,
    ):
        observed["command"] = command
        observed["cwd"] = cwd
        observed["stdin_text"] = stdin_text
        payload = json.dumps(
            {
                "outcome": "failed",
                "summary": "stop after transport proof",
                "next_action": "none",
                "needs_user": False,
                "blocker_reason": "",
                "task_id": "B-D",
                "role": "backend",
                "invocation_id": activity_metadata["invocation_id"],
            }
        )
        return runner.ProcessResult(0, payload, "")

    monkeypatch.setattr(runner, "run_command", fake_run)
    monkeypatch.setattr(
        runner, "ensure_claude_sandbox_image", lambda: observed.setdefault("image_ready", True)
    )
    invoker = runner.CliAgentInvoker(config)
    result = invoker.implement(
        "backend", WorkerState("backend"), TaskItem("B-D", "Docker route"), "ctx", "sonnet"
    )

    assert result.outcome == "failed"
    assert observed["cwd"].resolve() != backend.resolve()
    assert observed["cwd"].name == "repo"
    assert observed["stdin_text"] is not None
    assert "Docker route" in observed["stdin_text"]
    assert "Docker route" not in " ".join(observed["command"])


def test_claude_sandbox_rejects_mutated_image(monkeypatch) -> None:
    from scripts.dev_orchestrator import runner

    monkeypatch.setattr(runner, "_docker_image_state", lambda: ("present", "sha256:" + "0" * 64))
    with pytest.raises(RuntimeError, match="image identity mismatch"):
        runner.ensure_claude_sandbox_image()


def test_claude_sandbox_fails_closed_on_ambiguous_image_inspect(monkeypatch) -> None:
    import subprocess as sp

    from scripts.dev_orchestrator import runner

    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return sp.CompletedProcess(args, 1, "", "error during connect: daemon unavailable")
        if args[:2] == ["docker", "build"]:
            raise AssertionError("ambiguous image state must not trigger a build")
        raise AssertionError(args)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="image state is unavailable"):
        runner.ensure_claude_sandbox_image()
    assert not any(call[:2] == ["docker", "build"] for call in calls)


def test_production_claude_applies_only_scratch_patch(tmp_path: Path, monkeypatch) -> None:
    import json
    import subprocess as sp

    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.cli import initialize_runtime
    from scripts.dev_orchestrator.handoff import acknowledge_handoff
    from scripts.dev_orchestrator.types import TaskItem, WorkerState
    from tests.scripts.test_dev_orchestrator_safety import _git_repo

    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    coord = tmp_path / "coord"
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(coord, backend, mobile, frozen)
    acknowledge_handoff(config, "backend", source="test")

    def fake_run(
        command,
        cwd,
        timeout_seconds=1800,
        *,
        activity_file=None,
        activity_metadata=None,
        stdin_text=None,
    ):
        assert cwd.resolve() != backend.resolve()
        assert cwd.name == "repo"
        (cwd / "claude-only.txt").write_text("from scratch\n", encoding="utf-8")
        payload = json.dumps(
            {
                "outcome": "continue",
                "summary": "ok",
                "next_action": "continue",
                "needs_user": False,
                "blocker_reason": "",
                "task_id": "B-PATCH",
                "role": "backend",
                "invocation_id": activity_metadata["invocation_id"],
            }
        )
        return runner.ProcessResult(0, payload, "")

    monkeypatch.setattr(runner, "run_command", fake_run)
    monkeypatch.setattr(runner, "ensure_claude_sandbox_image", lambda: None)
    invoker = runner.CliAgentInvoker(config)
    result = invoker.implement(
        "backend", WorkerState("backend"), TaskItem("B-PATCH", "Scratch patch"), "ctx", "sonnet"
    )
    assert result.outcome == "continue"
    assert (backend / "claude-only.txt").read_text(encoding="utf-8") == "from scratch\n"
    body = sp.check_output(["git", "show", "-s", "--format=%B", "HEAD"], cwd=backend, text=True)
    assert "AskRex-Orchestrator-Invocation:" in body
    assert sp.check_output(["git", "status", "--porcelain"], cwd=backend, text=True).strip() == ""


def test_production_codex_fallback_publishes_only_scratch_patch(
    tmp_path: Path, monkeypatch
) -> None:
    import json
    import subprocess as sp

    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.cli import initialize_runtime
    from scripts.dev_orchestrator.handoff import acknowledge_handoff
    from scripts.dev_orchestrator.types import TaskItem, WorkerState
    from tests.scripts.test_dev_orchestrator_safety import _git_repo

    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    coord = tmp_path / "coord"
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(coord, backend, mobile, frozen)
    acknowledge_handoff(config, "backend", source="test")
    calls = 0

    def fake_run(command, cwd, timeout_seconds=1800, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return runner.ProcessResult(1, "", "You've hit your session limit")
        assert cwd.resolve() != backend.resolve()
        assert cwd.name == "repo"
        assert cwd.parent.parent.resolve() == (coord.parent / ".askrex-agent-scratch").resolve()
        assert command[command.index("-s") + 1] == "workspace-write"
        assert "--skip-git-repo-check" in command
        assert not (cwd / ".git").exists()
        hidden_git = list(cwd.parent.glob(".git-orchestrator-*"))
        assert len(hidden_git) == 1
        assert hidden_git[0].is_dir()
        (cwd / "codex-only.txt").write_text("from codex scratch\n", encoding="utf-8")
        payload = json.dumps(
            {
                "outcome": "continue",
                "summary": "ok",
                "next_action": "continue",
                "needs_user": False,
                "blocker_reason": "",
                "task_id": "B-CODEX",
                "role": "backend",
                "invocation_id": kwargs["activity_metadata"]["invocation_id"],
            }
        )
        return runner.ProcessResult(0, payload, "")

    monkeypatch.setattr(runner, "run_command", fake_run)
    monkeypatch.setattr(runner, "ensure_claude_sandbox_image", lambda: None)
    invoker = runner.CliAgentInvoker(config)
    result = invoker.implement(
        "backend", WorkerState("backend"), TaskItem("B-CODEX", "Scratch patch"), "ctx", "sonnet"
    )

    assert result.outcome == "continue"
    assert calls == 2
    assert (backend / "codex-only.txt").read_text(encoding="utf-8") == "from codex scratch\n"
    body = sp.check_output(["git", "show", "-s", "--format=%B", "HEAD"], cwd=backend, text=True)
    assert "AskRex-Orchestrator-Invocation:" in body
    assert sp.check_output(["git", "status", "--porcelain"], cwd=backend, text=True).strip() == ""


def test_production_claude_rejects_concurrent_live_repo_change(tmp_path: Path, monkeypatch) -> None:
    import json

    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.cli import initialize_runtime
    from scripts.dev_orchestrator.handoff import HandoffRequired, acknowledge_handoff
    from scripts.dev_orchestrator.types import TaskItem, WorkerState
    from tests.scripts.test_dev_orchestrator_safety import _git_repo

    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    coord = tmp_path / "coord"
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(coord, backend, mobile, frozen)
    acknowledge_handoff(config, "backend", source="test")

    def fake_run(
        command,
        cwd,
        timeout_seconds=1800,
        *,
        activity_file=None,
        activity_metadata=None,
        stdin_text=None,
    ):
        assert cwd.resolve() != backend.resolve()
        (cwd / "claude-only.txt").write_text("from scratch\n", encoding="utf-8")
        (backend / "foreign-writer.txt").write_text("external\n", encoding="utf-8")
        payload = json.dumps(
            {
                "outcome": "continue",
                "summary": "ok",
                "next_action": "continue",
                "needs_user": False,
                "blocker_reason": "",
                "task_id": "B-RACE",
                "role": "backend",
                "invocation_id": activity_metadata["invocation_id"],
            }
        )
        return runner.ProcessResult(0, payload, "")

    monkeypatch.setattr(runner, "run_command", fake_run)
    monkeypatch.setattr(runner, "ensure_claude_sandbox_image", lambda: None)
    invoker = runner.CliAgentInvoker(config)
    with pytest.raises(HandoffRequired, match="live repository changed"):
        invoker.implement(
            "backend", WorkerState("backend"), TaskItem("B-RACE", "Race"), "ctx", "sonnet"
        )
    assert not (backend / "claude-only.txt").exists()


@pytest.mark.parametrize("phase", ["review", "lead"])
def test_codex_results_validate_before_handoff_lease_mutation(
    tmp_path: Path, monkeypatch, phase: str
) -> None:
    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.runner import AgentInvocationError, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem

    config, _backend, _ = _safe_config(tmp_path)
    lease_path = config.coordination_root / "handoff" / "backend.json"
    before = lease_path.read_text(encoding="utf-8")

    def fake_run(_command, _cwd, **_kwargs):
        return ProcessResult(0, '{"outcome":"pass"}', "")

    monkeypatch.setattr(runner, "run_command", fake_run)
    invoker = runner.CliAgentInvoker(config)
    with pytest.raises(AgentInvocationError, match="invalid_output"):
        if phase == "review":
            invoker.review(
                "backend",
                WorkerState("backend"),
                TaskItem("B-INVALID-CODEX", "Invalid reviewer output"),
                "ctx",
                TERRA_MODEL,
            )
        else:
            invoker.lead("backend", WorkerState("backend"), "ctx")

    assert lease_path.read_text(encoding="utf-8") == before


def test_claude_invalid_output_does_not_publish_scratch_changes(tmp_path: Path) -> None:
    import subprocess as sp

    from scripts.dev_orchestrator.runner import AgentInvocationError, CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem

    config, backend, _ = _safe_config(tmp_path)
    before = sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip()

    def execute(_command, cwd, **_kwargs):
        (cwd / "invalid-output.txt").write_text("scratch only\n", encoding="utf-8")
        return ProcessResult(0, "not-json", "")

    with pytest.raises(AgentInvocationError, match="invalid_output"):
        CliAgentInvoker(config, execute=execute, allow_test_executor=True).implement(
            "backend", WorkerState("backend"), TaskItem("B-INVALID", "Invalid"), "ctx", "sonnet"
        )
    assert sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip() == before
    assert not (backend / "invalid-output.txt").exists()
    assert sp.check_output(["git", "status", "--porcelain"], cwd=backend, text=True).strip() == ""


def test_claude_blocked_result_keeps_live_repo_unchanged(tmp_path: Path) -> None:
    import json
    import subprocess as sp

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem

    config, backend, _ = _safe_config(tmp_path)
    before = sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip()
    payload = json.dumps(
        {
            "outcome": "blocked_user",
            "summary": "need user",
            "next_action": "confirm",
            "needs_user": True,
            "blocker_reason": "confirmation required",
        }
    )

    def execute(_command, cwd, **_kwargs):
        (cwd / "held-output.txt").write_text("scratch only\n", encoding="utf-8")
        return ProcessResult(0, payload, "")

    result = CliAgentInvoker(config, execute=execute, allow_test_executor=True).implement(
        "backend", WorkerState("backend"), TaskItem("B-HOLD", "Hold"), "ctx", "sonnet"
    )
    assert result.outcome == "blocked_user"
    assert sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip() == before
    assert not (backend / "held-output.txt").exists()
    assert sp.check_output(["git", "status", "--porcelain"], cwd=backend, text=True).strip() == ""


def test_custom_executor_requires_explicit_test_opt_in(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult

    config, _, _ = _safe_config(tmp_path)
    with pytest.raises(ValueError, match="test-only"):
        CliAgentInvoker(config, execute=lambda *_a, **_k: ProcessResult(0, "{}", ""))


def test_failed_live_publication_leaves_repo_pristine(tmp_path: Path, monkeypatch) -> None:
    import subprocess as sp

    from scripts.dev_orchestrator import runner
    from tests.scripts.test_dev_orchestrator_safety import _git_repo

    live = tmp_path / "live"
    _git_repo(live)
    before = sp.check_output(["git", "rev-parse", "HEAD"], cwd=live, text=True).strip()
    scratch = tmp_path / "scratch"
    runner._clone_scratch_repo(live, before, scratch)
    (scratch / "change.txt").write_text("x\n", encoding="utf-8")
    scratch_head = runner._create_scratch_commit(scratch, before, "inv-test")
    original = runner._git_run

    def fail_merge(repo, *args):
        if len(args) >= 2 and args[0] == "git" and args[1] == "merge":
            return sp.CompletedProcess(args, 1, "", "simulated merge failure")
        return original(repo, *args)

    monkeypatch.setattr(runner, "_git_run", fail_merge)
    with pytest.raises(runner.HandoffRequired, match="cannot fast-forward"):
        runner._publish_scratch_commit(live, scratch, before, scratch_head)
    assert sp.check_output(["git", "rev-parse", "HEAD"], cwd=live, text=True).strip() == before
    assert sp.check_output(["git", "status", "--porcelain"], cwd=live, text=True).strip() == ""
    assert not (live / "change.txt").exists()


def test_implementation_context_invalid_issue_update_does_not_publish(tmp_path: Path) -> None:
    import json
    import subprocess as sp

    from scripts.dev_orchestrator.runner import AgentInvocationError, CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem

    config, backend, _ = _safe_config(tmp_path)
    before = sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip()
    payload = json.dumps(
        {
            "outcome": "continue",
            "summary": "edited",
            "next_action": "review",
            "needs_user": False,
            "blocker_reason": "",
            "issue_updates": [
                {"issue_id": "TEST-123", "status": "fixed-needs-retest", "note": "bad phase"}
            ],
        }
    )

    def execute(_command, cwd, **_kwargs):
        (cwd / "should-not-publish.txt").write_text("scratch only\n", encoding="utf-8")
        return ProcessResult(0, payload, "")

    with pytest.raises(AgentInvocationError, match="issue updates are not allowed"):
        CliAgentInvoker(config, execute=execute, allow_test_executor=True).implement(
            "backend",
            WorkerState("backend"),
            TaskItem("B-CONTEXT", "Context validation"),
            "ctx",
            "sonnet",
        )
    assert sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip() == before
    assert not (backend / "should-not-publish.txt").exists()


def test_custom_codex_executor_uses_scratch_clone(tmp_path: Path) -> None:
    import json

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem

    config, backend, _ = _safe_config(tmp_path)
    observed = {}

    def execute(command, cwd, **_kwargs):
        observed["cwd"] = cwd.resolve()
        observed["command"] = command
        (cwd / "test-executor.txt").write_text("scratch\n", encoding="utf-8")
        return ProcessResult(
            0,
            json.dumps(
                {
                    "outcome": "pass",
                    "summary": "ok",
                    "next_action": "",
                    "needs_user": False,
                    "blocker_reason": "",
                }
            ),
            "",
        )

    result = CliAgentInvoker(config, execute=execute, allow_test_executor=True).review(
        "backend", WorkerState("backend"), TaskItem("B-REVIEW", "Review"), "ctx", TERRA_MODEL
    )
    assert result.outcome == "pass"
    assert observed["cwd"] != backend.resolve()
    assert not (backend / "test-executor.txt").exists()
    assert str(backend.resolve()) not in observed["command"]


def test_windows_creationflags_avoid_codex_process_group() -> None:
    from scripts.dev_orchestrator import runner

    if os.name == "nt":
        assert runner._windows_creationflags(["codex.cmd", "exec"]) == 0
        assert runner._windows_creationflags(["codex.exe", "exec"]) == 0
        assert (
            runner._windows_creationflags(["docker", "run"])
            == runner.subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        assert runner._windows_creationflags(["codex", "exec"]) == 0
        assert runner._windows_creationflags(["docker", "run"]) == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows Codex sandbox regression")
def test_windows_codex_process_scope_serializes_threads() -> None:
    import threading
    import time

    from scripts.dev_orchestrator import runner

    entered: list[str] = []
    first_inside = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def first() -> None:
        with runner._codex_process_scope():
            entered.append("first")
            first_inside.set()
            assert release_first.wait(2)

    def second() -> None:
        assert first_inside.wait(2)
        second_started.set()
        with runner._codex_process_scope():
            entered.append("second")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    assert first_inside.wait(2)
    t2.start()
    assert second_started.wait(2)
    time.sleep(0.05)
    assert entered == ["first"]
    release_first.set()
    t1.join(2)
    t2.join(2)
    assert not t1.is_alive()
    assert not t2.is_alive()
    assert entered == ["first", "second"]


def test_run_command_uses_utf8_for_unicode_stdin(tmp_path: Path) -> None:
    import sys

    from scripts.dev_orchestrator import runner

    result = runner.run_command(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.buffer.read().hex())"],
        tmp_path,
        stdin_text="\ufeffAskRex",
    )

    assert result.returncode == 0
    assert result.stdout == "\ufeffAskRex".encode().hex()


def test_scratch_cleanup_retries_transient_sharing_violation(tmp_path: Path, monkeypatch) -> None:
    from scripts.dev_orchestrator import runner

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    calls = []
    real_rmtree = runner.tempfile._shutil.rmtree

    def flaky_rmtree(path, *args, **kwargs):
        calls.append(Path(path))
        if len(calls) <= 25:
            exc = PermissionError(13, "sharing violation", str(path))
            exc.winerror = 32
            raise exc
        real_rmtree(path)

    monkeypatch.setattr(runner.tempfile._shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    runner._remove_scratch_tree(scratch)

    assert len(calls) == 26
    assert all(path == scratch for path in calls)
    assert not scratch.exists()


def test_scratch_cleanup_exhausted_sharing_violation_is_nonfatal(
    tmp_path: Path, monkeypatch
) -> None:
    from scripts.dev_orchestrator import runner

    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def locked_rmtree(path, *args, **kwargs):
        exc = PermissionError(13, "sharing violation", str(path))
        exc.winerror = 32
        raise exc

    monkeypatch.setattr(runner.tempfile._shutil, "rmtree", locked_rmtree)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    runner._remove_scratch_tree(scratch, attempts=2, delay_seconds=0)
    assert scratch.exists()


def test_production_codex_review_uses_disposable_clone(tmp_path: Path, monkeypatch) -> None:
    import json

    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.types import TaskItem

    config, backend, _ = _safe_config(tmp_path)
    observed = {}

    def fake_run(
        command,
        cwd,
        timeout_seconds=1800,
        *,
        activity_file=None,
        activity_metadata=None,
        stdin_text=None,
    ):
        observed["command"] = command
        observed["cwd"] = cwd.resolve()
        observed["same_target"] = Path(command[command.index("-C") + 1]).samefile(cwd)
        observed["stdin_text"] = stdin_text
        return runner.ProcessResult(
            0,
            json.dumps(
                {
                    "outcome": "pass",
                    "summary": "ok",
                    "next_action": "",
                    "needs_user": False,
                    "blocker_reason": "",
                    "task_id": "B-PROD-REVIEW",
                    "role": "backend",
                    "invocation_id": activity_metadata["invocation_id"],
                }
            ),
            "",
        )

    monkeypatch.setattr(runner, "run_command", fake_run)
    result = runner.CliAgentInvoker(config).review(
        "backend", WorkerState("backend"), TaskItem("B-PROD-REVIEW", "Review"), "ctx", TERRA_MODEL
    )
    assert result.outcome == "pass"
    assert observed["cwd"] != backend.resolve()
    assert observed["same_target"] is True
    assert observed["command"][-1] == "-"
    assert observed["stdin_text"] is not None
    assert "Review" in observed["stdin_text"]
    assert "AskRex-Orchestrator-Invocation-ID:" in observed["stdin_text"]


def test_production_claude_requires_exact_result_binding_before_publish(
    tmp_path: Path, monkeypatch
) -> None:
    import json
    import subprocess as sp

    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.runner import AgentInvocationError
    from scripts.dev_orchestrator.types import TaskItem

    config, backend, _ = _safe_config(tmp_path)
    before = sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip()

    def fake_run(
        command,
        cwd,
        timeout_seconds=1800,
        *,
        activity_file=None,
        activity_metadata=None,
        stdin_text=None,
    ):
        mount = next(
            part
            for part in command
            if part.startswith("type=bind,src=") and "dst=/workspace" in part
        )
        scratch = Path(mount.split(",dst=/workspace", 1)[0].split("src=", 1)[1])
        (scratch / "wrong-binding.txt").write_text("scratch\n", encoding="utf-8")
        return runner.ProcessResult(
            0,
            json.dumps(
                {
                    "outcome": "continue",
                    "summary": "edited",
                    "next_action": "review",
                    "needs_user": False,
                    "blocker_reason": "",
                    "task_id": "WRONG-TASK",
                    "role": "mobile",
                    "invocation_id": "wrong",
                }
            ),
            "",
        )

    monkeypatch.setattr(runner, "run_command", fake_run)
    monkeypatch.setattr(runner, "ensure_claude_sandbox_image", lambda: None)
    with pytest.raises(AgentInvocationError, match="binding"):
        runner.CliAgentInvoker(config).implement(
            "backend", WorkerState("backend"), TaskItem("B-BIND", "Bound task"), "ctx", "sonnet"
        )
    assert sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip() == before
    assert not (backend / "wrong-binding.txt").exists()


def test_production_claude_rejects_unbound_blocked_result_with_coordination(
    tmp_path: Path, monkeypatch
) -> None:
    import json

    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.runner import AgentInvocationError
    from scripts.dev_orchestrator.types import TaskItem

    config, _, _ = _safe_config(tmp_path)

    def fake_run(command, cwd, **_kwargs):
        return runner.ProcessResult(
            0,
            json.dumps(
                {
                    "outcome": "blocked_user",
                    "summary": "blocked",
                    "next_action": "confirm",
                    "needs_user": True,
                    "blocker_reason": "need confirmation",
                    "coordination_messages": [
                        {
                            "to": "mobile",
                            "priority": "high",
                            "related": "B-BIND-ALL",
                            "needs_response": True,
                            "body": "forged cross-stream message",
                        }
                    ],
                }
            ),
            "",
        )

    monkeypatch.setattr(runner, "run_command", fake_run)
    monkeypatch.setattr(runner, "ensure_claude_sandbox_image", lambda: None)
    with pytest.raises(AgentInvocationError, match="binding"):
        runner.CliAgentInvoker(config).implement(
            "backend",
            WorkerState("backend"),
            TaskItem("B-BIND-ALL", "Bind every result"),
            "ctx",
            "sonnet",
        )


def test_custom_claude_executor_cannot_publish_to_live_repo(tmp_path: Path) -> None:
    import json
    import subprocess as sp

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem

    config, backend, _ = _safe_config(tmp_path)
    before = sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip()

    def execute(_command, cwd, **_kwargs):
        (cwd / "synthetic-only.txt").write_text("scratch\n", encoding="utf-8")
        return ProcessResult(
            0,
            json.dumps(
                {
                    "outcome": "continue",
                    "summary": "synthetic",
                    "next_action": "review",
                    "needs_user": False,
                    "blocker_reason": "",
                }
            ),
            "",
        )

    result = CliAgentInvoker(config, execute=execute, allow_test_executor=True).implement(
        "backend",
        WorkerState("backend"),
        TaskItem("B-TEST-ONLY", "Synthetic executor"),
        "ctx",
        "sonnet",
    )
    assert result.outcome == "continue"
    assert sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip() == before
    assert not (backend / "synthetic-only.txt").exists()
    assert sp.check_output(["git", "status", "--porcelain"], cwd=backend, text=True).strip() == ""


def test_production_codex_review_uses_invocation_bound_schema(tmp_path: Path, monkeypatch) -> None:
    import json

    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.runner import ProcessResult
    from scripts.dev_orchestrator.types import TaskItem

    config, _backend, _ = _safe_config(tmp_path)
    observed: dict[str, object] = {}

    def fake_run(command, _cwd, **kwargs):
        invocation_id = kwargs["activity_metadata"]["invocation_id"]
        schema_path = Path(command[command.index("--output-schema") + 1])
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        observed["schema_path"] = schema_path
        observed["role"] = schema["properties"]["role"]
        observed["task_id"] = schema["properties"]["task_id"]
        observed["invocation_id"] = schema["properties"]["invocation_id"]
        payload = {
            "outcome": "pass",
            "summary": "ok",
            "next_action": "",
            "needs_user": False,
            "blocker_reason": "",
            "task_id": "B-BOUND",
            "task_prompt": "",
            "role": "backend",
            "invocation_id": invocation_id,
            "coordination_messages": [],
            "issue_updates": [],
        }
        return ProcessResult(0, json.dumps(payload), "")

    monkeypatch.setattr(runner, "run_command", fake_run)
    result = runner.CliAgentInvoker(config).review(
        "backend",
        WorkerState("backend"),
        TaskItem("B-BOUND", "Review"),
        "ctx",
        TERRA_MODEL,
    )

    assert result.outcome == "pass"
    assert observed["role"] == {"type": "string", "enum": ["backend"]}
    assert observed["task_id"] == {"type": "string", "enum": ["B-BOUND"]}
    invocation_schema = observed["invocation_id"]
    assert isinstance(invocation_schema, dict)
    assert invocation_schema["type"] == "string"
    assert len(invocation_schema["enum"]) == 1
    assert observed["schema_path"] != Path(runner._CODEX_SCHEMA_PATH)


def test_production_codex_review_rejects_mismatched_task_binding(
    tmp_path: Path, monkeypatch
) -> None:
    import json

    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.runner import AgentInvocationError, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem

    config, _backend, _ = _safe_config(tmp_path)

    def fake_run(_command, _cwd, **kwargs):
        invocation_id = kwargs["activity_metadata"]["invocation_id"]
        payload = {
            "outcome": "pass",
            "summary": "ok",
            "next_action": "",
            "needs_user": False,
            "blocker_reason": "",
            "task_id": "WRONG",
            "task_prompt": "",
            "role": "backend",
            "invocation_id": invocation_id,
            "coordination_messages": [],
            "issue_updates": [],
        }
        return ProcessResult(0, json.dumps(payload), "")

    monkeypatch.setattr(runner, "run_command", fake_run)
    invoker = runner.CliAgentInvoker(config)
    with pytest.raises(AgentInvocationError, match="binding mismatch"):
        invoker.review(
            "backend", WorkerState("backend"), TaskItem("B-BOUND", "Review"), "ctx", TERRA_MODEL
        )


def test_production_codex_lead_rejects_empty_role_and_invocation_binding(
    tmp_path: Path, monkeypatch
) -> None:
    import json

    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.runner import AgentInvocationError, ProcessResult

    config, _backend, _ = _safe_config(tmp_path)

    def fake_run(_command, _cwd, **_kwargs):
        payload = {
            "outcome": "assign",
            "summary": "assign",
            "next_action": "",
            "needs_user": False,
            "blocker_reason": "",
            "task_id": "B-NEXT",
            "task_prompt": "Do it",
            "role": "",
            "invocation_id": "",
            "coordination_messages": [],
            "issue_updates": [],
        }
        return ProcessResult(0, json.dumps(payload), "")

    monkeypatch.setattr(runner, "run_command", fake_run)
    invoker = runner.CliAgentInvoker(config)
    with pytest.raises(AgentInvocationError, match="binding mismatch"):
        invoker.lead("backend", WorkerState("backend"), "ctx")


def test_production_claude_keeps_activity_marker_until_handoff_is_durable(
    tmp_path: Path, monkeypatch
) -> None:
    import json
    import re
    import sys

    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.types import TaskItem

    config, backend, _ = _safe_config(tmp_path)
    marker = config.coordination_root / "active-agents" / "backend.json"
    observed = {}

    monkeypatch.setattr(runner, "ensure_claude_sandbox_image", lambda: None)

    def fake_sandbox(_repo, prompt, _model, *, container_name):
        invocation_id = re.search(r"Invocation-ID: ([0-9a-f-]+)", prompt).group(1)
        payload = json.dumps(
            {
                "outcome": "ready_for_review",
                "summary": "done",
                "next_action": "review",
                "needs_user": False,
                "blocker_reason": "",
                "task_id": "B-DURABLE",
                "role": "backend",
                "invocation_id": invocation_id,
                "coordination_messages": [],
                "issue_updates": [],
            }
        )
        code = (
            "from pathlib import Path; Path('durable.txt').write_text('ok\\n', encoding='utf-8'); print("
            + repr(payload)
            + ")"
        )
        return [sys.executable, "-c", code]

    monkeypatch.setattr(runner, "build_claude_sandbox_command", fake_sandbox)
    real_advance = runner.advance_handoff

    def observing_advance(*args, **kwargs):
        observed.update(json.loads(marker.read_text(encoding="utf-8")))
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(runner, "advance_handoff", observing_advance)
    result = runner.CliAgentInvoker(config).implement(
        "backend", WorkerState("backend"), TaskItem("B-DURABLE", "Durable"), "ctx", "sonnet"
    )
    assert result.outcome == "ready_for_review"
    assert observed["status"] == "postprocessing"
    assert not marker.exists()
    assert (backend / "durable.txt").read_text(encoding="utf-8") == "ok\n"


def test_production_claude_preserves_scratch_and_marker_when_publication_fails(
    tmp_path: Path, monkeypatch
) -> None:
    import json
    import re
    import sys

    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.handoff import HandoffRequired
    from scripts.dev_orchestrator.types import TaskItem

    config, _, _ = _safe_config(tmp_path)
    marker = config.coordination_root / "active-agents" / "backend.json"
    monkeypatch.setattr(runner, "ensure_claude_sandbox_image", lambda: None)

    def fake_sandbox(_repo, prompt, _model, *, container_name):
        invocation_id = re.search(r"Invocation-ID: ([0-9a-f-]+)", prompt).group(1)
        payload = json.dumps(
            {
                "outcome": "ready_for_review",
                "summary": "done",
                "next_action": "review",
                "needs_user": False,
                "blocker_reason": "",
                "task_id": "B-PRESERVE",
                "role": "backend",
                "invocation_id": invocation_id,
                "coordination_messages": [],
                "issue_updates": [],
            }
        )
        code = (
            "from pathlib import Path; Path('recoverable.txt').write_text('keep\\n', encoding='utf-8'); print("
            + repr(payload)
            + ")"
        )
        return [sys.executable, "-c", code]

    monkeypatch.setattr(runner, "build_claude_sandbox_command", fake_sandbox)

    def fail_publish(*_args, **_kwargs):
        raise HandoffRequired("simulated publication failure")

    monkeypatch.setattr(runner, "_publish_scratch_commit", fail_publish)
    with pytest.raises(HandoffRequired, match="simulated publication failure"):
        runner.CliAgentInvoker(config).implement(
            "backend", WorkerState("backend"), TaskItem("B-PRESERVE", "Preserve"), "ctx", "sonnet"
        )
    assert marker.is_file()
    marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    assert marker_payload["status"] == "postprocessing"
    scratch = Path(marker_payload["scratch_path"])
    assert scratch.is_dir()
    assert (scratch / "recoverable.txt").read_text(encoding="utf-8") == "keep\n"


def test_implementation_prompt_forbids_issue_updates_until_review() -> None:
    from scripts.dev_orchestrator import runner
    from scripts.dev_orchestrator.types import TaskItem

    prompt = runner._task_prompt(
        "backend", TaskItem("B-PROMPT", "Do work"), Path("C:/coord"), "ctx", "inv-1"
    )
    assert "issue_updates must be an empty array" in prompt


def test_cli_lead_prompt_is_provider_neutral_not_astra(tmp_path: Path) -> None:
    prompt = _lead_prompt("backend", tmp_path, "bounded context", "inv-lead")

    assert "lead engineer" in prompt.lower()
    assert "astra" not in prompt.lower()


def test_claude_sandbox_oauth_uses_inherited_env_without_secret_in_argv(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import build_claude_sandbox_command

    repo = tmp_path / "backend"
    repo.mkdir()
    secret = "synthetic-claude-oauth-value"
    command = build_claude_sandbox_command(
        repo,
        "Do work",
        "sonnet",
        container_name="askrex-test",
        use_oauth_token_env=True,
    )

    assert "CLAUDE_CODE_OAUTH_TOKEN" in command
    assert secret not in " ".join(command)
    assert ".credentials.json" not in " ".join(command)


def test_run_command_environment_override_is_child_local(tmp_path: Path, monkeypatch) -> None:
    import os
    import sys

    from scripts.dev_orchestrator.runner import run_command

    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    result = run_command(
        [sys.executable, "-c", "import os; print(os.getenv('CLAUDE_CODE_OAUTH_TOKEN', 'missing'))"],
        tmp_path,
        env_overrides={"CLAUDE_CODE_OAUTH_TOKEN": "child-only-token"},
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "child-only-token"
    assert os.getenv("CLAUDE_CODE_OAUTH_TOKEN") is None
