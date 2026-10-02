"""Mobile access-token (JWT) issuing/validation and the request principal.

Access JWTs are short-lived (default 15 minutes) and carry ``iss``, ``aud``,
``sub``, ``sid``, ``jti``, ``iat``, ``nbf``, and ``exp``.  Every authenticated
request validates, in order: Bearer syntax, signature with the configured
algorithm only, issuer, audience, time claims, required claims, canonical
``sub`` (before any private lookup), session existence/ownership/status, and
user existence/active status.  Display claims are never used for
authorization — permissions are resolved live from the server-side store.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
from typing import TYPE_CHECKING, Any

import jwt

from rex.identity import get_user_profile, validate_user_id
from rex.mobile_api import errors as merr
from rex.mobile_api import users as musers
from rex.mobile_api.db import connect
from rex.mobile_api.errors import MobileApiError

if TYPE_CHECKING:
    from rex.mobile_api.services import MobileApiServices

logger = logging.getLogger(__name__)

JWT_ISSUER = "askrex-assistant"
JWT_AUDIENCE = "askrex-mobile"
JWT_ALGORITHM = "HS256"
MIN_JWT_SECRET_LENGTH = 32

_REQUIRED_CLAIMS = ["iss", "aud", "sub", "sid", "jti", "iat", "nbf", "exp"]


class MobileAuthConfigurationError(RuntimeError):
    """The auth service cannot start; the message never contains the secret."""


def load_jwt_secret() -> str:
    """Return the mobile JWT signing secret from the credential authority.

    Fails closed when the secret is missing or does not meet the documented
    minimum length of 32 characters (use at least 32 random bytes — e.g.
    ``python -c "import secrets; print(secrets.token_hex(32))"``).
    """
    from rex.credentials import get_persisted_credential

    secret = get_persisted_credential("REX_JWT_SECRET") or ""
    if not secret:
        raise MobileAuthConfigurationError(
            "REX_JWT_SECRET is not set in the credential vault. Generate a "
            'value with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    if len(secret) < MIN_JWT_SECRET_LENGTH:
        raise MobileAuthConfigurationError(
            f"REX_JWT_SECRET is too short (minimum {MIN_JWT_SECRET_LENGTH} "
            "characters). Generate a value with: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
    return secret


@dataclass(frozen=True)
class MobilePrincipal:
    """Immutable per-request identity resolved from validated credentials.

    ``role`` is a presentation projection; ``permissions`` is the live
    server-side permission set at validation time.
    """

    user_id: str
    session_id: str
    username: str
    display_name: str
    role: str
    permissions: frozenset[str]
    paired_device_id: str | None = None
    grant_id: str | None = None
    desktop_id: str | None = None
    grant_version: int | None = None
    scopes: frozenset[str] = frozenset()
    strong_auth_at: str | None = None

    @property
    def paired(self) -> bool:
        return self.paired_device_id is not None


def issue_access_token(
    *,
    secret: str,
    user_id: str,
    session_id: str,
    ttl_seconds: int,
    now: datetime,
    token_id: str,
) -> str:
    """Create a signed short-lived mobile access JWT."""
    payload = {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "sub": user_id,
        "sid": session_id,
        "jti": token_id,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return str(jwt.encode(payload, secret, algorithm=JWT_ALGORITHM))


def decode_access_token(token: str, secret: str) -> dict[str, Any]:
    """Decode and validate a mobile access JWT.

    Raises:
        MobileApiError: With ``AUTH_TOKEN_EXPIRED`` or ``AUTH_TOKEN_INVALID``.
    """
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError as exc:
        raise MobileApiError(merr.AUTH_TOKEN_EXPIRED, "Access token expired.", 401) from exc
    except jwt.InvalidTokenError as exc:
        # Covers bad signature, wrong algorithm, wrong issuer/audience,
        # future nbf, and missing required claims.  One non-enumerating error.
        raise MobileApiError(merr.AUTH_TOKEN_INVALID, "Invalid access token.", 401) from exc
    if not isinstance(payload, dict):
        raise MobileApiError(merr.AUTH_TOKEN_INVALID, "Invalid access token.", 401)
    return payload


def _bearer_token_from_request() -> str:
    from flask import request  # noqa: PLC0415

    header = request.headers.get("Authorization", "")
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise MobileApiError(merr.AUTH_TOKEN_INVALID, "Missing or invalid authorization.", 401)
    return parts[1].strip()


def _load_principal_for_session(
    services: MobileApiServices,
    *,
    user_id: str,
    session_id: str,
    allow_revoked_session: bool = False,
    touch: bool = True,
) -> MobilePrincipal:
    """Resolve identity and current grant state from server-side storage."""
    from rex.mobile_api.authorization import (  # noqa: PLC0415
        GrantAuthorizationError,
        resolve_session_grant,
    )

    store = services.session_store
    session = store.get_session(session_id)
    if session is None:
        raise MobileApiError(merr.AUTH_TOKEN_INVALID, "Invalid access token.", 401)
    if session["user_id"] != user_id:
        logger.warning("Mobile session/subject mismatch: session=%s", session_id)
        raise MobileApiError(merr.AUTH_TOKEN_INVALID, "Invalid access token.", 401)
    session_revoked = session["revoked_at"] is not None
    if session_revoked and not allow_revoked_session:
        raise MobileApiError(merr.AUTH_SESSION_REVOKED, "Session has been revoked.", 401)
    if not session_revoked and not store.session_is_active(session, store.now()):
        raise MobileApiError(merr.AUTH_SESSION_REVOKED, "Session has expired.", 401)

    conn = connect(services.db_path)
    grant = None
    grant_invalid = False
    try:
        user = musers.get_user(conn, user_id)
        if not session_revoked:
            try:
                grant = resolve_session_grant(conn, session, now=store.now())
            except GrantAuthorizationError:
                grant_invalid = True
    finally:
        conn.close()
    if grant_invalid:
        store.revoke_session(session_id, "device_grant_invalid")
        raise MobileApiError(merr.AUTH_SESSION_REVOKED, "Session is no longer valid.", 401)
    if user is None or not musers.is_user_active(user):
        raise MobileApiError(merr.AUTH_SESSION_REVOKED, "Session is no longer valid.", 401)

    permissions = frozenset(musers.get_user_permissions(services.db_path, user_id))
    username = str(user["username"])
    display_name = username
    profile = get_user_profile(user_id)
    if profile and isinstance(profile.get("name"), str) and profile["name"].strip():
        display_name = profile["name"].strip()

    if touch and not session_revoked:
        store.touch_session(session_id)
    scopes = frozenset(grant.scopes) if grant is not None else frozenset()
    return MobilePrincipal(
        user_id=user_id,
        session_id=session_id,
        username=username,
        display_name=display_name,
        role=musers.role_projection(permissions),
        permissions=permissions,
        paired_device_id=grant.device_id if grant is not None else None,
        grant_id=grant.grant_id if grant is not None else None,
        desktop_id=grant.desktop_id if grant is not None else None,
        grant_version=grant.version if grant is not None else None,
        scopes=scopes,
        strong_auth_at=str(session["strong_auth_at"]) if session["strong_auth_at"] else None,
    )


def _require_scope(principal: MobilePrincipal, required_scope: str | None) -> None:
    if required_scope is None:
        return
    from rex.mobile_api.authorization import (  # noqa: PLC0415
        GrantAuthorizationError,
        require_scope,
    )

    try:
        require_scope(
            principal.scopes,
            required_scope,
            permissions=principal.permissions,
        )
    except GrantAuthorizationError as exc:
        raise MobileApiError(
            merr.FORBIDDEN,
            "This user and paired device are not authorized for the requested capability.",
            403,
        ) from exc


def authenticate_request(
    services: MobileApiServices,
    *,
    allow_revoked_session: bool = False,
    required_scope: str | None = None,
) -> MobilePrincipal:
    """Authenticate the current Flask request and return its live principal."""
    token = _bearer_token_from_request()
    return authenticate_token(
        services,
        token,
        allow_revoked_session=allow_revoked_session,
        required_scope=required_scope,
    )


def authenticate_token(
    services: MobileApiServices,
    token: str,
    *,
    allow_revoked_session: bool = False,
    required_scope: str | None = None,
) -> MobilePrincipal:
    """Validate a JWT and resolve current user/session/device/grant state."""
    claims = decode_access_token(token, services.jwt_secret)
    try:
        user_id = validate_user_id(str(claims["sub"]))
    except ValueError as exc:
        raise MobileApiError(merr.AUTH_TOKEN_INVALID, "Invalid access token.", 401) from exc
    principal = _load_principal_for_session(
        services,
        user_id=user_id,
        session_id=str(claims["sid"]),
        allow_revoked_session=allow_revoked_session,
    )
    _require_scope(principal, required_scope)
    return principal


def revalidate_principal(
    services: MobileApiServices,
    principal: MobilePrincipal,
    *,
    required_scope: str | None = None,
    touch: bool = False,
) -> MobilePrincipal:
    """Revalidate a long-lived SSE/WebSocket principal against current state."""
    current = _load_principal_for_session(
        services,
        user_id=principal.user_id,
        session_id=principal.session_id,
        touch=touch,
    )
    original_binding = (
        principal.paired_device_id,
        principal.grant_id,
        principal.desktop_id,
        principal.grant_version,
    )
    current_binding = (
        current.paired_device_id,
        current.grant_id,
        current.desktop_id,
        current.grant_version,
    )
    if original_binding != current_binding:
        services.session_store.revoke_session(principal.session_id, "device_grant_changed")
        raise MobileApiError(merr.AUTH_SESSION_REVOKED, "Session is no longer valid.", 401)
    if current.permissions != principal.permissions:
        services.session_store.revoke_session(principal.session_id, "user_permissions_changed")
        raise MobileApiError(merr.AUTH_SESSION_REVOKED, "Session is no longer valid.", 401)
    _require_scope(current, required_scope)
    return current


def require_mobile_auth(
    view: Any = None,
    *,
    allow_revoked_session: bool = False,
    required_scope: str | None = None,
) -> Any:
    """Decorator: authenticate and attach ``g.mobile_principal``."""

    def decorate(fn: Any) -> Any:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from flask import current_app, g  # noqa: PLC0415

            services = current_app.extensions["mobile_api_services"]
            g.mobile_principal = authenticate_request(
                services,
                allow_revoked_session=allow_revoked_session,
                required_scope=required_scope,
            )
            return fn(*args, **kwargs)

        return wrapper

    if view is not None:
        return decorate(view)
    return decorate


__all__ = [
    "JWT_ALGORITHM",
    "JWT_AUDIENCE",
    "JWT_ISSUER",
    "MIN_JWT_SECRET_LENGTH",
    "MobileAuthConfigurationError",
    "MobilePrincipal",
    "authenticate_request",
    "authenticate_token",
    "decode_access_token",
    "issue_access_token",
    "load_jwt_secret",
    "revalidate_principal",
    "require_mobile_auth",
]
