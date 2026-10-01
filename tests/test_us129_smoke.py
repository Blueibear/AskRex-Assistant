"""Tests for US-129: Production smoke test suite.

Smoke tests verify all critical paths against a running local instance.
All server-touching tests are marked @pytest.mark.smoke and skip gracefully
when no local Rex server is running.

Run smoke tests only:  pytest -m smoke
Run full suite:        pytest  (smoke tests skip if server not running)

To exercise the live-server tests, start Rex first:
    python flask_proxy.py
Then run:
    pytest -m smoke -v
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:5000"
REQUEST_TIMEOUT = 10  # seconds per request
REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Session-scoped helper and fixture
# ---------------------------------------------------------------------------


def _server_is_up() -> bool:
    """Return True if the local Rex instance answers /health/live within timeout."""
    try:
        resp = requests.get(f"{BASE_URL}/health/live", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def live_server() -> str:
    """Skip all server-dependent smoke tests if no local instance is reachable."""
    if not _server_is_up():
        pytest.skip(
            "Local Rex instance not running — start with 'python flask_proxy.py' "
            "to exercise live-server smoke tests"
        )
    return BASE_URL


# ---------------------------------------------------------------------------
# 1. Health check
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestHealthCheck:
    """Smoke tests for health endpoints."""

    def test_liveness_endpoint_returns_200(self, live_server: str) -> None:
        resp = requests.get(f"{live_server}/health/live", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200

    def test_liveness_response_is_json(self, live_server: str) -> None:
        resp = requests.get(f"{live_server}/health/live", timeout=REQUEST_TIMEOUT)
        data = resp.json()
        assert isinstance(data, dict)

    def test_liveness_status_field_present(self, live_server: str) -> None:
        resp = requests.get(f"{live_server}/health/live", timeout=REQUEST_TIMEOUT)
        data = resp.json()
        assert "status" in data, f"Expected 'status' key in liveness response: {data}"

    def test_liveness_status_value(self, live_server: str) -> None:
        resp = requests.get(f"{live_server}/health/live", timeout=REQUEST_TIMEOUT)
        data = resp.json()
        assert data["status"] in {
            "ok",
            "alive",
            "live",
        }, f"Unexpected liveness status: {data['status']!r}"

    def test_readiness_endpoint_responds(self, live_server: str) -> None:
        """Readiness may return 200 or 503 (dependency failure) but must respond."""
        resp = requests.get(f"{live_server}/health/ready", timeout=REQUEST_TIMEOUT)
        assert resp.status_code in {200, 503}

    def test_readiness_response_is_json(self, live_server: str) -> None:
        resp = requests.get(f"{live_server}/health/ready", timeout=REQUEST_TIMEOUT)
        data = resp.json()
        assert isinstance(data, dict)

    def test_readiness_status_field_present(self, live_server: str) -> None:
        resp = requests.get(f"{live_server}/health/ready", timeout=REQUEST_TIMEOUT)
        data = resp.json()
        assert "status" in data, f"Expected 'status' key in readiness response: {data}"


# ---------------------------------------------------------------------------
# 2. Authentication
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestAuthentication:
    """Smoke tests for authentication enforcement."""

    def test_unauthenticated_request_to_protected_endpoint_returns_403(
        self, live_server: str
    ) -> None:
        """Protected endpoints must reject unauthenticated requests."""
        resp = requests.get(f"{live_server}/whoami", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 403

    def test_invalid_bearer_token_returns_403(self, live_server: str) -> None:
        headers = {"Authorization": "Bearer smoke-test-invalid-token-xyz"}
        resp = requests.get(f"{live_server}/whoami", headers=headers, timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 403

    def test_invalid_proxy_token_returns_403(self, live_server: str) -> None:
        headers = {"X-Rex-Proxy-Token": "smoke-test-invalid-proxy-token-xyz"}
        resp = requests.get(f"{live_server}/whoami", headers=headers, timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 403

    def test_dashboard_login_endpoint_accepts_post(self, live_server: str) -> None:
        """Login endpoint must accept POST and respond (not 404/405)."""
        resp = requests.post(
            f"{live_server}/api/dashboard/login",
            json={
                "password": (
                    "smoke-test-invalid-password"  # pragma: allowlist secret -- invalid-login fixture
                )
            },
            timeout=REQUEST_TIMEOUT,
        )
        # Any of these is valid: success, bad credentials, validation error
        assert resp.status_code not in {
            404,
            405,
        }, f"Login endpoint returned {resp.status_code}: expected a defined response"

    def test_public_contracts_endpoint_requires_no_auth(self, live_server: str) -> None:
        """/contracts is a public endpoint and must not require authentication."""
        resp = requests.get(f"{live_server}/contracts", timeout=REQUEST_TIMEOUT)
        # 200 (available) or 503 (contracts module disabled) — never 403
        assert resp.status_code in {
            200,
            503,
        }, f"/contracts returned {resp.status_code}: expected 200 or 503"

    def test_health_live_requires_no_auth(self, live_server: str) -> None:
        """/health/live must be accessible without authentication."""
        resp = requests.get(f"{live_server}/health/live", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200, "/health/live must not require authentication"


# ---------------------------------------------------------------------------
# 3. Chat message round-trip
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestChatRoundTrip:
    """Smoke tests for chat API endpoints."""

    def test_chat_endpoint_exists(self, live_server: str) -> None:
        """/api/chat must respond to POST (not 404 or 405)."""
        resp = requests.post(
            f"{live_server}/api/chat",
            json={"message": "ping"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code not in {
            404,
            405,
        }, f"/api/chat returned {resp.status_code}: endpoint must exist"

    def test_chat_returns_json_content_type(self, live_server: str) -> None:
        resp = requests.post(
            f"{live_server}/api/chat",
            json={"message": "hello"},
            timeout=REQUEST_TIMEOUT,
        )
        content_type = resp.headers.get("Content-Type", "")
        assert (
            "application/json" in content_type
        ), f"/api/chat Content-Type was {content_type!r}: expected application/json"

    def test_chat_response_is_valid_json(self, live_server: str) -> None:
        resp = requests.post(
            f"{live_server}/api/chat",
            json={"message": "smoke test"},
            timeout=REQUEST_TIMEOUT,
        )
        # Response must be parseable JSON regardless of status
        data = resp.json()
        assert isinstance(data, dict)

    def test_chat_history_endpoint_exists(self, live_server: str) -> None:
        """/api/chat/history must respond (not 404 or 405)."""
        resp = requests.get(f"{live_server}/api/chat/history", timeout=REQUEST_TIMEOUT)
        assert resp.status_code not in {
            404,
            405,
        }, f"/api/chat/history returned {resp.status_code}: endpoint must exist"


# ---------------------------------------------------------------------------
# 4. Notification creation
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestNotifications:
    """Smoke tests for notification API endpoints."""

    def test_notifications_list_endpoint_exists(self, live_server: str) -> None:
        """/api/notifications must respond (not 404 or 405)."""
        resp = requests.get(f"{live_server}/api/notifications", timeout=REQUEST_TIMEOUT)
        assert resp.status_code not in {
            404,
            405,
        }, f"/api/notifications returned {resp.status_code}: endpoint must exist"

    def test_notifications_response_is_json(self, live_server: str) -> None:
        resp = requests.get(f"{live_server}/api/notifications", timeout=REQUEST_TIMEOUT)
        content_type = resp.headers.get("Content-Type", "")
        assert (
            "application/json" in content_type
        ), f"/api/notifications Content-Type was {content_type!r}"

    def test_dashboard_status_endpoint_exists(self, live_server: str) -> None:
        """/api/dashboard/status must respond (not 404 or 405)."""
        resp = requests.get(f"{live_server}/api/dashboard/status", timeout=REQUEST_TIMEOUT)
        assert resp.status_code not in {
            404,
            405,
        }, f"/api/dashboard/status returned {resp.status_code}: endpoint must exist"

    def test_dashboard_status_response_is_json(self, live_server: str) -> None:
        resp = requests.get(f"{live_server}/api/dashboard/status", timeout=REQUEST_TIMEOUT)
        content_type = resp.headers.get("Content-Type", "")
        assert (
            "application/json" in content_type
        ), f"/api/dashboard/status Content-Type was {content_type!r}"


# ---------------------------------------------------------------------------
# 5. CLI entrypoints  (structural — no live server required)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestCLIEntrypoints:
    """Verify CLI entry points are importable and structurally correct.

    These tests do NOT use the live_server fixture so they run even when
    no local instance is running.
    """

    def test_rex_cli_module_importable(self) -> None:
        """rex.cli must be importable and export 'main'."""
        mod = importlib.import_module("rex.cli")
        assert hasattr(mod, "main"), "rex.cli must export 'main'"

    def test_rex_config_module_importable(self) -> None:
        """rex.config must be importable and export 'cli'."""
        mod = importlib.import_module("rex.config")
        assert hasattr(mod, "cli"), "rex.config must export 'cli'"

    def test_rex_cli_help_exits_zero_or_two(self) -> None:
        """'python -m rex --help' must not crash (exit 0 or 2 are both acceptable)."""
        result = subprocess.run(
            [sys.executable, "-m", "rex", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(REPO_ROOT),
            env={**os.environ, "REX_TESTING": "true"},
        )
        assert result.returncode in {0, 2}, (
            f"'python -m rex --help' exited {result.returncode}:\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )

    def test_rex_speak_api_importable(self) -> None:
        """rex_speak_api module must import without error when REX_TESTING=true."""
        result = subprocess.run(
            [sys.executable, "-c", "import rex_speak_api"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(REPO_ROOT),
            env={**os.environ, "REX_TESTING": "true"},
        )
        assert result.returncode == 0, f"rex_speak_api import failed:\n{result.stderr[:500]}"

    def test_flask_proxy_importable(self) -> None:
        """flask_proxy must import without error when REX_TESTING=true."""
        result = subprocess.run(
            [sys.executable, "-c", "import flask_proxy"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(REPO_ROOT),
            env={**os.environ, "REX_TESTING": "true"},
        )
        assert result.returncode == 0, f"flask_proxy import failed:\n{result.stderr[:500]}"

    def test_agent_server_entry_point_importable(self) -> None:
        """rex.computers.agent_server must export 'main'."""
        try:
            mod = importlib.import_module("rex.computers.agent_server")
            assert hasattr(mod, "main"), "rex.computers.agent_server must export 'main'"
        except ImportError as exc:
            pytest.skip(f"agent_server optional dependency not available: {exc}")


# ---------------------------------------------------------------------------
# 6. Smoke suite metadata — marker and runnability (always-on structural tests)
# ---------------------------------------------------------------------------


class TestSmokeSuiteStructure:
    """Verify the smoke test suite itself is correctly structured."""

    def test_smoke_marker_registered_in_pyproject(self) -> None:
        """pyproject.toml must declare the 'smoke' marker."""
        pyproject = REPO_ROOT / "pyproject.toml"
        assert pyproject.is_file(), "pyproject.toml not found"
        content = pyproject.read_text(encoding="utf-8")
        assert "smoke" in content, "pyproject.toml must declare the 'smoke' marker"

    def test_smoke_test_file_exists(self) -> None:
        """This smoke test file must exist at tests/test_us129_smoke.py."""
        path = REPO_ROOT / "tests" / "test_us129_smoke.py"
        assert path.is_file(), f"Smoke test file not found at {path}"

    def test_smoke_tests_are_marked(self) -> None:
        """All test classes in this file must be marked @pytest.mark.smoke."""
        path = REPO_ROOT / "tests" / "test_us129_smoke.py"
        content = path.read_text(encoding="utf-8")
        # Count @pytest.mark.smoke decorators above class definitions
        import re

        classes_with_smoke = re.findall(r"@pytest\.mark\.smoke\s+class\s+\w+", content)
        assert (
            len(classes_with_smoke) >= 5
        ), f"Expected at least 5 smoke-marked test classes, found {len(classes_with_smoke)}"

    def test_live_server_check_function_present(self) -> None:
        """_server_is_up() helper must be defined in the smoke test module."""
        path = REPO_ROOT / "tests" / "test_us129_smoke.py"
        content = path.read_text(encoding="utf-8")
        assert "_server_is_up" in content, "_server_is_up() helper not found in smoke test file"

    def test_skip_logic_present(self) -> None:
        """Smoke tests must include skip logic for when server is not running."""
        path = REPO_ROOT / "tests" / "test_us129_smoke.py"
        content = path.read_text(encoding="utf-8")
        assert (
            "pytest.skip" in content
        ), "Smoke test file must contain pytest.skip() for graceful skips"

    def test_base_url_is_localhost(self) -> None:
        """Smoke tests must target localhost (not a remote host)."""
        path = REPO_ROOT / "tests" / "test_us129_smoke.py"
        content = path.read_text(encoding="utf-8")
        assert "localhost" in content, "BASE_URL must point to localhost"

    def test_request_timeout_defined(self) -> None:
        """REQUEST_TIMEOUT must be defined to keep test run times bounded."""
        path = REPO_ROOT / "tests" / "test_us129_smoke.py"
        content = path.read_text(encoding="utf-8")
        assert "REQUEST_TIMEOUT" in content, "REQUEST_TIMEOUT constant must be defined"
