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
