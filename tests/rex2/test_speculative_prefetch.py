"""US-101 privacy regression coverage for speculative dispatcher execution."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

from rex.audit import AuditLogger
from rex.tools.dispatcher import ToolDispatcher
from rex.tools.execution import ToolOutcome
from rex.tools.registry import Tool, ToolRegistry


class _PathLogCapture(logging.Handler):
    """Collect records even when a relevant logger does not propagate."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


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
    path_capture = _PathLogCapture()
    # Attach directly to the root logger *and* every named logger on the
    # dispatch -> lifecycle -> audit path, so a logger that sets
    # ``propagate = False`` in the future still cannot escape capture.
    root_logger = logging.getLogger()
    path_loggers = (root_logger,) + tuple(
        logging.getLogger(name)
        for name in ("rex.tools.dispatcher", "rex.tools.execution", "rex.audit")
    )

    with (
        patch("rex.tools.execution.get_audit_logger", return_value=audit_logger),
        caplog.at_level(logging.DEBUG),
        caplog.at_level(logging.DEBUG, logger="rex.tools.dispatcher"),
        caplog.at_level(logging.DEBUG, logger="rex.tools.execution"),
        caplog.at_level(logging.DEBUG, logger="rex.audit"),
    ):
        for path_logger in path_loggers:
            path_logger.addHandler(path_capture)
        try:
            result = ToolDispatcher(registry).dispatch(
                "speculative_lookup",
                {"query": private_payload},
                {
                    "user_id": "james",
                    "request_id": "speculative-private-failure",
                    "speculative": True,
                },
            )
        finally:
            for path_logger in path_loggers:
                path_logger.removeHandler(path_capture)

    assert result.status == ToolOutcome.FAILED
    assert result.error == "Speculative read failed"

    persistent_audit = audit_logger.log_path.read_text(encoding="utf-8")
    captured_logs = "\n".join(
        (caplog.text, *(record.getMessage() for record in path_capture.records))
    )
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
