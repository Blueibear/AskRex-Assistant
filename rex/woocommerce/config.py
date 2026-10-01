"""Pydantic v2 config models for the ``woocommerce.sites[]`` section of rex_config.json.

Example config fragment::

    {
      "woocommerce": {
        "sites": [
          {
            "id": "myshop",
            "base_url": "https://example.com",
            "enabled": true,
            "consumer_key_ref": "wc:myshop:key",
            "consumer_secret_ref": "wc:myshop:secret",  # pragma: allowlist secret -- credential reference example
            "timeout_seconds": 30
          }
        ]
      }
    }

Security notes
--------------
- ``consumer_key_ref`` and ``consumer_secret_ref`` are CredentialManager
  lookup keys.  The actual secrets are **never** stored in config; they must
  live in ``.env`` or ``config/credentials.json``.
- WooCommerce REST API v3 uses HTTP Basic Auth: consumer key as username,
  consumer secret as password.
- ``base_url`` must be an http(s) URL.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

# Maximum per_page value accepted by WooCommerce REST API v3.
WC_MAX_PER_PAGE = 100


class WooCommerceSiteConfig(BaseModel):
    """Configuration for a single WooCommerce site."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Unique site identifier (e.g. 'myshop')")
    base_url: str = Field(
        ...,
        description=(
            "Base URL of the WordPress/WooCommerce site "
            "(e.g. 'https://example.com'). Must be http or https. "
            "Trailing slashes are stripped automatically."
        ),
    )
    enabled: bool = Field(
        default=True,
        description=(
            "Whether this site is active.  Disabled sites are hidden from "
            "normal list output and cannot be targeted by CLI commands."
        ),
    )
    consumer_key_ref: str = Field(
        ...,
        description=(
            "CredentialManager lookup key for the WooCommerce consumer key.  "
            "Store the value in .env or config/credentials.json — never here."
        ),
    )
    consumer_secret_ref: str = Field(
        ...,
        description=(
            "CredentialManager lookup key for the WooCommerce consumer secret.  "
            "Store the value in .env or config/credentials.json — never here."
        ),
    )
    timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=120,
        description="HTTP request timeout in seconds (1–120).",
    )

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"base_url must use http or https scheme, got: {parsed.scheme!r}")
        if not parsed.netloc:
            raise ValueError("base_url must include a host (netloc)")
        return v.rstrip("/")


class WooCommerceConfig(BaseModel):
    """Top-level WooCommerce configuration holding all site entries."""

    model_config = ConfigDict(extra="forbid")

    sites: list[WooCommerceSiteConfig] = Field(
        default_factory=list,
        description="List of configured WooCommerce sites.",
    )

    def get_site(self, site_id: str) -> WooCommerceSiteConfig | None:
        """Return the config for *site_id*, or ``None`` if not found."""
        for site in self.sites:
            if site.id == site_id:
                return site
        return None

    def list_enabled(self) -> list[WooCommerceSiteConfig]:
        """Return only enabled sites."""
        return [s for s in self.sites if s.enabled]

    def list_all(self) -> list[WooCommerceSiteConfig]:
        """Return all sites including disabled ones."""
        return list(self.sites)


def load_woocommerce_config(raw_config: dict[str, Any]) -> WooCommerceConfig:
    """Parse the ``woocommerce`` section from the merged runtime config.

    Args:
        raw_config: The full runtime config dict (e.g. from ``load_config()``).

    Returns:
        A :class:`WooCommerceConfig` model.  If the ``woocommerce`` key is
        absent or malformed, an empty config with no sites is returned.
    """
    section = raw_config.get("woocommerce")
    if not section or not isinstance(section, dict):
        return WooCommerceConfig()
    sites = section.get("sites")
    if not sites or not isinstance(sites, list):
        return WooCommerceConfig()
    return WooCommerceConfig.model_validate({"sites": sites})


__all__ = [
    "WC_MAX_PER_PAGE",
    "WooCommerceConfig",
    "WooCommerceSiteConfig",
    "load_woocommerce_config",
]
