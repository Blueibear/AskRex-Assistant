"""Offline tests for the rex.woocommerce package (Cycle 6.1).

All tests run without network access.  The HTTP layer is replaced by
unittest.mock.patch on ``requests.get``.

Coverage targets
----------------
- Config parsing and validation for ``woocommerce.sites[]``
- Service error handling: missing site, disabled site, missing credentials
- WooCommerceClient list_orders() builds correct URL, passes params, respects limit
- WooCommerceClient list_products() with and without --low-stock
- Low-stock client-side filter (_filter_low_stock) logic
- HTTP 401/403 responses produce a clear error (not a crash)
- Non-list body produces a clear error
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from rex.woocommerce.client import (
    MAX_LIMIT,
    OrdersResult,
    ProductsResult,
    WooCommerceClient,
    _filter_low_stock,
)
from rex.woocommerce.config import (
    WooCommerceConfig,
    WooCommerceSiteConfig,
    load_woocommerce_config,
)
from rex.woocommerce.service import (
    WooCommerceMissingCredentialError,
    WooCommerceService,
    WooCommerceSiteDisabledError,
    WooCommerceSiteNotFoundError,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _mock_public_addrinfo(*_args, **_kwargs):
    """Return a stable public address for SSRF-safe URL validation in tests."""
    return [(0, 0, 0, "", ("93.184.216.34", 0))]


@pytest.fixture(autouse=True)
def _patch_getaddrinfo():
    """Keep tests fully offline by mocking DNS used in SSRF checks."""
    with patch("socket.getaddrinfo", side_effect=_mock_public_addrinfo):
        yield


_SAMPLE_ORDERS = [
    {
        "id": 101,
        "status": "pending",
        "total": "49.99",
        "currency": "USD",
        "date_created": "2026-02-01T10:00:00",
        "billing": {"first_name": "Alice", "last_name": "Smith"},
    },
    {
        "id": 102,
        "status": "completed",
        "total": "99.00",
        "currency": "USD",
        "date_created": "2026-02-02T11:00:00",
        "billing": {"first_name": "Bob", "last_name": "Jones"},
    },
]

_SAMPLE_PRODUCTS = [
    {
        "id": 1,
        "name": "Widget A",
        "price": "9.99",
        "stock_status": "instock",
        "manage_stock": True,
        "stock_quantity": 50,
        "low_stock_amount": 5,
    },
    {
        "id": 2,
        "name": "Widget B",
        "price": "19.99",
        "stock_status": "outofstock",
        "manage_stock": True,
        "stock_quantity": 0,
        "low_stock_amount": 5,
    },
    {
        "id": 3,
        "name": "Widget C",
        "price": "4.99",
        "stock_status": "instock",
        "manage_stock": True,
        "stock_quantity": 3,
        "low_stock_amount": 5,
    },
    {
        "id": 4,
        "name": "Widget D",
        "price": "2.99",
        "stock_status": "instock",
        "manage_stock": False,
        "stock_quantity": None,
        "low_stock_amount": None,
    },
]


def _make_site_config(
    *,
    site_id: str = "myshop",
    base_url: str = "https://example.com",
    enabled: bool = True,
    consumer_key_ref: str = "wc:myshop:key",
    consumer_secret_ref: str = "wc:myshop:secret",
    timeout_seconds: int = 30,
) -> WooCommerceSiteConfig:
    """Build a :class:`WooCommerceSiteConfig` for testing."""
    return WooCommerceSiteConfig(
        id=site_id,
        base_url=base_url,
        enabled=enabled,
        consumer_key_ref=consumer_key_ref,
        consumer_secret_ref=consumer_secret_ref,
        timeout_seconds=timeout_seconds,
    )


def _make_service(
    sites: list[WooCommerceSiteConfig],
    *,
    key_value: str | None = "ck_testkey",
    secret_value: str | None = "cs_testsecret",
) -> WooCommerceService:
    """Build a :class:`WooCommerceService` backed by a mock CredentialManager."""
    creds = MagicMock()

    def _get_token(ref: str) -> str | None:
        if ref.endswith(":key"):
            return key_value
        if ref.endswith(":secret"):
            return secret_value
        return None

    creds.get_token.side_effect = _get_token
    config = WooCommerceConfig(sites=sites)
    return WooCommerceService(wc_config=config, credential_manager=creds)


def _mock_ok_response(data):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = data
    return resp


def _mock_http_error(status_code: int):
    import requests

    resp = MagicMock()
    resp.status_code = status_code
    err = requests.HTTPError(response=resp)
    resp.raise_for_status.side_effect = err
    return resp


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestWooCommerceConfig:
    """Tests for rex.woocommerce.config."""

    def test_basic_parse(self):
        """Parse a minimal site entry."""
        raw = {
            "woocommerce": {
                "sites": [
                    {
                        "id": "myshop",
                        "base_url": "https://example.com",
                        "consumer_key_ref": "wc:myshop:key",
                        "consumer_secret_ref": "wc:myshop:secret",  # pragma: allowlist secret
                    }
                ]
            }
        }
        cfg = load_woocommerce_config(raw)
        assert len(cfg.sites) == 1
        site = cfg.sites[0]
        assert site.id == "myshop"
        assert site.base_url == "https://example.com"
        assert site.enabled is True
        assert site.consumer_key_ref == "wc:myshop:key"
        assert site.consumer_secret_ref == "wc:myshop:secret"  # pragma: allowlist secret -- fixture

    def test_trailing_slash_stripped(self):
        """base_url trailing slash is stripped."""
        site = WooCommerceSiteConfig(
            id="x",
            base_url="https://example.com/",
            consumer_key_ref="k",
            consumer_secret_ref="s",
        )
        assert site.base_url == "https://example.com"

    def test_invalid_scheme_rejected(self):
        """Non-http(s) schemes raise ValidationError."""
        with pytest.raises(ValidationError):
            WooCommerceSiteConfig(
                id="x",
                base_url="ftp://example.com",
                consumer_key_ref="k",
                consumer_secret_ref="s",
            )

    def test_missing_woocommerce_section(self):
        """Missing woocommerce key returns empty config."""
        cfg = load_woocommerce_config({})
        assert cfg.sites == []

    def test_missing_sites_key(self):
        """Missing sites key returns empty config."""
        cfg = load_woocommerce_config({"woocommerce": {}})
        assert cfg.sites == []

    def test_disabled_site_excluded_from_list_enabled(self):
        """Disabled site is excluded from list_enabled."""
        raw = {
            "woocommerce": {
                "sites": [
                    {
                        "id": "a",
                        "base_url": "https://a.example.com",
                        "consumer_key_ref": "k",
                        "consumer_secret_ref": "s",
                        "enabled": True,
                    },
                    {
                        "id": "b",
                        "base_url": "https://b.example.com",
                        "consumer_key_ref": "k",
                        "consumer_secret_ref": "s",
                        "enabled": False,
                    },
                ]
            }
        }
        cfg = load_woocommerce_config(raw)
        enabled = cfg.list_enabled()
        assert len(enabled) == 1
        assert enabled[0].id == "a"

    def test_get_site_found(self):
        """get_site returns the matching entry."""
        raw = {
            "woocommerce": {
                "sites": [
                    {
                        "id": "myshop",
                        "base_url": "https://example.com",
                        "consumer_key_ref": "k",
                        "consumer_secret_ref": "s",
                    }
                ]
            }
        }
        cfg = load_woocommerce_config(raw)
        site = cfg.get_site("myshop")
        assert site is not None
        assert site.id == "myshop"

    def test_get_site_not_found(self):
        """get_site returns None for unknown IDs."""
        cfg = load_woocommerce_config({})
        assert cfg.get_site("nonexistent") is None


# ---------------------------------------------------------------------------
# Service error-handling tests
# ---------------------------------------------------------------------------


class TestWooCommerceServiceErrors:
    """Tests for service-level error cases."""

    def test_site_not_found_orders(self):
        """Unknown site ID raises WooCommerceSiteNotFoundError for orders."""
        service = _make_service([])
        with pytest.raises(WooCommerceSiteNotFoundError, match="myshop"):
            service.list_orders("myshop")

    def test_site_not_found_products(self):
        """Unknown site ID raises WooCommerceSiteNotFoundError for products."""
        service = _make_service([])
        with pytest.raises(WooCommerceSiteNotFoundError, match="myshop"):
            service.list_products("myshop")

    def test_site_disabled_orders(self):
        """Disabled site raises WooCommerceSiteDisabledError for orders."""
        site = _make_site_config(enabled=False)
        service = _make_service([site])
        with pytest.raises(WooCommerceSiteDisabledError, match="myshop"):
            service.list_orders("myshop")

    def test_site_disabled_products(self):
        """Disabled site raises WooCommerceSiteDisabledError for products."""
        site = _make_site_config(enabled=False)
        service = _make_service([site])
        with pytest.raises(WooCommerceSiteDisabledError, match="myshop"):
            service.list_products("myshop")

    def test_missing_consumer_key(self):
        """Missing consumer key raises WooCommerceMissingCredentialError."""
        site = _make_site_config()
        service = _make_service([site], key_value=None)
        with pytest.raises(WooCommerceMissingCredentialError, match="(?i)consumer key"):
            service.list_orders("myshop")

    def test_missing_consumer_secret(self):
        """Missing consumer secret raises WooCommerceMissingCredentialError."""
        site = _make_site_config()
        service = _make_service([site], secret_value=None)
        with pytest.raises(WooCommerceMissingCredentialError, match="(?i)consumer secret"):
            service.list_orders("myshop")


# ---------------------------------------------------------------------------
# WooCommerceClient list_orders tests
# ---------------------------------------------------------------------------


class TestWooCommerceClientOrders:
    """Tests for WooCommerceClient.list_orders() using mocked requests.get."""

    def _client(self) -> WooCommerceClient:
        return WooCommerceClient(
            "https://example.com",
            consumer_key="ck_testkey",
            consumer_secret="cs_testsecret",  # pragma: allowlist secret
            site_id="myshop",
        )

    def test_orders_calls_correct_url(self):
        """list_orders() calls GET /wp-json/wc/v3/orders."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok_response(_SAMPLE_ORDERS)
            client.list_orders()

        called_url = mock_get.call_args[0][0]
        assert called_url == "https://example.com/wp-json/wc/v3/orders"

    def test_orders_passes_auth_as_basic(self):
        """Consumer key and secret are passed as HTTP Basic Auth."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok_response(_SAMPLE_ORDERS)
            client.list_orders()

        kwargs = mock_get.call_args[1]
        assert kwargs.get("auth") == ("ck_testkey", "cs_testsecret")

    def test_orders_passes_per_page(self):
        """per_page query param matches requested limit."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok_response(_SAMPLE_ORDERS)
            client.list_orders(limit=5)

        kwargs = mock_get.call_args[1]
        assert kwargs.get("params", {}).get("per_page") == 5

    def test_orders_passes_status_filter(self):
        """status filter is forwarded as a query param."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok_response([_SAMPLE_ORDERS[0]])
            client.list_orders(status="pending")

        kwargs = mock_get.call_args[1]
        assert kwargs.get("params", {}).get("status") == "pending"

    def test_orders_no_status_filter_when_none(self):
        """No status param when status=None."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok_response(_SAMPLE_ORDERS)
            client.list_orders(status=None)

        kwargs = mock_get.call_args[1]
        assert "status" not in kwargs.get("params", {})

    def test_orders_limit_capped_at_max(self):
        """Limit > MAX_LIMIT is capped at MAX_LIMIT."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok_response(_SAMPLE_ORDERS)
            client.list_orders(limit=9999)

        kwargs = mock_get.call_args[1]
        assert kwargs.get("params", {}).get("per_page") == MAX_LIMIT

    def test_orders_returns_list(self):
        """list_orders() returns ok=True with the order list."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok_response(_SAMPLE_ORDERS)
            result = client.list_orders()

        assert result.ok is True
        assert len(result.orders) == 2
        assert result.orders[0]["id"] == 101

    def test_orders_http_401(self):
        """HTTP 401 produces ok=False with an error message."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_http_error(401)
            result = client.list_orders()

        assert result.ok is False
        assert result.error is not None

    def test_orders_connection_error(self):
        """Connection error produces ok=False with an error message."""
        import requests as _requests

        client = self._client()
        with patch(
            "requests.get",
            side_effect=_requests.ConnectionError("Connection refused"),
        ):
            result = client.list_orders()

        assert result.ok is False
        assert result.error is not None

    def test_orders_non_list_response(self):
        """Non-list JSON response produces ok=False."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok_response({"error": "not a list"})
            result = client.list_orders()

        assert result.ok is False
        assert result.error is not None


# ---------------------------------------------------------------------------
# WooCommerceClient list_products tests
# ---------------------------------------------------------------------------


class TestWooCommerceClientProducts:
    """Tests for WooCommerceClient.list_products() using mocked requests.get."""

    def _client(self) -> WooCommerceClient:
        return WooCommerceClient(
            "https://example.com",
            consumer_key="ck_testkey",
            consumer_secret="cs_testsecret",  # pragma: allowlist secret -- fixture
            site_id="myshop",
        )

    def test_products_calls_correct_url(self):
        """list_products() calls GET /wp-json/wc/v3/products."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok_response(_SAMPLE_PRODUCTS)
            client.list_products()

        called_url = mock_get.call_args[0][0]
        assert called_url == "https://example.com/wp-json/wc/v3/products"

    def test_products_returns_list(self):
        """list_products() returns ok=True with the product list."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok_response(_SAMPLE_PRODUCTS)
            result = client.list_products()

        assert result.ok is True
        assert len(result.products) == 4

    def test_products_low_stock_filter(self):
        """low_stock=True filters to only low-stock / out-of-stock products."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok_response(_SAMPLE_PRODUCTS)
            result = client.list_products(low_stock=True, limit=10)

        assert result.ok is True
        # Widget B (outofstock) and Widget C (qty=3 <= low_amount=5) qualify.
        # Widget A (qty=50 > 5) and Widget D (manage_stock=False) do not.
        ids = [p["id"] for p in result.products]
        assert 2 in ids  # Widget B
        assert 3 in ids  # Widget C
        assert 1 not in ids  # Widget A — well-stocked
        assert 4 not in ids  # Widget D — stock not managed

    def test_products_limit_passed_as_per_page(self):
        """list_products() passes limit as per_page query parameter."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok_response(_SAMPLE_PRODUCTS)
            client.list_products(limit=2)

        kwargs = mock_get.call_args[1]
        assert kwargs.get("params", {}).get("per_page") == 2

    def test_products_http_403(self):
        """HTTP 403 produces ok=False with an error message."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_http_error(403)
            result = client.list_products()

        assert result.ok is False
        assert result.error is not None

    def test_products_non_list_response(self):
        """Non-list JSON response produces ok=False."""
        client = self._client()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok_response({"message": "Bad request"})
            result = client.list_products()

        assert result.ok is False
        assert result.error is not None


# ---------------------------------------------------------------------------
# _filter_low_stock unit tests
# ---------------------------------------------------------------------------


class TestFilterLowStock:
    """Unit tests for the _filter_low_stock helper."""

    def test_outofstock_included(self):
        """Products with stock_status=outofstock are always included."""
        products = [{"id": 1, "stock_status": "outofstock", "manage_stock": False}]
        result = _filter_low_stock(products)
        assert len(result) == 1

    def test_instock_with_quantity_above_threshold_excluded(self):
        """instock product with qty > low_stock_amount is excluded."""
        products = [
            {
                "id": 1,
                "stock_status": "instock",
                "manage_stock": True,
                "stock_quantity": 100,
                "low_stock_amount": 5,
            }
        ]
        result = _filter_low_stock(products)
        assert result == []

    def test_instock_with_quantity_at_threshold_included(self):
        """instock product with qty <= low_stock_amount is included."""
        products = [
            {
                "id": 1,
                "stock_status": "instock",
                "manage_stock": True,
                "stock_quantity": 5,
                "low_stock_amount": 5,
            }
        ]
        result = _filter_low_stock(products)
        assert len(result) == 1

    def test_zero_quantity_included(self):
        """Products with stock_quantity=0 are included regardless of low_stock_amount."""
        products = [
            {
                "id": 1,
                "stock_status": "instock",
                "manage_stock": True,
                "stock_quantity": 0,
                "low_stock_amount": None,
            }
        ]
        result = _filter_low_stock(products)
        assert len(result) == 1

    def test_manage_stock_false_excluded(self):
        """Products with manage_stock=False and non-outofstock status are excluded."""
        products = [
            {
                "id": 1,
                "stock_status": "instock",
                "manage_stock": False,
                "stock_quantity": None,
                "low_stock_amount": None,
            }
        ]
        result = _filter_low_stock(products)
        assert result == []

    def test_missing_stock_quantity_excluded(self):
        """Products with manage_stock=True but stock_quantity=None are excluded."""
        products = [
            {
                "id": 1,
                "stock_status": "instock",
                "manage_stock": True,
                "stock_quantity": None,
                "low_stock_amount": 5,
            }
        ]
        result = _filter_low_stock(products)
        assert result == []

    def test_non_dict_product_skipped(self):
        """Non-dict entries in the product list are skipped safely."""
        products = ["not a dict", None, 42]  # type: ignore[list-item]
        result = _filter_low_stock(products)
        assert result == []

    def test_empty_list(self):
        """Empty product list returns empty result."""
        assert _filter_low_stock([]) == []


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestCmdWcOrders:
    """Tests for cmd_wc with wc_command='orders'."""

    def _make_orders_args(
        self,
        site_id: str = "myshop",
        status: str | None = None,
        limit: int = 10,
    ) -> argparse.Namespace:
        args = argparse.Namespace()
        args.wc_command = "orders"
        args.wc_orders_command = "list"
        args.site = site_id
        args.status = status
        args.limit = limit
        return args

    def test_orders_list_ok(self, capsys):
        """Successful orders list prints results and returns 0."""
        from rex.cli import cmd_wc

        mock_service = MagicMock()
        mock_service.list_orders.return_value = OrdersResult(ok=True, orders=_SAMPLE_ORDERS)

        with patch(
            "rex.woocommerce.service.get_woocommerce_service",
            return_value=mock_service,
        ):
            rc = cmd_wc(self._make_orders_args())

        assert rc == 0
        out = capsys.readouterr().out
        assert "myshop" in out
        assert "101" in out

    def test_orders_site_not_found(self, capsys):
        """Unknown site ID prints error and returns 1."""
        from rex.cli import cmd_wc

        mock_service = MagicMock()
        mock_service.list_orders.side_effect = WooCommerceSiteNotFoundError(
            "No WooCommerce site with id 'myshop' found in config."
        )

        with patch(
            "rex.woocommerce.service.get_woocommerce_service",
            return_value=mock_service,
        ):
            rc = cmd_wc(self._make_orders_args())

        assert rc == 1
        out = capsys.readouterr().out
        assert "Error" in out

    def test_orders_missing_credential(self, capsys):
        """Missing credential prints error and returns 1."""
        from rex.cli import cmd_wc

        mock_service = MagicMock()
        mock_service.list_orders.side_effect = WooCommerceMissingCredentialError(
            "Credential not configured."
        )

        with patch(
            "rex.woocommerce.service.get_woocommerce_service",
            return_value=mock_service,
        ):
            rc = cmd_wc(self._make_orders_args())

        assert rc == 1

    def test_orders_empty_list(self, capsys):
        """Empty orders list prints 'No orders found.' and returns 0."""
        from rex.cli import cmd_wc

        mock_service = MagicMock()
        mock_service.list_orders.return_value = OrdersResult(ok=True, orders=[])

        with patch(
            "rex.woocommerce.service.get_woocommerce_service",
            return_value=mock_service,
        ):
            rc = cmd_wc(self._make_orders_args())

        assert rc == 0
        out = capsys.readouterr().out
        assert "No orders found" in out

    def test_orders_status_filter_passed(self, capsys):
        """Status filter is forwarded to the service."""
        from rex.cli import cmd_wc

        mock_service = MagicMock()
        mock_service.list_orders.return_value = OrdersResult(ok=True, orders=[])

        with patch(
            "rex.woocommerce.service.get_woocommerce_service",
            return_value=mock_service,
        ):
            cmd_wc(self._make_orders_args(status="pending"))

        mock_service.list_orders.assert_called_once_with("myshop", status="pending", limit=10)


class TestCmdWcProducts:
    """Tests for cmd_wc with wc_command='products'."""

    def _make_products_args(
        self,
        site_id: str = "myshop",
        limit: int = 10,
        low_stock: bool = False,
    ) -> argparse.Namespace:
        args = argparse.Namespace()
        args.wc_command = "products"
        args.wc_products_command = "list"
        args.site = site_id
        args.limit = limit
        args.low_stock = low_stock
        return args

    def test_products_list_ok(self, capsys):
        """Successful products list prints results and returns 0."""
        from rex.cli import cmd_wc

        mock_service = MagicMock()
        mock_service.list_products.return_value = ProductsResult(ok=True, products=_SAMPLE_PRODUCTS)

        with patch(
            "rex.woocommerce.service.get_woocommerce_service",
            return_value=mock_service,
        ):
            rc = cmd_wc(self._make_products_args())

        assert rc == 0
        out = capsys.readouterr().out
        assert "myshop" in out
        assert "Widget" in out

    def test_products_low_stock_flag_forwarded(self, capsys):
        """--low-stock flag is forwarded to the service."""
        from rex.cli import cmd_wc

        mock_service = MagicMock()
        mock_service.list_products.return_value = ProductsResult(ok=True, products=[])

        with patch(
            "rex.woocommerce.service.get_woocommerce_service",
            return_value=mock_service,
        ):
            cmd_wc(self._make_products_args(low_stock=True))

        mock_service.list_products.assert_called_once_with("myshop", limit=10, low_stock=True)

    def test_products_empty_list_low_stock(self, capsys):
        """Empty low-stock list prints 'No low-stock products found.'."""
        from rex.cli import cmd_wc

        mock_service = MagicMock()
        mock_service.list_products.return_value = ProductsResult(ok=True, products=[])

        with patch(
            "rex.woocommerce.service.get_woocommerce_service",
            return_value=mock_service,
        ):
            rc = cmd_wc(self._make_products_args(low_stock=True))

        assert rc == 0
        out = capsys.readouterr().out
        assert "No low-stock" in out

    def test_products_site_disabled(self, capsys):
        """Disabled site prints error and returns 1."""
        from rex.cli import cmd_wc

        mock_service = MagicMock()
        mock_service.list_products.side_effect = WooCommerceSiteDisabledError(
            "WooCommerce site 'myshop' is disabled."
        )

        with patch(
            "rex.woocommerce.service.get_woocommerce_service",
            return_value=mock_service,
        ):
            rc = cmd_wc(self._make_products_args())

        assert rc == 1


class TestWooCommerceClientSecurity:
    """Security-focused tests for URL validation and error sanitization."""

    def test_rejects_localhost_base_url(self):
        """localhost is rejected to reduce SSRF risk."""
        with pytest.raises(ValueError, match="localhost"):
            WooCommerceClient(
                "https://localhost",
                consumer_key="ck_testkey",
                consumer_secret="cs_testsecret",
            )

    def test_rejects_embedded_credentials_in_base_url(self):
        """Embedded credentials in base_url are rejected."""
        with pytest.raises(ValueError, match="embedded credentials"):
            WooCommerceClient(
                "https://user:pass@example.com",  # pragma: allowlist secret
                consumer_key="ck_testkey",
                consumer_secret="cs_testsecret",
            )

    def test_timeout_error_message_sanitized(self):
        """Timeout errors are normalized without leaking request details."""
        import requests

        client = WooCommerceClient(
            "https://example.com",
            consumer_key="ck_testkey",
            consumer_secret="cs_testsecret",
        )

        with patch("requests.get", side_effect=requests.Timeout("secret-token")):
            result = client.list_orders()

        assert result.ok is False
        assert result.error == "Request timed out"

    def test_http_error_message_sanitized(self):
        """HTTP errors expose only status code in user-facing errors."""
        import requests

        client = WooCommerceClient(
            "https://example.com",
            consumer_key="ck_testkey",
            consumer_secret="cs_testsecret",
        )
        response = requests.Response()
        response.status_code = 403
        err = requests.HTTPError("https://key:secret@example.com", response=response)

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = err
            mock_get.return_value = mock_resp
            result = client.list_products()

        assert result.ok is False
        assert result.error == "HTTP error from WooCommerce API (status=403)"
