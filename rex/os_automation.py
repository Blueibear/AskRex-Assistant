"""
OS automation service for safe command execution and file operations.

Provides system-level automation with safety rails:
- Whitelisted command execution
- Path sanitization for file operations
- Backup before destructive operations
- Policy-gated execution
- Comprehensive audit logging
"""

import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rex.audit import LogEntry, get_audit_logger
from rex.contracts.core import ToolCall
from rex.policy_engine import get_policy_engine


@dataclass
class CommandResult:
    """Result of a command execution."""

    success: bool
    command: list[str]
    stdout: str
    stderr: str
    returncode: int
    duration_ms: int


class OSAutomationService:
    """
    Service for safe OS-level automation.

    Features:
    - Command execution with whitelist
    - File operations with path restrictions
    - Backup before deletion
    - Policy checks for destructive operations
    - Audit logging for all operations
    """

    def __init__(
        self,
        allowed_commands: list[str] | None = None,
        config_path: str | None = None,
        data_path: str | None = None,
    ):
        """
        Initialize OS automation service.

        Args:
            allowed_commands: List of allowed command names (e.g., ["ls", "cat"])
            config_path: Path to config file with allowed commands
            data_path: Base path for data directory (default: "data")
        """
        self.data_path = Path(data_path or "data")
        self.trash_path = self.data_path / "trash"
        self.trash_path.mkdir(parents=True, exist_ok=True)

        self._audit_logger = get_audit_logger()
        self._policy_engine = get_policy_engine()

        # Load allowed commands from config or use defaults
        if config_path and Path(config_path).exists():
            with open(config_path) as f:
                config = json.load(f)
                self.allowed_commands = set(config.get("allowed_commands", []))
        elif allowed_commands:
            self.allowed_commands = set(allowed_commands)
        else:
            # Default safe commands
            self.allowed_commands = {
                "ls",
                "cat",
                "echo",
                "pwd",
                "whoami",
                "date",
                "head",
                "tail",
                "grep",
                "find",
                "wc",
                "sort",
                "uniq",
                "which",
                "man",
                "file",
                "stat",
                "du",
                "df",
                "tree",
                "diff",
                "cmp",
                "md5sum",
                "sha256sum",
            }

    def run_command(
        self,
        cmd: list[str],
        timeout: int = 30,
        cwd: str | None = None,
    ) -> CommandResult:
        """
        Run a command if it's in the allowed list.

        Args:
            cmd: Command as list of strings (e.g., ["ls", "-la"])
            timeout: Timeout in seconds
            cwd: Working directory for command

        Returns:
            CommandResult with output and status

        Raises:
            PermissionError: If command is not allowed
            RuntimeError: If command execution fails
        """
        if not cmd:
            raise ValueError("Command list cannot be empty")

        command_name = cmd[0]

        # Check if command is allowed
        if command_name not in self.allowed_commands:
            raise PermissionError(
                f"Command '{command_name}' is not allowed. "
                f"Allowed commands: {', '.join(sorted(self.allowed_commands))}"
            )

        # ``find -exec`` (and its interactive variants) turns an otherwise
        # read-only allowlisted command into arbitrary command execution.
        if command_name == "find" and any(
            argument.startswith(("-exec", "-ok")) for argument in cmd[1:]
        ):
            raise PermissionError("find command execution actions are not allowed")

        # An explicit working directory is an authority boundary just like
        # file-operation paths. Do not let callers use it to escape the
        # service data directory.
        sanitized_cwd = self._sanitize_path(cwd) if cwd is not None else None

        # Policy check
        tool_call = ToolCall(
            tool="execute_shell_command",
            args={"command": cmd},
            requested_by="user",
            created_at=datetime.now(),
        )
        decision = self._policy_engine.decide(tool_call, metadata={})

        if not decision.allowed:
            raise PermissionError(f"Policy denied command execution: {decision.reason}")

        # Execute command
        action_id = str(uuid.uuid4())
        start_time = datetime.now()

        try:
            run_cmd = cmd
            if os.name == "nt" and command_name == "echo":
                # echo is a cmd.exe builtin. Running it through cmd /c would
                # interpret metacharacters supplied as message text. Python
                # receives argv directly, so it preserves echo semantics
                # without providing a shell-injection boundary.
                run_cmd = [
                    sys.executable,
                    "-c",
                    "import sys; print(' '.join(sys.argv[1:]))",
                    *cmd[1:],
                ]

            result = subprocess.run(
                run_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=sanitized_cwd,
            )

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            cmd_result = CommandResult(
                success=result.returncode == 0,
                command=run_cmd,
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
                duration_ms=duration_ms,
            )

            # Audit log
            self._audit_logger.log(
                LogEntry(
                    action_id=action_id,
                    tool="execute_shell_command",
                    tool_call_args={
                        "command": cmd,
                        "cwd": str(sanitized_cwd) if sanitized_cwd else None,
                    },
                    policy_decision="allowed",
                    tool_result={
                        "returncode": result.returncode,
                        "stdout_length": len(result.stdout),
                        "stderr_length": len(result.stderr),
                    },
                    error=None if result.returncode == 0 else result.stderr,
                    duration_ms=duration_ms,
                )
            )

            return cmd_result

        except subprocess.TimeoutExpired as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            error_msg = f"Command timed out after {timeout}s"

            self._audit_logger.log(
                LogEntry(
                    action_id=action_id,
                    tool="execute_shell_command",
                    tool_call_args={"command": run_cmd},
                    policy_decision="allowed",
                    tool_result=None,
                    error=error_msg,
                    duration_ms=duration_ms,
                )
            )

            raise RuntimeError(error_msg) from e

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            error_msg = f"Command execution failed: {str(e)}"

            self._audit_logger.log(
                LogEntry(
                    action_id=action_id,
                    tool="execute_shell_command",
                    tool_call_args={"command": run_cmd},
                    policy_decision="allowed",
                    tool_result=None,
                    error=error_msg,
                    duration_ms=duration_ms,
                )
            )

            raise RuntimeError(error_msg) from e

    def _sanitize_path(self, path: str) -> Path:
        """
        Sanitize and validate a path.

        Ensures path is within data/ directory to prevent access to sensitive files.

        Args:
            path: Path string to sanitize

        Returns:
            Resolved Path object

        Raises:
            ValueError: If path is outside data/ directory
        """
        path_obj = Path(path).resolve()

        # Ensure path is within data directory
        try:
            path_obj.relative_to(self.data_path.resolve())
        except ValueError:
            raise ValueError(
                f"Path '{path}' is outside allowed directory '{self.data_path}'. "
                "Only paths within data/ are allowed."
            )

        return path_obj

    def copy_file(self, src: str, dst: str) -> dict[str, Any]:
        """
        Copy a file with path sanitization.

        Args:
            src: Source file path
            dst: Destination file path

        Returns:
            Dictionary with copy status and paths
        """
        action_id = str(uuid.uuid4())
        start_time = datetime.now()

        try:
            src_path = self._sanitize_path(src)
            dst_path = self._sanitize_path(dst)

            if not src_path.exists():
                raise FileNotFoundError(f"Source file not found: {src}")

            # Ensure destination directory exists
            dst_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy file
            shutil.copy2(src_path, dst_path)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            result = {
                "status": "success",
                "src": str(src_path),
                "dst": str(dst_path),
                "size": dst_path.stat().st_size,
            }

            # Audit log
            self._audit_logger.log(
                LogEntry(
                    action_id=action_id,
                    tool="file_copy",
                    tool_call_args={"src": src, "dst": dst},
                    policy_decision="allowed",
                    tool_result=result,
                    error=None,
                    duration_ms=duration_ms,
                )
            )

            return result

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            error_msg = f"Copy failed: {str(e)}"

            self._audit_logger.log(
                LogEntry(
                    action_id=action_id,
                    tool="file_copy",
                    tool_call_args={"src": src, "dst": dst},
                    policy_decision="allowed",
                    tool_result=None,
                    error=error_msg,
                    duration_ms=duration_ms,
                )
            )

            raise RuntimeError(error_msg) from e

    def move_file(self, src: str, dst: str) -> dict[str, Any]:
        """
        Move a file with path sanitization.

        Args:
            src: Source file path
            dst: Destination file path

        Returns:
            Dictionary with move status and paths
        """
        action_id = str(uuid.uuid4())
        start_time = datetime.now()

        try:
            src_path = self._sanitize_path(src)
            dst_path = self._sanitize_path(dst)

            if not src_path.exists():
                raise FileNotFoundError(f"Source file not found: {src}")

            # Ensure destination directory exists
            dst_path.parent.mkdir(parents=True, exist_ok=True)

            # Move file
            shutil.move(str(src_path), str(dst_path))

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            result = {
                "status": "success",
                "src": str(src_path),
                "dst": str(dst_path),
            }

            # Audit log
            self._audit_logger.log(
                LogEntry(
                    action_id=action_id,
                    tool="file_move",
                    tool_call_args={"src": src, "dst": dst},
                    policy_decision="allowed",
                    tool_result=result,
                    error=None,
                    duration_ms=duration_ms,
                )
            )

            return result

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            error_msg = f"Move failed: {str(e)}"

            self._audit_logger.log(
                LogEntry(
                    action_id=action_id,
                    tool="file_move",
                    tool_call_args={"src": src, "dst": dst},
                    policy_decision="allowed",
                    tool_result=None,
                    error=error_msg,
                    duration_ms=duration_ms,
                )
            )

            raise RuntimeError(error_msg) from e

    def delete_file(self, path: str, backup: bool = True) -> dict[str, Any]:
        """
        Delete a file with optional backup.

        Args:
            path: File path to delete
            backup: Move to trash instead of permanent deletion

        Returns:
            Dictionary with deletion status
        """
        # Policy check for destructive operation
        tool_call = ToolCall(
            tool="file_delete",
            args={"path": path, "backup": backup},
            requested_by="user",
            created_at=datetime.now(),
        )
        decision = self._policy_engine.decide(tool_call, metadata={})

        if not decision.allowed:
            raise PermissionError(f"Policy denied file deletion: {decision.reason}")

        action_id = str(uuid.uuid4())
        start_time = datetime.now()

        try:
            file_path = self._sanitize_path(path)

            if not file_path.exists():
                raise FileNotFoundError(f"File not found: {path}")

            backup_path = None

            if backup:
                # Move to trash
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_filename = f"{file_path.stem}_{timestamp}{file_path.suffix}"
                backup_path = self.trash_path / backup_filename

                shutil.move(str(file_path), str(backup_path))
                action = "moved_to_trash"
            else:
                # Permanent deletion
                file_path.unlink()
                action = "deleted_permanently"

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            result = {
                "status": "success",
                "action": action,
                "path": str(file_path),
                "backup_path": str(backup_path) if backup_path else None,
            }

            # Audit log
            self._audit_logger.log(
                LogEntry(
                    action_id=action_id,
                    tool="file_delete",
                    tool_call_args={"path": path, "backup": backup},
                    policy_decision="allowed",
                    tool_result=result,
                    error=None,
                    duration_ms=duration_ms,
                )
            )

            return result

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            error_msg = f"Delete failed: {str(e)}"

            self._audit_logger.log(
                LogEntry(
                    action_id=action_id,
                    tool="file_delete",
                    tool_call_args={"path": path, "backup": backup},
                    policy_decision="allowed",
                    tool_result=None,
                    error=error_msg,
                    duration_ms=duration_ms,
                )
            )

            raise RuntimeError(error_msg) from e

    def list_trash(self) -> list[dict[str, Any]]:
        """
        List files in trash.

        Returns:
            List of file information dictionaries
        """
        if not self.trash_path.exists():
            return []

        files = []
        for file_path in self.trash_path.iterdir():
            if file_path.is_file():
                stat = file_path.stat()
                files.append(
                    {
                        "name": file_path.name,
                        "path": str(file_path),
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    }
                )

        return sorted(files, key=lambda x: x["modified"], reverse=True)

    def restore_from_trash(self, filename: str, dest: str) -> dict[str, Any]:
        """
        Restore a file from trash.

        Args:
            filename: Name of file in trash
            dest: Destination path to restore to

        Returns:
            Dictionary with restore status
        """
        # Trash entries are direct, service-created files. Treat ``filename``
        # as an identifier rather than a path, including on a host whose path
        # syntax differs from the user-provided separator.
        if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
            raise ValueError("Trash filename must be a single file name")

        trash_root = self.trash_path.resolve()
        trash_file = (trash_root / filename).resolve()
        try:
            trash_file.relative_to(trash_root)
        except ValueError as exc:
            raise ValueError("Trash filename must remain within the trash directory") from exc

        if not trash_file.is_file():
            raise FileNotFoundError(f"File not found in trash: {filename}")

        dest_path = self._sanitize_path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        shutil.move(str(trash_file), str(dest_path))

        return {
            "status": "success",
            "restored_from": str(trash_file),
            "restored_to": str(dest_path),
        }

    def empty_trash(self) -> dict[str, Any]:
        """
        Empty the trash folder.

        Returns:
            Dictionary with number of files deleted
        """
        if not self.trash_path.exists():
            return {"status": "success", "files_deleted": 0}

        count = 0
        for file_path in self.trash_path.iterdir():
            if file_path.is_file():
                file_path.unlink()
                count += 1

        return {"status": "success", "files_deleted": count}


# Singleton instance
_os_service: OSAutomationService | None = None


def get_os_service() -> OSAutomationService:
    """Get or create the global OS automation service."""
    global _os_service
    if _os_service is None:
        # Try to load config
        config_path = "config/os_allowed_commands.json"
        _os_service = OSAutomationService(config_path=config_path)
    return _os_service


def set_os_service(service: OSAutomationService) -> None:
    """Set the global OS automation service."""
    global _os_service
    _os_service = service


def reset_os_service() -> None:
    """Reset the global OS automation service (for testing)."""
    global _os_service
    _os_service = None
