"""Tests for Rex audit logging functionality."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("pydantic")

from rex.audit import (
    DEFAULT_LOG_DIR,
    DEFAULT_LOG_FILE,
    AuditLogger,
    LogEntry,
    get_audit_logger,
    reset_audit_logger,
)


class TestLogEntry:
    """Tests for the LogEntry model."""

    def test_minimal_instantiation(self):
        """LogEntry should work with minimal required fields."""
        entry = LogEntry(
            action_id="act_001",
            tool="time_now",
            policy_decision="allowed",
        )
        assert entry.action_id == "act_001"
        assert entry.tool == "time_now"
        assert entry.policy_decision == "allowed"
        assert entry.task_id is None
        assert entry.tool_call_args == {}
        assert entry.tool_result is None
        assert entry.error is None
        assert entry.redacted is False
        assert isinstance(entry.timestamp, datetime)

    def test_full_instantiation(self):
        """LogEntry should accept all fields."""
        now = datetime.now(UTC)
        entry = LogEntry(
            timestamp=now,
            action_id="act_002",
            task_id="task_001",
            tool="weather_now",
            tool_call_args={"location": "Dallas, TX"},
            policy_decision="allowed",
            tool_result={"temperature": 75, "conditions": "sunny"},
            error=None,
            redacted=True,
            requested_by="user:james",
            duration_ms=150,
        )
        assert entry.timestamp == now
        assert entry.action_id == "act_002"
        assert entry.task_id == "task_001"
        assert entry.tool == "weather_now"
        assert entry.tool_call_args == {"location": "Dallas, TX"}
        assert entry.policy_decision == "allowed"
        assert entry.tool_result == {"temperature": 75, "conditions": "sunny"}
        assert entry.redacted is True
        assert entry.requested_by == "user:james"
        assert entry.duration_ms == 150

    def test_json_roundtrip(self):
        """LogEntry should serialize and deserialize correctly."""
        original = LogEntry(
            action_id="act_003",
            task_id="task_002",
            tool="time_now",
            tool_call_args={"location": "Dallas"},
            policy_decision="allowed",
            tool_result={"local_time": "2024-01-15 10:30"},
            duration_ms=25,
        )
        json_str = original.model_dump_json()
        restored = LogEntry.model_validate_json(json_str)
        assert restored.action_id == original.action_id
        assert restored.task_id == original.task_id
        assert restored.tool == original.tool
        assert restored.tool_call_args == original.tool_call_args
        assert restored.policy_decision == original.policy_decision
        assert restored.tool_result == original.tool_result
        assert restored.duration_ms == original.duration_ms

    def test_policy_decision_values(self):
        """LogEntry should accept all valid policy decisions."""
        for decision in ["allowed", "denied", "requires_approval"]:
            entry = LogEntry(
                action_id="act_test",
                tool="test_tool",
                policy_decision=decision,
            )
            assert entry.policy_decision == decision

    def test_invalid_policy_decision(self):
        """LogEntry should reject invalid policy decisions."""
        with pytest.raises(Exception, match="policy_decision"):  # Pydantic validation error
            LogEntry(
                action_id="act_test",
                tool="test_tool",
                policy_decision="invalid_decision",
            )


class TestAuditLogger:
    """Tests for the AuditLogger class."""

    def test_creates_log_directory(self):
        """AuditLogger should create log directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs" / "subdir"
            AuditLogger(log_dir=log_dir)
            assert log_dir.exists()

    def test_log_creates_file(self):
        """Logging an entry should create the log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)
            entry = LogEntry(
                action_id="act_001",
                tool="time_now",
                policy_decision="allowed",
            )
            logger.log(entry)
            assert logger.log_path.exists()

    def test_log_path_override(self):
        """AuditLogger should honor log_path when provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "custom" / "audit.log"
            logger = AuditLogger(log_path=log_path)
            entry = LogEntry(
                action_id="act_002",
                tool="time_now",
                policy_decision="allowed",
            )
            logger.log(entry)
            assert logger.log_path == log_path
            assert log_path.exists()

    def test_log_writes_json_line(self):
        """Log entries should be written as JSON lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)
            entry = LogEntry(
                action_id="act_001",
                tool="time_now",
                tool_call_args={"location": "Dallas"},
                policy_decision="allowed",
            )
            logger.log(entry)

            with open(logger.log_path) as f:
                content = f.read()

            lines = content.strip().split("\n")
            assert len(lines) == 1

            # Should be valid JSON
            data = json.loads(lines[0])
            assert data["action_id"] == "act_001"
            assert data["tool"] == "time_now"
            assert data["redacted"] is True  # Redaction is applied

    def test_log_multiple_entries(self):
        """Multiple log entries should be appended."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)

            for i in range(3):
                entry = LogEntry(
                    action_id=f"act_{i:03d}",
                    tool="test_tool",
                    policy_decision="allowed",
                )
                logger.log(entry)

            with open(logger.log_path) as f:
                lines = [line for line in f.readlines() if line.strip()]

            assert len(lines) == 3

    def test_read_empty_log(self):
        """Reading from non-existent log should return empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)
            entries = logger.read()
            assert entries == []

    def test_read_returns_entries(self):
        """Reading should return all logged entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)

            for i in range(3):
                entry = LogEntry(
                    action_id=f"act_{i:03d}",
                    tool="test_tool",
                    policy_decision="allowed",
                )
                logger.log(entry)

            entries = logger.read()
            assert len(entries) == 3
            assert entries[0].action_id == "act_000"
            assert entries[1].action_id == "act_001"
            assert entries[2].action_id == "act_002"

    def test_read_with_time_range_start(self):
        """Reading with start_time should filter entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)
            now = datetime.now(UTC)

            # Create entries with different timestamps
            for i, hours_ago in enumerate([2, 1, 0]):
                entry = LogEntry(
                    timestamp=now - timedelta(hours=hours_ago),
                    action_id=f"act_{i:03d}",
                    tool="test_tool",
                    policy_decision="allowed",
                )
                logger.log(entry)

            # Read entries from last 1.5 hours
            start_time = now - timedelta(hours=1, minutes=30)
            entries = logger.read(start_time=start_time)

            assert len(entries) == 2  # Only the last two entries

    def test_read_with_time_range_end(self):
        """Reading with end_time should filter entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)
            now = datetime.now(UTC)

            # Create entries with different timestamps
            for i, hours_ago in enumerate([2, 1, 0]):
                entry = LogEntry(
                    timestamp=now - timedelta(hours=hours_ago),
                    action_id=f"act_{i:03d}",
                    tool="test_tool",
                    policy_decision="allowed",
                )
                logger.log(entry)

            # Read entries until 30 minutes ago
            end_time = now - timedelta(minutes=30)
            entries = logger.read(end_time=end_time)

            assert len(entries) == 2  # Only the first two entries

    def test_read_with_time_range_both(self):
        """Reading with both start and end time should filter correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)
            now = datetime.now(UTC)

            # Create entries at hours ago: 3, 2, 1, 0
            for i, hours_ago in enumerate([3, 2, 1, 0]):
                entry = LogEntry(
                    timestamp=now - timedelta(hours=hours_ago),
                    action_id=f"act_{i:03d}",
                    tool="test_tool",
                    policy_decision="allowed",
                )
                logger.log(entry)

            # Read entries between 2.5 and 0.5 hours ago
            start_time = now - timedelta(hours=2, minutes=30)
            end_time = now - timedelta(minutes=30)
            entries = logger.read(start_time=start_time, end_time=end_time)

            assert len(entries) == 2  # Only entries at 2 and 1 hours ago

    def test_read_by_action_id(self):
        """Reading by action ID should return the specific entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)

            for i in range(3):
                entry = LogEntry(
                    action_id=f"act_{i:03d}",
                    tool="test_tool",
                    policy_decision="allowed",
                )
                logger.log(entry)

            entry = logger.read_by_action_id("act_001")
            assert entry is not None
            assert entry.action_id == "act_001"

    def test_read_by_action_id_not_found(self):
        """Reading non-existent action ID should return None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)

            entry = LogEntry(
                action_id="act_001",
                tool="test_tool",
                policy_decision="allowed",
            )
            logger.log(entry)

            result = logger.read_by_action_id("act_nonexistent")
            assert result is None

    def test_export(self):
        """Export should write all entries to a new file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)

            for i in range(3):
                entry = LogEntry(
                    action_id=f"act_{i:03d}",
                    tool="test_tool",
                    policy_decision="allowed",
                )
                logger.log(entry)

            export_path = Path(tmpdir) / "export" / "exported.log"
            count = logger.export(export_path)

            assert count == 3
            assert export_path.exists()

            # Verify exported content
            with open(export_path) as f:
                lines = [line for line in f.readlines() if line.strip()]
            assert len(lines) == 3

    def test_clear(self):
        """Clear should remove all entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)

            entry = LogEntry(
                action_id="act_001",
                tool="test_tool",
                policy_decision="allowed",
            )
            logger.log(entry)
            assert logger.log_path.exists()

            logger.clear()
            assert not logger.log_path.exists()

    def test_custom_log_file_name(self):
        """AuditLogger should support custom log file names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir, log_file="custom.log")
            assert logger.log_path.name == "custom.log"


class TestRedactionInAuditLog:
    """Tests for sensitive data redaction in audit logs."""

    def test_redacts_token_in_args(self):
        """Token in tool args should be redacted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)

            entry = LogEntry(
                action_id="act_001",
                tool="api_call",
                tool_call_args={"url": "https://api.example.com", "token": "secret123"},
                policy_decision="allowed",
            )
            logger.log(entry)

            # Read back and verify redaction
            entries = logger.read()
            assert len(entries) == 1
            assert entries[0].tool_call_args["token"] == "[REDACTED]"
            assert entries[0].tool_call_args["url"] == "https://api.example.com"
            assert entries[0].redacted is True

    def test_redacts_password_in_args(self):
        """Password in tool args should be redacted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)

            entry = LogEntry(
                action_id="act_001",
                tool="login",
                tool_call_args={
                    "username": "admin",
                    "password": "hunter2",
                },  # pragma: allowlist secret -- audit redaction fixture
                policy_decision="allowed",
            )
            logger.log(entry)

            entries = logger.read()
            assert entries[0].tool_call_args["password"] == "[REDACTED]"
            assert entries[0].tool_call_args["username"] == "admin"

    def test_redacts_api_key_in_result(self):
        """API key in tool result should be redacted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)

            entry = LogEntry(
                action_id="act_001",
                tool="get_credentials",
                tool_call_args={},
                policy_decision="allowed",
                tool_result={
                    "api_key": "sk-secret",
                    "endpoint": "https://api.example.com",
                },  # pragma: allowlist secret -- audit redaction fixture
            )
            logger.log(entry)

            entries = logger.read()
            assert entries[0].tool_result["api_key"] == "[REDACTED]"
            assert entries[0].tool_result["endpoint"] == "https://api.example.com"

    def test_redacts_nested_sensitive_data(self):
        """Nested sensitive data should be redacted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)

            entry = LogEntry(
                action_id="act_001",
                tool="config",
                tool_call_args={
                    "config": {
                        "database": {
                            "host": "localhost",
                            "password": "db_secret",  # pragma: allowlist secret -- audit redaction fixture
                        }
                    }
                },
                policy_decision="allowed",
            )
            logger.log(entry)

            entries = logger.read()
            assert entries[0].tool_call_args["config"]["database"]["password"] == "[REDACTED]"
            assert entries[0].tool_call_args["config"]["database"]["host"] == "localhost"

    def test_redacts_authorization_header(self):
        """Authorization header should be redacted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)

            entry = LogEntry(
                action_id="act_001",
                tool="http_request",
                tool_call_args={
                    "headers": {
                        "Content-Type": "application/json",
                        "Authorization": "Bearer xyz123",
                    }
                },
                policy_decision="allowed",
            )
            logger.log(entry)

            entries = logger.read()
            assert entries[0].tool_call_args["headers"]["Authorization"] == "[REDACTED]"
            assert entries[0].tool_call_args["headers"]["Content-Type"] == "application/json"


class TestAuditLoggerSingleton:
    """Tests for the audit logger singleton pattern."""

    def test_get_audit_logger_returns_same_instance(self):
        """get_audit_logger should return the same instance."""
        reset_audit_logger()
        logger1 = get_audit_logger()
        logger2 = get_audit_logger()
        assert logger1 is logger2

    def test_reset_audit_logger_clears_singleton(self):
        """reset_audit_logger should clear the singleton."""
        reset_audit_logger()
        logger1 = get_audit_logger()
        reset_audit_logger()
        logger2 = get_audit_logger()
        assert logger1 is not logger2


class TestDefaultPaths:
    """Tests for default log paths."""

    def test_default_log_dir(self):
        """DEFAULT_LOG_DIR should be data/logs."""
        assert DEFAULT_LOG_DIR == Path("data/logs")

    def test_default_log_file(self):
        """DEFAULT_LOG_FILE should be audit.log."""
        assert DEFAULT_LOG_FILE == "audit.log"
