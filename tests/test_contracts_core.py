"""Tests for Rex core contracts and schemas."""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

# Ensure pydantic is available
pytest.importorskip("pydantic")

from rex.contracts import (
    CONTRACT_VERSION,
    Action,
    Approval,
    EvidenceRef,
    Notification,
    Task,
    ToolCall,
    ToolResult,
    get_version_info,
    redact_sensitive_keys,
)
from rex.contracts.core import ALL_MODELS


class TestVersionInfo:
    """Tests for contract version functionality."""

    def test_contract_version_is_string(self):
        """Contract version should be a valid semver-like string."""
        assert isinstance(CONTRACT_VERSION, str)
        assert len(CONTRACT_VERSION.split(".")) == 3

    def test_get_version_info_returns_dict(self):
        """get_version_info should return expected keys."""
        info = get_version_info()
        assert isinstance(info, dict)
        assert "version" in info
        assert "compatibility" in info
        assert "status" in info
        assert info["version"] == CONTRACT_VERSION


class TestModelInstantiation:
    """Tests for creating model instances with minimal valid fields."""

    def test_evidence_ref_minimal(self):
        """EvidenceRef should work with minimal required fields."""
        ref = EvidenceRef(evidence_id="ev_001", kind="log")
        assert ref.evidence_id == "ev_001"
        assert ref.kind == "log"
        assert ref.uri is None
        assert ref.sha256 is None
        assert isinstance(ref.created_at, datetime)

    def test_tool_call_minimal(self):
        """ToolCall should work with minimal required fields."""
        call = ToolCall(tool="time_now")
        assert call.tool == "time_now"
        assert call.args == {}
        assert call.requested_by is None
        assert isinstance(call.created_at, datetime)

    def test_tool_result_minimal(self):
        """ToolResult should work with minimal required fields."""
        result = ToolResult(tool="time_now", ok=True)
        assert result.tool == "time_now"
        assert result.ok is True
        assert result.output is None
        assert result.error is None
        assert result.evidence == []

    def test_approval_minimal(self):
        """Approval should work with minimal required fields."""
        approval = Approval(approval_id="apr_001", status="pending")
        assert approval.approval_id == "apr_001"
        assert approval.status == "pending"
        assert approval.reason is None
        assert isinstance(approval.requested_at, datetime)

    def test_action_minimal(self):
        """Action should work with minimal required fields."""
        action = Action(action_id="act_001", kind="tool_call")
        assert action.action_id == "act_001"
        assert action.kind == "tool_call"
        assert action.risk == "low"
        assert action.tool_call is None

    def test_task_minimal(self):
        """Task should work with minimal required fields."""
        task = Task(task_id="task_001", title="Test task")
        assert task.task_id == "task_001"
        assert task.title == "Test task"
        assert task.status == "queued"
        assert task.actions == []

    def test_notification_minimal(self):
        """Notification should work with minimal required fields."""
        notif = Notification(
            notification_id="notif_001",
            channel="dashboard",
            title="Test",
            body="Test body",
        )
        assert notif.notification_id == "notif_001"
        assert notif.channel == "dashboard"
        assert notif.priority == "normal"
        assert notif.metadata == {}


class TestJSONRoundTrip:
    """Tests for JSON serialization/deserialization."""

    def test_evidence_ref_json_roundtrip(self):
        """EvidenceRef should serialize and deserialize correctly."""
        original = EvidenceRef(
            evidence_id="ev_001",
            kind="screenshot",
            uri="s3://bucket/file.png",
            sha256="abc123",
        )
        json_str = original.model_dump_json()
        restored = EvidenceRef.model_validate_json(json_str)
        assert restored.evidence_id == original.evidence_id
        assert restored.kind == original.kind
        assert restored.uri == original.uri

    def test_tool_call_json_roundtrip(self):
        """ToolCall should serialize and deserialize correctly."""
        original = ToolCall(
            tool="weather_now",
            args={"location": "Dallas, TX", "units": "imperial"},
            requested_by="user:james",
        )
        json_str = original.model_dump_json()
        restored = ToolCall.model_validate_json(json_str)
        assert restored.tool == original.tool
        assert restored.args == original.args
        assert restored.requested_by == original.requested_by

    def test_tool_result_json_roundtrip(self):
        """ToolResult should serialize and deserialize correctly."""
        evidence = EvidenceRef(evidence_id="ev_001", kind="log")
        original = ToolResult(
            tool="time_now",
            ok=True,
            output={"local_time": "2024-01-15 10:30", "timezone": "America/Chicago"},
            evidence=[evidence],
        )
        json_str = original.model_dump_json()
        restored = ToolResult.model_validate_json(json_str)
        assert restored.tool == original.tool
        assert restored.ok == original.ok
        assert restored.output == original.output
        assert len(restored.evidence) == 1

    def test_approval_json_roundtrip(self):
        """Approval should serialize and deserialize correctly."""
        original = Approval(
            approval_id="apr_001",
            status="approved",
            reason="Routine action",
            requested_by="scheduler",
            decided_by="james",
        )
        json_str = original.model_dump_json()
        restored = Approval.model_validate_json(json_str)
        assert restored.approval_id == original.approval_id
        assert restored.status == original.status
        assert restored.reason == original.reason

    def test_action_json_roundtrip(self):
        """Action should serialize and deserialize correctly."""
        tool_call = ToolCall(tool="time_now", args={"location": "Dallas"})
        original = Action(
            action_id="act_001",
            task_id="task_001",
            kind="tool_call",
            risk="medium",
            tool_call=tool_call,
        )
        json_str = original.model_dump_json()
        restored = Action.model_validate_json(json_str)
        assert restored.action_id == original.action_id
        assert restored.tool_call.tool == "time_now"

    def test_task_json_roundtrip(self):
        """Task should serialize and deserialize correctly."""
        action = Action(action_id="act_001", kind="tool_call")
        original = Task(
            task_id="task_001",
            title="Check the time",
            status="completed",
            requested_by="user:james",
            actions=[action],
            summary="Successfully retrieved time",
        )
        json_str = original.model_dump_json()
        restored = Task.model_validate_json(json_str)
        assert restored.task_id == original.task_id
        assert restored.title == original.title
        assert len(restored.actions) == 1

    def test_notification_json_roundtrip(self):
        """Notification should serialize and deserialize correctly."""
        original = Notification(
            notification_id="notif_001",
            channel="email",
            priority="urgent",
            title="Alert",
            body="Something happened",
            metadata={"task_id": "task_001", "count": 5},
        )
        json_str = original.model_dump_json()
        restored = Notification.model_validate_json(json_str)
        assert restored.notification_id == original.notification_id
        assert restored.channel == original.channel
        assert restored.metadata == original.metadata


class TestRedactSensitiveKeys:
    """Tests for the redact_sensitive_keys utility function."""

    def test_redacts_token_key(self):
        """Should redact keys containing 'token'."""
        data = {"token": "secret123", "name": "test"}
        result = redact_sensitive_keys(data)
        assert result["token"] == "[REDACTED]"
        assert result["name"] == "test"

    def test_redacts_access_token(self):
        """Should redact 'access_token' key."""
        data = {"access_token": "abc123", "user_id": "user1"}
        result = redact_sensitive_keys(data)
        assert result["access_token"] == "[REDACTED]"
        assert result["user_id"] == "user1"

    def test_redacts_password(self):
        """Should redact 'password' key."""
        data = {
            "password": "hunter2",  # pragma: allowlist secret -- redaction fixture
            "username": "admin",
        }
        result = redact_sensitive_keys(data)
        assert result["password"] == "[REDACTED]"
        assert result["username"] == "admin"

    def test_redacts_api_key(self):
        """Should redact 'api_key' key."""
        data = {
            "api_key": "sk-xxx",  # pragma: allowlist secret -- redaction fixture
            "endpoint": "https://api.example.com",
        }
        result = redact_sensitive_keys(data)
        assert result["api_key"] == "[REDACTED]"
        assert result["endpoint"] == "https://api.example.com"

    def test_redacts_secret(self):
        """Should redact keys containing 'secret'."""
        data = {
            "client_secret": "secret123",  # pragma: allowlist secret -- redaction fixture
            "client_id": "abc",
        }  # pragma: allowlist secret -- redaction fixture
        result = redact_sensitive_keys(data)
        assert result["client_secret"] == "[REDACTED]"
        assert result["client_id"] == "abc"

    def test_redacts_authorization(self):
        """Should redact 'authorization' key."""
        data = {"authorization": "Bearer xyz", "content_type": "json"}
        result = redact_sensitive_keys(data)
        assert result["authorization"] == "[REDACTED]"

    def test_redacts_nested_dicts(self):
        """Should redact sensitive keys in nested dictionaries."""
        data = {
            "config": {
                "api_key": "secret",  # pragma: allowlist secret -- redaction fixture
                "timeout": 30,
            },
            "name": "test",
        }
        result = redact_sensitive_keys(data)
        assert result["config"]["api_key"] == "[REDACTED]"
        assert result["config"]["timeout"] == 30
        assert result["name"] == "test"

    def test_redacts_in_lists(self):
        """Should redact sensitive keys in lists of dicts."""
        data = {
            "providers": [
                {"name": "provider1", "token": "abc"},
                {"name": "provider2", "token": "xyz"},
            ]
        }
        result = redact_sensitive_keys(data)
        assert result["providers"][0]["token"] == "[REDACTED]"
        assert result["providers"][1]["token"] == "[REDACTED]"
        assert result["providers"][0]["name"] == "provider1"

    def test_deeply_nested_redaction(self):
        """Should redact sensitive keys in deeply nested structures."""
        data = {
            "level1": {
                "level2": {
                    "level3": {
                        "password": "deep_secret",  # pragma: allowlist secret -- redaction fixture
                        "value": 42,
                    }
                }
            }
        }
        result = redact_sensitive_keys(data)
        assert result["level1"]["level2"]["level3"]["password"] == "[REDACTED]"
        assert result["level1"]["level2"]["level3"]["value"] == 42

    def test_case_insensitive_redaction(self):
        """Should redact keys regardless of case."""
        data = {
            "API_KEY": "secret",  # pragma: allowlist secret -- redaction fixture
            "Password": "hunter2",
            "TOKEN": "abc",
        }
        result = redact_sensitive_keys(data)
        assert result["API_KEY"] == "[REDACTED]"
        assert result["Password"] == "[REDACTED]"
        assert result["TOKEN"] == "[REDACTED]"

    def test_custom_redacted_value(self):
        """Should support custom redaction placeholder."""
        data = {"password": "secret"}
        result = redact_sensitive_keys(data, redacted_value="***")
        assert result["password"] == "***"

    def test_does_not_modify_original(self):
        """Should not modify the original data structure."""
        data = {"password": "secret", "nested": {"token": "abc"}}
        result = redact_sensitive_keys(data)
        assert data["password"] == "secret"
        assert data["nested"]["token"] == "abc"
        assert result["password"] == "[REDACTED]"

    def test_handles_non_dict_input(self):
        """Should handle non-dict inputs gracefully."""
        assert redact_sensitive_keys("string") == "string"
        assert redact_sensitive_keys(123) == 123
        assert redact_sensitive_keys(None) is None
        assert redact_sensitive_keys([1, 2, 3]) == [1, 2, 3]


class TestSchemaExport:
    """Tests for the schema export script."""

    def test_export_script_produces_files(self, monkeypatch):
        """Schema export script should produce expected files."""
        from scripts import export_contract_schemas

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            export_contract_schemas.export_schemas(output_dir)

            # Check that index.json was created
            index_path = output_dir / "index.json"
            assert index_path.exists()

            # Check index content
            with open(index_path) as f:
                index = json.load(f)
            assert "contract_version" in index
            assert "generated_at" in index
            assert "schemas" in index
            assert index["contract_version"] == CONTRACT_VERSION

            # Check that all model schemas were created
            for model in ALL_MODELS:
                schema_path = output_dir / f"{model.__name__}.json"
                assert schema_path.exists(), f"Missing schema file: {model.__name__}.json"

                with open(schema_path) as f:
                    schema = json.load(f)
                assert "$comment" in schema
                assert CONTRACT_VERSION in schema["$comment"]

    def test_all_models_in_export_list(self):
        """ALL_MODELS should contain all expected models."""
        model_names = {m.__name__ for m in ALL_MODELS}
        expected = {
            "EvidenceRef",
            "ToolCall",
            "ToolResult",
            "Approval",
            "Action",
            "Task",
            "Notification",
        }
        assert model_names == expected


class TestFlaskContractsEndpoint:
    """Tests for the /contracts Flask endpoint."""

    def _load_app(self, monkeypatch):
        """Load the Flask app for testing."""
        pytest.importorskip("flask")

        monkeypatch.setenv("REX_TESTING", "true")

        if "flask_proxy" in sys.modules:
            module = importlib.reload(sys.modules["flask_proxy"])
        else:
            module = importlib.import_module("flask_proxy")

        return module.app, module

    def test_contracts_endpoint_returns_version(self, monkeypatch):
        """GET /contracts should return contract version."""
        app, module = self._load_app(monkeypatch)

        with app.test_client() as client:
            response = client.get("/contracts")

        assert response.status_code == 200
        payload = response.get_json()
        assert "contract_version" in payload
        assert payload["contract_version"] == CONTRACT_VERSION

    def test_contracts_endpoint_returns_models(self, monkeypatch):
        """GET /contracts should return model names."""
        app, module = self._load_app(monkeypatch)

        with app.test_client() as client:
            response = client.get("/contracts")

        assert response.status_code == 200
        payload = response.get_json()
        assert "models" in payload
        assert isinstance(payload["models"], list)
        assert "Task" in payload["models"]
        assert "Action" in payload["models"]
        assert "ToolCall" in payload["models"]

    def test_contracts_endpoint_returns_schema_path(self, monkeypatch):
        """GET /contracts should return schema docs path."""
        app, module = self._load_app(monkeypatch)

        with app.test_client() as client:
            response = client.get("/contracts")

        assert response.status_code == 200
        payload = response.get_json()
        assert "schema_docs_path" in payload
        assert payload["schema_docs_path"] == "docs/contracts/"

    def test_contracts_endpoint_no_auth_required(self, monkeypatch):
        """GET /contracts should not require authentication."""
        app, module = self._load_app(monkeypatch)

        # Make request without any auth headers from a "remote" address
        with app.test_client() as client:
            response = client.get(
                "/contracts",
                environ_overrides={"REMOTE_ADDR": "8.8.8.8"},
            )

        # Should still succeed (unlike other endpoints which require auth)
        assert response.status_code == 200
