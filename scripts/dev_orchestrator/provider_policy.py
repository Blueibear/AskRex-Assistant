from __future__ import annotations

import os

PROVIDER_POLICY_ENV = "ASKREX_PROVIDER_POLICY"


class ProviderPolicyError(ValueError):
    pass


def subscription_only() -> bool:
    """Deployment policy overrides configuration without changing saved flags."""
    value = os.environ.get(PROVIDER_POLICY_ENV, "")
    if value == "":
        return False
    if value == "subscription_only":
        return True
    raise ProviderPolicyError(
        f"Invalid {PROVIDER_POLICY_ENV}: expected unset, empty, or subscription_only"
    )


def require_configured_provider(route: str) -> None:
    if subscription_only():
        raise ProviderPolicyError(f"{PROVIDER_POLICY_ENV}=subscription_only forbids {route}")


def require_model_allowed(model: str) -> None:
    restricted = subscription_only()
    if restricted and model.strip().casefold() == "fable":
        raise ProviderPolicyError(
            f"{PROVIDER_POLICY_ENV}=subscription_only forbids Fable invocation"
        )
