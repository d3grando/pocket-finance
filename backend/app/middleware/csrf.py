"""CSRF protection for cookie sessions.

Signed double-submit: for POST/PUT/PATCH/DELETE with a cookie session the header
``X-CSRF-Token`` must equal the ``pf_csrf`` cookie AND its HMAC must equal
``sessions.csrf_hash`` (the token is derived from the session token, see core/security.py).
Additionally a present ``Origin`` header must be one of the web origins (PUBLIC_URL /
WEB_ORIGINS). Device (Bearer ``pfd_``) and bot principals are exempt — they never use cookies.
"""

from __future__ import annotations

from fastapi import Request

from app.core.errors import CsrfFailed
from app.core.principal import Principal
from app.core.security import CSRF_COOKIE, CSRF_HEADER, constant_time_equals, token_hash
from app.core.security_config import origin_of, web_origins

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

__all__ = ["CSRF_COOKIE", "CSRF_HEADER", "SAFE_METHODS", "enforce_csrf"]


def enforce_csrf(request: Request, principal: Principal) -> None:
    """Raise CsrfFailed for unsafe methods of a cookie-session principal without a valid token.

    Device/bot principals are exempt. The session's csrf hash is expected on
    ``request.state.csrf_hash`` (set by resolve_principal).
    """
    if principal.kind != "session" or request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is not None and origin_of(origin) not in web_origins():
        # refreshing does not help here: the site is opened at an address that is neither
        # PUBLIC_URL nor in WEB_ORIGINS (e.g. an SSH tunnel to 127.0.0.1:8080)
        raise CsrfFailed(
            "Сайт открыт не по адресу PUBLIC_URL: откройте его по этому адресу "
            "или добавьте этот адрес в WEB_ORIGINS",
            details={"reason": "origin"},
        )
    header = request.headers.get(CSRF_HEADER)
    cookie = request.cookies.get(CSRF_COOKIE)
    expected: bytes | None = getattr(request.state, "csrf_hash", None)
    if not header or not cookie or expected is None:
        raise CsrfFailed(details={"reason": "missing"})
    if not constant_time_equals(header, cookie):
        raise CsrfFailed(details={"reason": "mismatch"})
    if not constant_time_equals(token_hash(header), expected):
        raise CsrfFailed(details={"reason": "invalid"})
