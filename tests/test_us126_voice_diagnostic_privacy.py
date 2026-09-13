from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VOICE_LOG_FILES = (
    _REPO_ROOT / "rex" / "voice_loop.py",
    *sorted((_REPO_ROOT / "rex" / "voice").glob("*.py")),
)
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}


def _is_safe_exception_code(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "__name__"
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "type"
        and len(node.value.args) == 1
        and isinstance(node.value.args[0], ast.Name)
        and node.value.args[0].id == "exc"
    )


def _contains_raw_exception(node: ast.AST) -> bool:
    if _is_safe_exception_code(node):
        return False
    if isinstance(node, ast.Name) and node.id == "exc":
        return True
    if isinstance(node, ast.Attribute) and node.attr in {"_xtts_init_error", "best_user_id", "ip"}:
        return True
    return any(_contains_raw_exception(child) for child in ast.iter_child_nodes(node))


def _logger_call(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr in _LOG_METHODS


def test_voice_diagnostic_logs_do_not_serialize_raw_exception_payloads() -> None:
    violations: list[str] = []
    for path in _VOICE_LOG_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _logger_call(node):
                continue
            unsafe = any(_contains_raw_exception(arg) for arg in node.args[1:])
            unsafe = unsafe or any(
                keyword.arg == "exc_info"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            )
            unsafe = unsafe or any(
                keyword.arg != "exc_info" and _contains_raw_exception(keyword.value)
                for keyword in node.keywords
            )
            if unsafe:
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}")

    assert violations == [], (
        "Always-on voice diagnostics must use bounded error codes instead of raw "
        f"exception payloads or tracebacks: {violations}"
    )
