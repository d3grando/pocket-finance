"""Session cookie refresh/clear on the way out.

``resolve_principal`` cannot touch the response, so it leaves an instruction in the request
state and this middleware appends the Set-Cookie headers:

* ``state.pf_cookies = ("renew", session_token, csrf_token)`` — sliding 90-day renewal;
* ``state.pf_cookies = ("clear",)`` — a dead/expired session cookie was presented.

If the route already set ``pf_session`` itself (login/logout), the route wins.

``session_cookie_value`` is THE parser of the session cookie: authentication
(services/auth/principal.py) and the rate limiter (middleware/ratelimit.py) both use it, so the
limiter's key is always the credential that authentication actually checks. More than one
``pf_session`` in a request is ambiguous (a browser sends one) → 400 ``bad_request``.
"""

from __future__ import annotations

from collections.abc import Iterable
from http import cookies as http_cookies
from typing import Protocol

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import BadRequest
from app.core.security import SESSION_COOKIE
from app.core.security_cookies import clear_cookie_raw_headers, session_cookie_raw_headers

STATE_KEY = "pf_cookies"


class MultipleSessionCookies(BadRequest):
    message = "В запросе несколько cookie сессии — очистите cookie сайта и войдите снова"


class _HeaderList(Protocol):
    def getlist(self, key: str) -> list[str]: ...


def session_cookie_values(cookie_headers: Iterable[str]) -> list[str]:
    """Every ``pf_session`` value in the ``Cookie`` header(s), parsed exactly like Starlette's
    ``request.cookies`` (``;``-separated, stripped, unquoted) but without «last one wins»."""
    values: list[str] = []
    for header in cookie_headers:
        for chunk in header.split(";"):
            key, sep, val = chunk.partition("=")
            if not sep:
                continue  # Starlette maps a bare chunk to the empty name
            if key.strip() == SESSION_COOKIE:
                values.append(http_cookies._unquote(val.strip()))  # type: ignore[attr-defined]
    return values


def session_cookie_value(headers: _HeaderList) -> str | None:
    """The session cookie of a request (None/empty if absent). Raises
    ``MultipleSessionCookies`` (400) when the request carries more than one."""
    values = session_cookie_values(headers.getlist("cookie"))
    if len(values) > 1:
        raise MultipleSessionCookies()
    return values[0] if values else None


class SessionCookieMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                instr = state.get(STATE_KEY)
                if instr:
                    headers = MutableHeaders(scope=message)
                    marker = (SESSION_COOKIE + "=").encode()
                    already = any(
                        k == b"set-cookie" and v.startswith(marker) for k, v in headers.raw
                    )
                    if not already:
                        if instr[0] == "renew":
                            extra = session_cookie_raw_headers(instr[1], instr[2])
                        else:
                            extra = clear_cookie_raw_headers()
                        for k, v in extra:
                            headers.append(k.decode("latin-1"), v.decode("latin-1"))
            await send(message)

        await self.app(scope, receive, _send)
