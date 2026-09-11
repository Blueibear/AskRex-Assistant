from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import validate_agent_result
from .types import AgentResult

_SCHEMA_PATH = Path(__file__).with_name("agent-result.schema.json")


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


def _schema_json() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def build_claude_command(repo: Path, prompt: str, model: str) -> list[str]:
    return [
        "claude",
        "-p",
        "--model",
        model,
        "--permission-mode",
        "auto",
        "--output-format",
        "json",
        "--json-schema",
        _schema_json(),
        prompt,
    ]


def build_codex_command(kind: str, repo: Path, prompt: str, model: str) -> list[str]:
    if kind not in {"review", "lead"}:
        raise ValueError(f"unsupported Codex role: {kind}")
    return [
        "codex",
        "exec",
        "-m",
        model,
        "-s",
        "read-only",
        "-a",
        "never",
        "-C",
        str(repo),
        "--output-schema",
        str(_SCHEMA_PATH),
        prompt,
    ]


def run_command(command: list[str], cwd: Path, timeout_seconds: int = 1800) -> ProcessResult:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ProcessResult(124, exc.stdout or "", exc.stderr or "process timed out")
    return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


def classify_cli_failure(text: str, returncode: int) -> str:
    normalized = text.lower()
    if "usage limit" in normalized or "rate limit" in normalized or "quota" in normalized:
        return "usage_limit"
    if "login" in normalized or "not authenticated" in normalized or "authentication" in normalized:
        return "auth"
    if returncode == 124 or "timed out" in normalized or "timeout" in normalized:
        return "timeout"
    return "failed"


def _find_result(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if "outcome" in value and "summary" in value and "next_action" in value:
            return value
        for key in ("structured_output", "result", "message", "content"):
            nested = value.get(key)
            if isinstance(nested, str):
                try:
                    parsed = json.loads(nested)
                except json.JSONDecodeError:
                    continue
                found = _find_result(parsed)
                if found:
                    return found
            else:
                found = _find_result(nested)
                if found:
                    return found
    if isinstance(value, list):
        for item in value:
            found = _find_result(item)
            if found:
                return found
    return None


def extract_agent_result(output: str) -> AgentResult:
    candidates = [output.strip()]
    candidates.extend(line.strip() for line in output.splitlines() if line.strip())
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        found = _find_result(parsed)
        if found:
            return validate_agent_result(found)
    raise ValueError("agent output did not contain a valid structured result")


class AgentInvocationError(RuntimeError):
    def __init__(self, provider: str, kind: str, detail: str) -> None:
        super().__init__(f"{provider} {kind}: {detail}")
        self.provider = provider
        self.kind = kind
        self.detail = detail


def _append_extra_dir(command: list[str], directory: Path) -> list[str]:
    return [*command[:-1], "--add-dir", str(directory), command[-1]]


def _task_prompt(role: str, task, coordination_root: Path) -> str:
    feedback = f"\nReviewer feedback to address:\n{task.feedback}" if task.feedback else ""
    return (
        f"You are the {role} AskRex implementation worker. Work only in the current repository. "
        f"Read the repository agent instructions and {coordination_root}\\PROTOCOL.md, the {role} role file, "
        f"your mailbox, and owned open issues before editing. Task {task.task_id}: {task.prompt}.{feedback} "
        "Continue autonomously through implementation and relevant tests. Do not modify the other repository "
        "or the frozen rex-ai-pc-test worktree. Return only the required structured result."
    )


def _review_prompt(role: str, task, coordination_root: Path) -> str:
    return (
        f"Independently review AskRex {role} task {task.task_id}: {task.prompt}. "
        f"Read repository instructions and the shared coordination state under {coordination_root}. "
        "Inspect the current diff, relevant tests, security/ownership constraints, and claimed validation. "
        "Do not modify files. Return pass only when the task is actually ready; otherwise return changes_required, "
        "blocked_user, blocked_system, or failed using the required structured result."
    )


def _lead_prompt(role: str, coordination_root: Path) -> str:
    return (
        f"Act as Astra, lead engineer for the AskRex {role} workstream. Read the repository instructions and "
        f"authoritative coordination state under {coordination_root}. Select only the next highest-priority actionable "
        "task that belongs to this role and does not require James. Return assign with a stable task_id and bounded "
        "task_prompt, done only when objective completion gates are satisfied, or a truthful blocker outcome. "
        "Do not modify files. Return only the required structured result."
    )


class CliAgentInvoker:
    def __init__(self, config, *, execute=run_command) -> None:
        self.config = config
        self.execute = execute

    def _repo(self, role: str) -> Path:
        root = self.config.backend_root if role == "backend" else self.config.mobile_root
        if root is None:
            raise ValueError(f"missing repository root for {role}")
        if self.config.frozen_worktree and root.resolve() == self.config.frozen_worktree.resolve():
            raise ValueError("frozen worktree cannot be used by an agent")
        return root

    def _finish(self, provider: str, result: ProcessResult) -> AgentResult:
        if result.returncode != 0:
            detail = "\n".join(part for part in (result.stderr, result.stdout) if part).strip()
            kind = classify_cli_failure(detail, result.returncode)
            raise AgentInvocationError(provider, kind, detail[:4000])
        try:
            return extract_agent_result(result.stdout)
        except ValueError as exc:
            raise AgentInvocationError(provider, "invalid_output", str(exc)) from exc

    def implement(self, role, state, task, context, model) -> AgentResult:
        repo = self._repo(role)
        command = build_claude_command(
            repo, _task_prompt(role, task, self.config.coordination_root), model
        )
        command = _append_extra_dir(command, self.config.coordination_root)
        return self._finish("claude", self.execute(command, repo))

    def review(self, role, state, task, context, model) -> AgentResult:
        repo = self._repo(role)
        command = build_codex_command(
            "review",
            repo,
            _review_prompt(role, task, self.config.coordination_root),
            model,
        )
        command = _append_extra_dir(command, self.config.coordination_root)
        return self._finish("codex", self.execute(command, repo))

    def lead(self, role, state, context, task=None) -> AgentResult:
        from .routing import ASTRA_MODEL

        repo = self._repo(role)
        command = build_codex_command(
            "lead",
            repo,
            _lead_prompt(role, self.config.coordination_root),
            ASTRA_MODEL,
        )
        command = _append_extra_dir(command, self.config.coordination_root)
        return self._finish("codex", self.execute(command, repo))
