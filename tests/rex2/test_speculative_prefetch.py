"""US-101 privacy regression coverage for speculative dispatcher execution."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

from rex.audit import AuditLogger
from rex.tools.dispatcher import ToolDispatcher
from rex.tools.execution import ToolOutcome
from rex.tools.registry import Tool, ToolRegistry


def test_speculative_dispatcher_failure_retains_only_safe_metadata(
    tmp_path: Path, caplog
) -> None:
    """A real speculative dispatch never logs or persists private failure text."""
    private_payload = "SPECULATIVE_PRIVATE_PAYLOAD_77"
    private_exception = "SPECULATIVE_PRIVATE_EXCEPTION_88"

    def failing_read(query: str) -> None:
        raise ConnectionError(f"{private_exception}: {query}")

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="speculative_lookup",
            description="Read-only speculative lookup",
            capability_tags=["lookup"],
            requires_config=[],
            handler=failing_read,
            operation="read",
            risk="safe",
            requires_identity=True,
            required_args=("query",),
            health="healthy",
        )
    )
    audit_logger = AuditLogger(log_path=tmp_path / "audit.log")

    with (
        patch("rex.tools.execution.get_audit_logger", return_value=audit_logger),
        caplog.at_level(logging.DEBUG, logger="rex.tools.execution"),
    ):
        result = ToolDispatcher(registry).dispatch(
            "speculative_lookup",
            {"query": private_payload},
            {
                "user_id": "james",
                "request_id": "speculative-private-failure",
                "speculative": True,
            },
        )

    assert result.status == ToolOutcome.FAILED
    assert result.error == "Speculative read failed"

    persistent_audit = audit_logger.log_path.read_text(encoding="utf-8")
    captured_logs = caplog.text
    for marker in (private_payload, private_exception):
        assert marker not in persistent_audit
        assert marker not in captured_logs

    entry = audit_logger.read_by_action_id("speculative-private-failure")
    assert entry is not None
    assert entry.error == "Speculative read failed"
    assert entry.tool_call_args["argument_names"] == ["query"]
    assert entry.tool_call_args["arguments_hash"]
    assert entry.tool_result["error_type"] == "ConnectionError"
    assert entry.duration_ms is not None
