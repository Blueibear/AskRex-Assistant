"""Offline tests for WooCommerce write actions (Cycle 6.3).

All tests run without any network calls.
- HTTP layer mocked via ``unittest.mock.patch`` on ``requests.put`` / ``requests.post``.
- DNS used in SSRF checks mocked via ``socket.getaddrinfo``.
- Approvals are stored in a pytest ``tmp_path`` directory (never in tracked files).

Coverage
--------
Gating (approval flow):
  1. First run without an existing approval creates a pending approval and
     returns exit code 1 without performing any network call.
  2. Second run with a pending approval but without --yes also exits with 1
     and no network call.
  3. Third run with an approved approval and --yes performs the mocked HTTP
     call and returns exit code 0.

Deterministic approvals:
  - Repeated invocations of check_wc_write_policy produce the same approval_id
    for identical inputs.

Payload correctness:
  - Order status PUT payload contains correct fields.
  - Order note POST payload contains correct fields (note + customer_note=false).
  - Coupon create POST payload contains all expected fields.
  - Coupon disable PUT payload is {"status": "draft"}.

Input validation:
  - Empty coupon code rejected before any policy or network call.
  - Non-positive amount rejected.
  - Invalid discount type rejected.
  - Bad date format rejected.
  - Non-positive coupon_id rejected.

Security:
  - SSRF DNS checks remain in effect but are fully mocked.
  - Approval summary payload never contains consumer key/secret.
  - Error messages are sanitized (no raw credentials in exception output).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rex.contracts import RiskLevel
from rex.policy import ActionPolicy
from rex.policy_engine import PolicyEngine
from rex.woocommerce.client import WooCommerceClient, WriteResult
from rex.woocommerce.config import WooCommerceConfig, WooCommerceSiteConfig
from rex.woocommerce.service import WooCommerceService
from rex.woocommerce.write_policy import (
    WC_COUPON_CREATE_TOOL,
    WC_COUPON_DISABLE_TOOL,
    WC_ORDER_SET_STATUS_TOOL,
    WC_WRITE_WORKFLOW_ID,
    _action_step_id,
    check_wc_write_policy,
    find_pending_or_approved_wc_approval,
)
from rex.workflow import WorkflowApproval

# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------


def _mock_public_addrinfo(*_args, **_kwargs):
    """Return a stable public IP for SSRF-safe URL validation in tests."""
    return [(0, 0, 0, "", ("93.184.216.34", 0))]


@pytest.fixture(autouse=True)
def _patch_getaddrinfo():
    """Keep all tests fully offline by mocking DNS used in SSRF checks."""
    with patch("socket.getaddrinfo", side_effect=_mock_public_addrinfo):
        yield


def _make_site_config(
    site_id: str = "myshop",
    base_url: str = "https://example.com",
) -> WooCommerceSiteConfig:
    return WooCommerceSiteConfig(
        id=site_id,
        base_url=base_url,
        enabled=True,
        consumer_key_ref="wc:myshop:key",
        consumer_secret_ref="wc:myshop:secret",  # pragma: allowlist secret -- credential reference fixture
    )


def _make_service(site: WooCommerceSiteConfig | None = None) -> WooCommerceService:
    if site is None:
        site = _make_site_config()
    creds = MagicMock()
    creds.get_token.side_effect = lambda ref: (
        "ck_testkey" if ref.endswith(":key") else "cs_testsecret"
    )
    return WooCommerceService(
        wc_config=WooCommerceConfig(sites=[site]),
        credential_manager=creds,
    )


def _make_write_ok_response(data: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = data
    return resp


def _make_http_error(status_code: int) -> MagicMock:
    import requests

    resp = MagicMock()
    resp.status_code = status_code
    err = requests.HTTPError(response=resp)
    resp.raise_for_status.side_effect = err
    return resp


def _make_approval_engine() -> PolicyEngine:
    """Default engine: HIGH risk, requires approval."""
    return PolicyEngine()


def _make_auto_engine() -> PolicyEngine:
    """Custom engine: LOW risk, auto-execute."""
    return PolicyEngine(
        policies=[
            ActionPolicy(
                tool_name=WC_ORDER_SET_STATUS_TOOL,
                risk=RiskLevel.LOW,
                allow_auto=True,
            )
        ]
    )


def _make_denied_engine(tool: str = WC_ORDER_SET_STATUS_TOOL) -> PolicyEngine:
    """Engine that explicitly denies the specified tool."""
    return PolicyEngine(
        policies=[
            ActionPolicy(
                tool_name=tool,
                risk=RiskLevel.HIGH,
                allow_auto=False,
                allowed_recipients=["nobody"],
            )
        ]
    )


def _save_approved_wc_approval(
    action: str,
    site_id: str,
    identifiers: dict,
    approval_dir: Path,
) -> WorkflowApproval:
    """Helper: write a pre-approved approval record to disk."""
    step_id = _action_step_id(action, site_id, identifiers)
    approval = WorkflowApproval(
        workflow_id=WC_WRITE_WORKFLOW_ID,
        step_id=step_id,
        status="approved",
        requested_by="cli",
        step_description=f"Pre-approved {action} on {site_id}",
        tool_call_summary=json.dumps({"action": action, "site_id": site_id}),
    )
    approval.save(approval_dir)
    return approval


# ===========================================================================
# WooCommerceClient write method tests
# ===========================================================================


class TestWooCommerceClientSetOrderStatus:
    def _client(self) -> WooCommerceClient:
        return WooCommerceClient(
            "https://example.com",
            consumer_key="ck_testkey",
            consumer_secret="cs_testsecret",  # pragma: allowlist secret -- test credential fixture
            site_id="myshop",
        )

    def test_calls_correct_url(self):
        """set_order_status() calls PUT /wp-json/wc/v3/orders/<id>."""
        client = self._client()
        with patch("requests.put") as mock_put:
            mock_put.return_value = _make_write_ok_response({"id": 101, "status": "completed"})
            client.set_order_status(101, status="completed")

        called_url = mock_put.call_args[0][0]
        assert called_url == "https://example.com/wp-json/wc/v3/orders/101"

    def test_payload_contains_status(self):
        """PUT payload includes {"status": <new_status>}."""
        client = self._client()
        with patch("requests.put") as mock_put:
            mock_put.return_value = _make_write_ok_response({"id": 101, "status": "completed"})
            client.set_order_status(101, status="completed")

        kwargs = mock_put.call_args[1]
        assert kwargs.get("json") == {"status": "completed"}

    def test_passes_basic_auth(self):
        """Consumer key and secret are passed as HTTP Basic Auth."""
        client = self._client()
        with patch("requests.put") as mock_put:
            mock_put.return_value = _make_write_ok_response({"id": 101, "status": "completed"})
            client.set_order_status(101, status="completed")

        kwargs = mock_put.call_args[1]
        assert kwargs.get("auth") == ("ck_testkey", "cs_testsecret")

    def test_returns_ok_on_success(self):
        client = self._client()
        with patch("requests.put") as mock_put:
            mock_put.return_value = _make_write_ok_response({"id": 101, "status": "completed"})
            result = client.set_order_status(101, status="completed")

        assert result.ok is True
        assert result.data is not None
        assert result.data["status"] == "completed"

    def test_returns_error_on_http_error(self):
        client = self._client()
        with patch("requests.put") as mock_put:
            mock_put.return_value = _make_http_error(422)
            result = client.set_order_status(101, status="completed")

        assert result.ok is False
        assert result.error is not None
        # Status code should appear in sanitized message
        assert "422" in result.error

    def test_non_dict_response(self):
        client = self._client()
        with patch("requests.put") as mock_put:
            mock_put.return_value = _make_write_ok_response(["not", "a", "dict"])
            result = client.set_order_status(101, status="completed")

        assert result.ok is False
        assert result.error is not None


class TestWooCommerceClientAddOrderNote:
    def _client(self) -> WooCommerceClient:
        return WooCommerceClient(
            "https://example.com",
            consumer_key="ck_testkey",
            consumer_secret="cs_testsecret",  # pragma: allowlist secret -- client fixture
            site_id="myshop",
        )

    def test_calls_correct_url(self):
        """add_order_note() calls POST /wp-json/wc/v3/orders/<id>/notes."""
        client = self._client()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_write_ok_response({"id": 5, "note": "Hello"})
            client.add_order_note(101, note="Hello")

        called_url = mock_post.call_args[0][0]
        assert called_url == "https://example.com/wp-json/wc/v3/orders/101/notes"

    def test_payload_note_and_customer_note_false(self):
        """POST payload includes note text and customer_note=False."""
        client = self._client()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_write_ok_response({"id": 5, "note": "Internal"})
            client.add_order_note(101, note="Internal")

        kwargs = mock_post.call_args[1]
        assert kwargs.get("json") == {"note": "Internal", "customer_note": False}

    def test_customer_note_true(self):
        """customer_note=True is forwarded in the payload."""
        client = self._client()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_write_ok_response({"id": 6, "note": "Hi"})
            client.add_order_note(101, note="Hi", customer_note=True)

        kwargs = mock_post.call_args[1]
        assert kwargs.get("json", {}).get("customer_note") is True

    def test_returns_ok_on_success(self):
        client = self._client()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_write_ok_response({"id": 5, "note": "Done"})
            result = client.add_order_note(101, note="Done")

        assert result.ok is True

    def test_returns_error_on_failure(self):
        client = self._client()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_http_error(404)
            result = client.add_order_note(101, note="Done")

        assert result.ok is False
        assert "404" in (result.error or "")


class TestWooCommerceClientCreateCoupon:
    def _client(self) -> WooCommerceClient:
        return WooCommerceClient(
            "https://example.com",
            consumer_key="ck_testkey",
            consumer_secret="cs_testsecret",  # pragma: allowlist secret
            site_id="myshop",
        )

    def test_calls_correct_url(self):
        """create_coupon() calls POST /wp-json/wc/v3/coupons."""
        client = self._client()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_write_ok_response({"id": 10, "code": "SAVE10"})
            client.create_coupon(code="SAVE10", amount="10", discount_type="percent")

        called_url = mock_post.call_args[0][0]
        assert called_url == "https://example.com/wp-json/wc/v3/coupons"

    def test_minimal_payload(self):
        """Minimal coupon payload contains code, amount, discount_type."""
        client = self._client()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_write_ok_response({"id": 10, "code": "SAVE10"})
            client.create_coupon(code="SAVE10", amount="10", discount_type="percent")

        kwargs = mock_post.call_args[1]
        payload = kwargs.get("json", {})
        assert payload["code"] == "SAVE10"
        assert payload["amount"] == "10"
        assert payload["discount_type"] == "percent"
        assert "date_expires" not in payload
        assert "usage_limit" not in payload

    def test_full_payload_with_expires_and_usage(self):
        """Optional fields are included in the payload when provided."""
        client = self._client()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_write_ok_response({"id": 11, "code": "PROMO"})
            client.create_coupon(
                code="PROMO",
                amount="15",
                discount_type="fixed_cart",
                date_expires="2026-12-31",
                usage_limit=100,
            )

        kwargs = mock_post.call_args[1]
        payload = kwargs.get("json", {})
        assert payload["date_expires"] == "2026-12-31T00:00:00"
        assert payload["usage_limit"] == 100

    def test_returns_ok_on_success(self):
        client = self._client()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_write_ok_response({"id": 10, "code": "SAVE10"})
            result = client.create_coupon(code="SAVE10", amount="10", discount_type="percent")

        assert result.ok is True
        assert result.data is not None
        assert result.data["code"] == "SAVE10"

    def test_returns_error_on_http_error(self):
        client = self._client()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_http_error(400)
            result = client.create_coupon(code="X", amount="5", discount_type="percent")

        assert result.ok is False
        assert result.error is not None


class TestWooCommerceClientDisableCoupon:
    def _client(self) -> WooCommerceClient:
        return WooCommerceClient(
            "https://example.com",
            consumer_key="ck_testkey",
            consumer_secret="cs_testsecret",  # pragma: allowlist secret
            site_id="myshop",
        )

    def test_calls_correct_url(self):
        """disable_coupon() calls PUT /wp-json/wc/v3/coupons/<id>."""
        client = self._client()
        with patch("requests.put") as mock_put:
            mock_put.return_value = _make_write_ok_response({"id": 55, "status": "draft"})
            client.disable_coupon(55)

        called_url = mock_put.call_args[0][0]
        assert called_url == "https://example.com/wp-json/wc/v3/coupons/55"

    def test_payload_is_draft(self):
        """PUT payload is exactly {"status": "draft"}."""
        client = self._client()
        with patch("requests.put") as mock_put:
            mock_put.return_value = _make_write_ok_response({"id": 55, "status": "draft"})
            client.disable_coupon(55)

        kwargs = mock_put.call_args[1]
        assert kwargs.get("json") == {"status": "draft"}

    def test_returns_ok_on_success(self):
        client = self._client()
        with patch("requests.put") as mock_put:
            mock_put.return_value = _make_write_ok_response({"id": 55, "status": "draft"})
            result = client.disable_coupon(55)

        assert result.ok is True
        assert (result.data or {}).get("status") == "draft"

    def test_returns_error_on_failure(self):
        client = self._client()
        with patch("requests.put") as mock_put:
            mock_put.return_value = _make_http_error(404)
            result = client.disable_coupon(55)

        assert result.ok is False
        assert result.error is not None


# ===========================================================================
# write_policy unit tests
# ===========================================================================


class TestActionStepId:
    def test_same_inputs_produce_same_step_id(self):
        """Deterministic: same inputs → same step_id."""
        sid1 = _action_step_id(
            WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 101, "status": "completed"}
        )
        sid2 = _action_step_id(
            WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 101, "status": "completed"}
        )
        assert sid1 == sid2

    def test_different_status_different_step_id(self):
        """Different status → different step_id."""
        sid1 = _action_step_id(
            WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 101, "status": "completed"}
        )
        sid2 = _action_step_id(
            WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 101, "status": "cancelled"}
        )
        assert sid1 != sid2

    def test_different_order_id_different_step_id(self):
        sid1 = _action_step_id(
            WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 101, "status": "completed"}
        )
        sid2 = _action_step_id(
            WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 202, "status": "completed"}
        )
        assert sid1 != sid2

    def test_different_site_different_step_id(self):
        sid1 = _action_step_id(
            WC_ORDER_SET_STATUS_TOOL, "shop_a", {"order_id": 1, "status": "completed"}
        )
        sid2 = _action_step_id(
            WC_ORDER_SET_STATUS_TOOL, "shop_b", {"order_id": 1, "status": "completed"}
        )
        assert sid1 != sid2

    def test_step_id_has_expected_prefix(self):
        sid = _action_step_id(WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 1})
        assert sid.startswith("wc_myshop_")


class TestCheckWcWritePolicy:
    def test_creates_pending_approval_when_none_exists(self, tmp_path: Path) -> None:
        engine = _make_approval_engine()
        decision, approval = check_wc_write_policy(
            action=WC_ORDER_SET_STATUS_TOOL,
            site_id="myshop",
            identifiers={"order_id": 101, "status": "completed"},
            params={"order_id": 101, "status": "completed"},
            step_description="Update order #101 to completed",
            initiated_by="alice",
            policy_engine=engine,
            approval_dir=tmp_path,
        )

        assert decision.requires_approval is True
        assert decision.denied is False
        assert approval is not None
        assert approval.status == "pending"
        assert approval.workflow_id == WC_WRITE_WORKFLOW_ID

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1

    def test_approval_payload_contains_required_fields(self, tmp_path: Path) -> None:
        engine = _make_approval_engine()
        _, approval = check_wc_write_policy(
            action=WC_COUPON_CREATE_TOOL,
            site_id="myshop",
            identifiers={"code": "SAVE10", "amount": "10", "discount_type": "percent"},
            params={"code": "SAVE10", "amount": "10", "discount_type": "percent"},
            step_description="Create coupon SAVE10",
            initiated_by="bob",
            policy_engine=engine,
            approval_dir=tmp_path,
        )

        assert approval is not None
        payload = json.loads(approval.tool_call_summary)
        assert payload["action"] == WC_COUPON_CREATE_TOOL
        assert payload["site_id"] == "myshop"
        assert payload["code"] == "SAVE10"
        assert payload["initiated_by"] == "bob"
        # No secrets in the payload
        assert "consumer_key" not in approval.tool_call_summary.lower()
        assert "consumer_secret" not in approval.tool_call_summary.lower()
        assert "password" not in approval.tool_call_summary.lower()

    def test_idempotent_second_call_finds_existing_approval(self, tmp_path: Path) -> None:
        engine = _make_approval_engine()

        _, first = check_wc_write_policy(
            action=WC_ORDER_SET_STATUS_TOOL,
            site_id="myshop",
            identifiers={"order_id": 101, "status": "completed"},
            params={"order_id": 101, "status": "completed"},
            step_description="Update order #101",
            policy_engine=engine,
            approval_dir=tmp_path,
        )
        assert first is not None

        _, second = check_wc_write_policy(
            action=WC_ORDER_SET_STATUS_TOOL,
            site_id="myshop",
            identifiers={"order_id": 101, "status": "completed"},
            params={"order_id": 101, "status": "completed"},
            step_description="Update order #101",
            policy_engine=engine,
            approval_dir=tmp_path,
        )
        assert second is not None
        assert second.approval_id == first.approval_id

        # Only one file on disk
        assert len(list(tmp_path.glob("*.json"))) == 1

    def test_returns_approved_approval_when_present(self, tmp_path: Path) -> None:
        _save_approved_wc_approval(
            WC_ORDER_SET_STATUS_TOOL,
            "myshop",
            {"order_id": 101, "status": "completed"},
            tmp_path,
        )
        engine = _make_approval_engine()
        decision, approval = check_wc_write_policy(
            action=WC_ORDER_SET_STATUS_TOOL,
            site_id="myshop",
            identifiers={"order_id": 101, "status": "completed"},
            params={"order_id": 101, "status": "completed"},
            step_description="Update order #101",
            policy_engine=engine,
            approval_dir=tmp_path,
        )

        assert decision.requires_approval is True
        assert approval is not None
        assert approval.status == "approved"

    def test_denied_decision_returns_none_and_no_file(self, tmp_path: Path) -> None:
        engine = _make_denied_engine(WC_ORDER_SET_STATUS_TOOL)
        decision, approval = check_wc_write_policy(
            action=WC_ORDER_SET_STATUS_TOOL,
            site_id="myshop",
            identifiers={"order_id": 101, "status": "completed"},
            params={"order_id": 101, "status": "completed"},
            step_description="Update order #101",
            policy_engine=engine,
            approval_dir=tmp_path,
        )

        assert decision.denied is True
        assert approval is None
        assert list(tmp_path.glob("*.json")) == []

    def test_auto_execute_returns_none_and_no_file(self, tmp_path: Path) -> None:
        engine = _make_auto_engine()
        decision, approval = check_wc_write_policy(
            action=WC_ORDER_SET_STATUS_TOOL,
            site_id="myshop",
            identifiers={"order_id": 101, "status": "completed"},
            params={"order_id": 101, "status": "completed"},
            step_description="Update order #101",
            policy_engine=engine,
            approval_dir=tmp_path,
        )

        assert decision.denied is False
        assert decision.requires_approval is False
        assert approval is None
        assert list(tmp_path.glob("*.json")) == []

    def test_different_identifiers_produce_different_approvals(self, tmp_path: Path) -> None:
        engine = _make_approval_engine()
        _, a1 = check_wc_write_policy(
            action=WC_ORDER_SET_STATUS_TOOL,
            site_id="myshop",
            identifiers={"order_id": 101, "status": "completed"},
            params={},
            step_description="101 completed",
            policy_engine=engine,
            approval_dir=tmp_path,
        )
        _, a2 = check_wc_write_policy(
            action=WC_ORDER_SET_STATUS_TOOL,
            site_id="myshop",
            identifiers={"order_id": 202, "status": "completed"},
            params={},
            step_description="202 completed",
            policy_engine=engine,
            approval_dir=tmp_path,
        )

        assert a1 is not None
        assert a2 is not None
        assert a1.approval_id != a2.approval_id


class TestFindPendingOrApprovedWcApproval:
    def test_returns_none_when_dir_absent(self, tmp_path: Path) -> None:
        missing = tmp_path / "no_such_dir"
        result = find_pending_or_approved_wc_approval(
            WC_ORDER_SET_STATUS_TOOL, "myshop", {}, approval_dir=missing
        )
        assert result is None

    def test_returns_none_when_no_matching_approval(self, tmp_path: Path) -> None:
        step_id = _action_step_id(WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 999})
        approval = WorkflowApproval(
            workflow_id=WC_WRITE_WORKFLOW_ID,
            step_id=step_id,
            status="pending",
            requested_by="cli",
            step_description="other",
            tool_call_summary="{}",
        )
        approval.save(tmp_path)

        result = find_pending_or_approved_wc_approval(
            WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 1}, approval_dir=tmp_path
        )
        assert result is None

    def test_ignores_denied_approvals(self, tmp_path: Path) -> None:
        step_id = _action_step_id(WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 101})
        approval = WorkflowApproval(
            workflow_id=WC_WRITE_WORKFLOW_ID,
            step_id=step_id,
            status="denied",
            requested_by="cli",
            step_description="denied",
            tool_call_summary="{}",
        )
        approval.save(tmp_path)

        result = find_pending_or_approved_wc_approval(
            WC_ORDER_SET_STATUS_TOOL,
            "myshop",
            {"order_id": 101},
            approval_dir=tmp_path,
        )
        assert result is None

    def test_ignores_different_workflow_id(self, tmp_path: Path) -> None:
        step_id = _action_step_id(WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 101})
        approval = WorkflowApproval(
            workflow_id="some_other_workflow",
            step_id=step_id,
            status="approved",
            requested_by="cli",
            step_description="other wf",
            tool_call_summary="{}",
        )
        approval.save(tmp_path)

        result = find_pending_or_approved_wc_approval(
            WC_ORDER_SET_STATUS_TOOL,
            "myshop",
            {"order_id": 101},
            approval_dir=tmp_path,
        )
        assert result is None

    def test_finds_pending_approval(self, tmp_path: Path) -> None:
        step_id = _action_step_id(WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 101})
        approval = WorkflowApproval(
            workflow_id=WC_WRITE_WORKFLOW_ID,
            step_id=step_id,
            status="pending",
            requested_by="cli",
            step_description="pending",
            tool_call_summary="{}",
        )
        approval.save(tmp_path)

        result = find_pending_or_approved_wc_approval(
            WC_ORDER_SET_STATUS_TOOL,
            "myshop",
            {"order_id": 101},
            approval_dir=tmp_path,
        )
        assert result is not None
        assert result.approval_id == approval.approval_id

    def test_finds_approved_approval(self, tmp_path: Path) -> None:
        saved = _save_approved_wc_approval(
            WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 101}, tmp_path
        )
        result = find_pending_or_approved_wc_approval(
            WC_ORDER_SET_STATUS_TOOL,
            "myshop",
            {"order_id": 101},
            approval_dir=tmp_path,
        )
        assert result is not None
        assert result.approval_id == saved.approval_id


# ===========================================================================
# CLI integration tests: set-status
# ===========================================================================


class TestCmdWcOrderSetStatus:
    """Three-step approval flow for rex wc orders set-status."""

    def _make_args(
        self,
        site: str = "myshop",
        order_id: int = 101,
        status: str = "completed",
        note: str | None = None,
        yes: bool = False,
        user: str | None = None,
    ) -> argparse.Namespace:
        args = argparse.Namespace()
        args.wc_command = "orders"
        args.wc_orders_command = "set-status"
        args.site = site
        args.order_id = order_id
        args.status = status
        args.note = note
        args.yes = yes
        args.user = user
        return args

    def _patch_approval_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("rex.woocommerce.write_policy.DEFAULT_APPROVAL_DIR", tmp_path)

    # ------------------------------------------------------------------
    # Step 1: No approval → creates pending approval, no network call
    # ------------------------------------------------------------------

    def test_step1_no_approval_creates_pending(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        from rex.cli import cmd_wc

        self._patch_approval_dir(monkeypatch, tmp_path)

        with patch("requests.put") as mock_put:
            rc = cmd_wc(self._make_args(yes=True))

        assert rc == 1
        mock_put.assert_not_called()
        out = capsys.readouterr().out
        assert "Approval required" in out
        assert "rex approvals --approve" in out

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1

    def test_step1_approval_id_shown_in_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        from rex.cli import cmd_wc

        self._patch_approval_dir(monkeypatch, tmp_path)
        cmd_wc(self._make_args(yes=True))
        out = capsys.readouterr().out
        assert "apr_" in out  # approval_id prefix

    # ------------------------------------------------------------------
    # Step 2: Pending approval exists, --yes supplied → still blocked
    # ------------------------------------------------------------------

    def test_step2_pending_approval_with_yes_still_blocked(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        from rex.cli import cmd_wc

        # Create a pending approval first
        _save_approved_wc_approval(  # actually save a "pending" one via write_policy
            # We need a pending one — use check_wc_write_policy directly
            WC_ORDER_SET_STATUS_TOOL,
            "myshop",
            {"order_id": 101, "status": "completed"},
            tmp_path,
        )
        # Overwrite status to "pending" by saving a pending WorkflowApproval
        step_id = _action_step_id(
            WC_ORDER_SET_STATUS_TOOL, "myshop", {"order_id": 101, "status": "completed"}
        )
        pending = WorkflowApproval(
            workflow_id=WC_WRITE_WORKFLOW_ID,
            step_id=step_id,
            status="pending",
            requested_by="cli",
            step_description="pending",
            tool_call_summary="{}",
        )
        pending.save(tmp_path)

        self._patch_approval_dir(monkeypatch, tmp_path)

        with patch("requests.put") as mock_put:
            rc = cmd_wc(self._make_args(yes=True))

        assert rc == 1
        mock_put.assert_not_called()

    # ------------------------------------------------------------------
    # Step 2b: Approved approval exists, WITHOUT --yes → --yes guard fires
    # ------------------------------------------------------------------

    def test_step2b_approved_without_yes_refuses(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        from rex.cli import cmd_wc

        _save_approved_wc_approval(
            WC_ORDER_SET_STATUS_TOOL,
            "myshop",
            {"order_id": 101, "status": "completed"},
            tmp_path,
        )
        self._patch_approval_dir(monkeypatch, tmp_path)

        with patch("requests.put") as mock_put:
            rc = cmd_wc(self._make_args(yes=False))

        assert rc == 1
        mock_put.assert_not_called()
        out = capsys.readouterr().out
        assert "--yes" in out

    # ------------------------------------------------------------------
    # Step 3: Approved approval + --yes → executes
    # ------------------------------------------------------------------

    def test_step3_approved_with_yes_executes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        from rex.cli import cmd_wc

        _save_approved_wc_approval(
            WC_ORDER_SET_STATUS_TOOL,
            "myshop",
            {"order_id": 101, "status": "completed"},
            tmp_path,
        )
        self._patch_approval_dir(monkeypatch, tmp_path)

        mock_service = MagicMock()
        mock_service.set_order_status.return_value = WriteResult(
            ok=True, data={"id": 101, "status": "completed"}
        )

        with patch("rex.woocommerce.service.get_woocommerce_service", return_value=mock_service):
            rc = cmd_wc(self._make_args(yes=True))

        assert rc == 0
        mock_service.set_order_status.assert_called_once_with("myshop", 101, status="completed")
        out = capsys.readouterr().out
        assert "completed" in out

    def test_step3_with_note_calls_add_order_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        from rex.cli import cmd_wc

        _save_approved_wc_approval(
            WC_ORDER_SET_STATUS_TOOL,
            "myshop",
            {"order_id": 101, "status": "completed"},
            tmp_path,
        )
        self._patch_approval_dir(monkeypatch, tmp_path)

        mock_service = MagicMock()
        mock_service.set_order_status.return_value = WriteResult(
            ok=True, data={"id": 101, "status": "completed"}
        )
        mock_service.add_order_note.return_value = WriteResult(ok=True, data={"id": 5})

        with patch("rex.woocommerce.service.get_woocommerce_service", return_value=mock_service):
            rc = cmd_wc(self._make_args(yes=True, note="Shipped manually"))

        assert rc == 0
        mock_service.add_order_note.assert_called_once_with(
            "myshop", 101, note="Shipped manually", customer_note=False
        )

    # ------------------------------------------------------------------
    # Service error path
    # ------------------------------------------------------------------

    def test_service_error_returns_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        from rex.cli import cmd_wc

        _save_approved_wc_approval(
            WC_ORDER_SET_STATUS_TOOL,
            "myshop",
            {"order_id": 101, "status": "completed"},
            tmp_path,
        )
        self._patch_approval_dir(monkeypatch, tmp_path)

        mock_service = MagicMock()
        mock_service.set_order_status.return_value = WriteResult(
            ok=False, error="HTTP error from WooCommerce API (status=422)"
        )

        with patch("rex.woocommerce.service.get_woocommerce_service", return_value=mock_service):
            rc = cmd_wc(self._make_args(yes=True))

        assert rc == 1
        out = capsys.readouterr().out
        assert "Error" in out
        assert "422" in out


# ===========================================================================
# CLI integration tests: coupons create
# ===========================================================================


class TestCmdWcCouponCreate:
    def _make_args(
        self,
        site: str = "myshop",
        code: str = "SAVE10",
        amount: str = "10",
        discount_type: str = "percent",
        expires: str | None = None,
        usage_limit: int | None = None,
        yes: bool = False,
        user: str | None = None,
    ) -> argparse.Namespace:
        args = argparse.Namespace()
        args.wc_command = "coupons"
        args.wc_coupons_command = "create"
        args.site = site
        args.code = code
        args.amount = amount
        args.type = discount_type
        args.expires = expires
        args.usage_limit = usage_limit
        args.yes = yes
        args.user = user
        return args

    def _patch_approval_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("rex.woocommerce.write_policy.DEFAULT_APPROVAL_DIR", tmp_path)

    def test_empty_code_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rex.cli import cmd_wc

        rc = cmd_wc(self._make_args(code="   "))
        assert rc == 1
        assert "non-empty" in capsys.readouterr().out.lower()

    def test_non_positive_amount_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rex.cli import cmd_wc

        rc = cmd_wc(self._make_args(amount="-5"))
        assert rc == 1
        out = capsys.readouterr().out
        assert "amount" in out.lower()

    def test_zero_amount_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rex.cli import cmd_wc

        rc = cmd_wc(self._make_args(amount="0"))
        assert rc == 1

    def test_invalid_type_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rex.cli import cmd_wc

        # Build args manually to bypass argparse choices validation
        args = self._make_args()
        args.type = "invalid_type"
        rc = cmd_wc(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "--type" in out

    def test_bad_expires_format_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rex.cli import cmd_wc

        rc = cmd_wc(self._make_args(expires="not-a-date"))
        assert rc == 1
        out = capsys.readouterr().out
        assert "YYYY-MM-DD" in out

    def test_no_approval_creates_pending(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        from rex.cli import cmd_wc

        self._patch_approval_dir(monkeypatch, tmp_path)

        with patch("requests.post") as mock_post:
            rc = cmd_wc(self._make_args(yes=True))

        assert rc == 1
        mock_post.assert_not_called()
        assert len(list(tmp_path.glob("*.json"))) == 1
        out = capsys.readouterr().out
        assert "Approval required" in out

    def test_approved_with_yes_executes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        from rex.cli import cmd_wc

        _save_approved_wc_approval(
            WC_COUPON_CREATE_TOOL,
            "myshop",
            {"code": "SAVE10", "amount": "10", "discount_type": "percent"},
            tmp_path,
        )
        self._patch_approval_dir(monkeypatch, tmp_path)

        mock_service = MagicMock()
        mock_service.create_coupon.return_value = WriteResult(
            ok=True, data={"id": 10, "code": "SAVE10"}
        )

        with patch("rex.woocommerce.service.get_woocommerce_service", return_value=mock_service):
            rc = cmd_wc(self._make_args(yes=True))

        assert rc == 0
        mock_service.create_coupon.assert_called_once_with(
            "myshop",
            code="SAVE10",
            amount="10",
            discount_type="percent",
            date_expires=None,
            usage_limit=None,
        )
        out = capsys.readouterr().out
        assert "SAVE10" in out

    def test_idempotent_repeated_runs_same_approval(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from rex.cli import cmd_wc

        self._patch_approval_dir(monkeypatch, tmp_path)

        cmd_wc(self._make_args())
        cmd_wc(self._make_args())

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1


# ===========================================================================
# CLI integration tests: coupons disable
# ===========================================================================


class TestCmdWcCouponDisable:
    def _make_args(
        self,
        site: str = "myshop",
        coupon_id: int = 55,
        yes: bool = False,
        user: str | None = None,
    ) -> argparse.Namespace:
        args = argparse.Namespace()
        args.wc_command = "coupons"
        args.wc_coupons_command = "disable"
        args.site = site
        args.coupon_id = coupon_id
        args.yes = yes
        args.user = user
        return args

    def _patch_approval_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("rex.woocommerce.write_policy.DEFAULT_APPROVAL_DIR", tmp_path)

    def test_non_positive_coupon_id_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rex.cli import cmd_wc

        rc = cmd_wc(self._make_args(coupon_id=0))
        assert rc == 1
        assert "positive integer" in capsys.readouterr().out

    def test_no_approval_creates_pending(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        from rex.cli import cmd_wc

        self._patch_approval_dir(monkeypatch, tmp_path)

        with patch("requests.put") as mock_put:
            rc = cmd_wc(self._make_args(yes=True))

        assert rc == 1
        mock_put.assert_not_called()
        assert len(list(tmp_path.glob("*.json"))) == 1

    def test_approved_with_yes_executes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        from rex.cli import cmd_wc

        _save_approved_wc_approval(WC_COUPON_DISABLE_TOOL, "myshop", {"coupon_id": 55}, tmp_path)
        self._patch_approval_dir(monkeypatch, tmp_path)

        mock_service = MagicMock()
        mock_service.disable_coupon.return_value = WriteResult(
            ok=True, data={"id": 55, "status": "draft"}
        )

        with patch("rex.woocommerce.service.get_woocommerce_service", return_value=mock_service):
            rc = cmd_wc(self._make_args(yes=True))

        assert rc == 0
        mock_service.disable_coupon.assert_called_once_with("myshop", 55)
        out = capsys.readouterr().out
        assert "draft" in out

    def test_approved_without_yes_refuses(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        from rex.cli import cmd_wc

        _save_approved_wc_approval(WC_COUPON_DISABLE_TOOL, "myshop", {"coupon_id": 55}, tmp_path)
        self._patch_approval_dir(monkeypatch, tmp_path)

        with patch("requests.put") as mock_put:
            rc = cmd_wc(self._make_args(yes=False))

        assert rc == 1
        mock_put.assert_not_called()
        out = capsys.readouterr().out
        assert "--yes" in out


# ===========================================================================
# Security tests
# ===========================================================================


class TestWcWriteSecuritySsrf:
    """Confirm SSRF protection applies to write paths too."""

    def test_localhost_rejected_for_write_client(self):
        """Writing to a localhost base_url is rejected at client construction."""
        with pytest.raises(ValueError, match="localhost"):
            WooCommerceClient(
                "https://localhost",
                consumer_key="ck_testkey",
                consumer_secret="cs_testsecret",  # pragma: allowlist secret -- fixture
            )

    def test_embedded_credentials_rejected(self):
        with pytest.raises(ValueError, match="embedded credentials"):
            WooCommerceClient(
                "https://user:pass@example.com",  # pragma: allowlist secret -- invalid basic-auth fixture
                consumer_key="ck_testkey",
                consumer_secret="cs_testsecret",  # pragma: allowlist secret -- fixture
            )

    def test_timeout_error_sanitized_for_write(self):
        """Timeout on a write call does not leak credential material."""
        import requests

        client = WooCommerceClient(
            "https://example.com",
            consumer_key="ck_testkey",
            consumer_secret="cs_testsecret",  # pragma: allowlist secret -- fixture
        )
        with patch("requests.put", side_effect=requests.Timeout("secret-token")):
            result = client.set_order_status(101, status="completed")

        assert result.ok is False
        assert result.error == "Request timed out"

    def test_http_error_sanitized_for_write(self):
        """HTTP error for write call exposes only status code."""
        import requests

        client = WooCommerceClient(
            "https://example.com",
            consumer_key="ck_testkey",
            consumer_secret="cs_testsecret",  # pragma: allowlist secret -- fake credential fixture
        )
        response = requests.Response()
        response.status_code = 403
        err = requests.HTTPError(
            "https://key:secret@example.com",  # pragma: allowlist secret -- deliberately malformed credential URL
            response=response,
        )

        with patch("requests.put") as mock_put:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = err
            mock_put.return_value = mock_resp
            result = client.disable_coupon(55)

        assert result.ok is False
        assert result.error == "HTTP error from WooCommerce API (status=403)"
        assert "key" not in result.error
        assert "secret" not in result.error

    def test_approval_payload_has_no_credentials(self, tmp_path: Path) -> None:
        """Approval summary stored on disk must not contain consumer key/secret."""
        engine = _make_approval_engine()
        _, approval = check_wc_write_policy(
            action=WC_COUPON_DISABLE_TOOL,
            site_id="myshop",
            identifiers={"coupon_id": 55},
            params={"coupon_id": 55},
            step_description="Disable coupon #55",
            initiated_by="alice",
            policy_engine=engine,
            approval_dir=tmp_path,
        )

        assert approval is not None
        summary = approval.tool_call_summary or ""
        assert "ck_" not in summary
        assert "cs_" not in summary
        assert "consumer_key" not in summary.lower()
        assert "consumer_secret" not in summary.lower()
        assert "password" not in summary.lower()
