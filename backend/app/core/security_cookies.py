"""Session cookie helpers.

* ``pf_session``: HttpOnly, Secure (COOKIE_SECURE), SameSite=Lax, Path=/,
  Max-Age = SESSION_TTL_DAYS.
* ``pf_csrf``: readable by JS (double-submit), Secure, SameSite=Strict, same Max-Age.
* ``pf_known``: «this browser logged in before» marker for the login rate limiter (HttpOnly,
  Secure, SameSite=Strict, Path=/api/v1/auth, 365 days; survives logout on purpose).
"""

from __future__ import annotations

from starlette.responses import Response

from app.config import get_settings
from app.core.security import CSRF_COOKIE, KNOWN_DEVICE_COOKIE, SESSION_COOKIE

KNOWN_DEVICE_COOKIE_PATH = "/api/v1/auth"
KNOWN_DEVICE_MAX_AGE = 365 * 86400


def cookie_max_age() -> int:
    return int(get_settings().session_ttl_days) * 86400


def set_session_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    s = get_settings()
    max_age = cookie_max_age()
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=max_age,
        path="/",
        secure=s.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    set_csrf_cookie(response, csrf_token)


def set_csrf_cookie(response: Response, csrf_token: str) -> None:
    s = get_settings()
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=cookie_max_age(),
        path="/",
        secure=s.cookie_secure,
        httponly=False,
        samesite="strict",
    )


def clear_session_cookies(response: Response) -> None:
    s = get_settings()
    for name, httponly, samesite in (
        (SESSION_COOKIE, True, "lax"),
        (CSRF_COOKIE, False, "strict"),
    ):
        response.delete_cookie(
            name,
            path="/",
            secure=s.cookie_secure,
            httponly=httponly,
            samesite=samesite,  # type: ignore[arg-type]
        )


def session_cookie_raw_headers(session_token: str, csrf_token: str) -> list[tuple[bytes, bytes]]:
    r = Response()
    set_session_cookies(r, session_token, csrf_token)
    return [(k, v) for k, v in r.raw_headers if k == b"set-cookie"]


def clear_cookie_raw_headers() -> list[tuple[bytes, bytes]]:
    r = Response()
    clear_session_cookies(r)
    return [(k, v) for k, v in r.raw_headers if k == b"set-cookie"]


def known_device_cookie_raw_header(value: str) -> tuple[bytes, bytes]:
    """``Set-Cookie: pf_known=…`` as a raw ASGI header (added by the rate-limit middleware)."""
    r = Response()
    r.set_cookie(
        KNOWN_DEVICE_COOKIE,
        value,
        max_age=KNOWN_DEVICE_MAX_AGE,
        path=KNOWN_DEVICE_COOKIE_PATH,
        secure=get_settings().cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return next((k, v) for k, v in r.raw_headers if k == b"set-cookie")
