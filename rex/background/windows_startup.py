"""Windows Task Scheduler registration for the persistent AskRex runtime."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path, PureWindowsPath

SCHTASKS_TIMEOUT_SECONDS = 15.0
SCHTASKS_TASK_ACTION_MAX_CHARS = 262
DEFAULT_TASK_NAME = "AskRex Background Runtime"


class StartupTaskError(RuntimeError):
    """Raised when Windows Task Scheduler cannot apply a requested operation."""


def _is_windows() -> bool:
    return os.name == "nt"


def _require_absolute(path: Path, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() and not PureWindowsPath(str(candidate)).is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return candidate


def _run_as_user(value: str | None) -> str:
    principal = (value or "").strip()
    if not principal:
        raise ValueError("Windows run-as user is required")
    return principal


def build_schtasks_create_command(
    task_name: str,
    pythonw_path: Path,
    runtime_root: Path,
    user_id: str,
    *,
    run_as_user: str | None = None,
) -> list[str]:
    """Build one deterministic current-user ONLOGON task-creation command."""

    if not task_name.strip():
        raise ValueError("Task name is required")
    from rex.identity import validate_user_id

    pythonw = _require_absolute(pythonw_path, "pythonw.exe path")
    root = _require_absolute(runtime_root, "Runtime root")
    user = validate_user_id(user_id)
    principal = _run_as_user(run_as_user)
    action = subprocess.list2cmdline(
        [
            str(pythonw),
            "-m",
            "rex.background.cli",
            "supervisor",
            "-r",
            str(root),
            "-u",
            user,
            "-p",
        ]
    )
    if len(action) > SCHTASKS_TASK_ACTION_MAX_CHARS:
        raise ValueError(
            f"Windows Task Scheduler action exceeds {SCHTASKS_TASK_ACTION_MAX_CHARS} characters"
        )
    return [
        "schtasks.exe",
        "/Create",
        "/TN",
        task_name,
        "/SC",
        "ONLOGON",
        "/RU",
        principal,
        "/IT",
        "/RL",
        "LIMITED",
        "/TR",
        action,
        "/F",
    ]


def _run_schtasks(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    if not _is_windows():
        raise OSError("Windows Task Scheduler operations require Windows")
    try:
        return subprocess.run(
            list(command),
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=SCHTASKS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StartupTaskError("Windows Task Scheduler command failed") from exc


def install_startup(
    task_name: str,
    pythonw_path: Path,
    runtime_root: Path,
    user_id: str,
    *,
    run_as_user: str | None = None,
) -> None:
    """Create or repair the per-user interactive startup task idempotently."""

    command = build_schtasks_create_command(
        task_name,
        pythonw_path,
        runtime_root,
        user_id,
        run_as_user=run_as_user,
    )
    result = _run_schtasks(command)
    if result.returncode != 0:
        raise StartupTaskError("Failed to create AskRex startup task")


def query_startup(task_name: str = DEFAULT_TASK_NAME) -> bool:
    """Return whether the named Task Scheduler entry can be queried."""

    if not task_name.strip():
        raise ValueError("Task name is required")
    result = _run_schtasks(["schtasks.exe", "/Query", "/TN", task_name])
    return result.returncode == 0


def remove_startup(task_name: str = DEFAULT_TASK_NAME) -> None:
    """Remove the named Task Scheduler entry if present."""

    if not task_name.strip():
        raise ValueError("Task name is required")
    result = _run_schtasks(["schtasks.exe", "/Delete", "/TN", task_name, "/F"])
    if result.returncode != 0:
        raise StartupTaskError("Failed to remove AskRex startup task")


__all__ = [
    "DEFAULT_TASK_NAME",
    "SCHTASKS_TASK_ACTION_MAX_CHARS",
    "SCHTASKS_TIMEOUT_SECONDS",
    "StartupTaskError",
    "build_schtasks_create_command",
    "install_startup",
    "query_startup",
    "remove_startup",
]
