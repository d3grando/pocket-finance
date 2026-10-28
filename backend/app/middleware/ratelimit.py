"""In-memory rate limiting. Limits: docs/ARCH.md §6 (one API process).

* Password endpoints (``/auth/login``, ``/auth/device-login``, ``/users/me/password``,
  ``/users/me/wipe``): sliding window 5/min per client IP, ≤ 20 failed attempts per hour for all
  IPs together, and a temporary ban of an IP after 10 failures in 15 min.
  The global cap must not let anyone lock the single owner out (20 wrong passwords an hour from
  anywhere would otherwise block every password login): it applies only to *unknown* clients.
  A browser that has logged in successfully gets a signed ``pf_known`` cookie (OWASP «device
  cookie», no DB); its failures are counted per cookie (10/h) instead, and only after that does
  it fall back to the global cap. ``/users/me/password``, ``/users/me/wipe`` and
  ``/passkeys/register/options`` (password re-check) need a live session/device, so their
  failures are counted per credential and per user (20/h each; the per-user window is checked
  by ``check_user_password`` in a route dependency, because only the route knows the user),
  not globally. Passkey registration uses the 20/min passkey window, not the 5/min one.
* Passkey login (``/passkeys/login/*``): 20/min per IP, failures count towards the IP ban.
* Every other ``/api`` call: token bucket 300/min per principal, plus a flood cap of 1200/min
  per IP. ``POST /assistant/messages`` additionally 60/min per principal and 60/min per user
  (``take_user_ai``, route dependency): one user cannot multiply the AI budget by switching
  between the cookie session, device tokens and the bot.
* The principal key is built from the credential *as authentication reads it* (``parse_bearer``,
  ``parse_telegram_id``, ``session_cookie_value``): spelling variants of one credential
  (``bearer x``, ``Bearer  x``, a quoted cookie) share one bucket; X-Telegram-User-Id counts
  only with the bot token (device tokens ignore it); a request with two ``pf_session`` cookies
  is answered 400 here and in authentication alike.
* 429 ``rate_limited`` + ``Retry-After`` (seconds). Limits are tunable via env
  (``app/core/security_config.py``); disabled by default under APP_ENV=test.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.core.errors import AppError, RateLimited, error_body
from app.core.security import (
    KNOWN_DEVICE_COOKIE,
    fingerprint,
    new_known_device_value,
    verify_known_device_value,
)
from app.core.security_config import get_security_settings
from app.core.security_cookies import known_device_cookie_raw_header
from app.middleware.session_cookies import MultipleSessionCookies, session_cookie_value
from app.services.auth.bot import (
    TELEGRAM_USER_HEADER,
    is_bot_token,
    parse_bearer,
    parse_telegram_id,
)

log = logging.getLogger("pocket.security")

API_PREFIX = "/api/v1"
#: Password logins of anonymous clients (global failure cap unless the browser is known).
LOGIN_PATHS = frozenset({f"{API_PREFIX}/auth/login", f"{API_PREFIX}/auth/device-login"})
#: Adding a passkey re-checks the password (unless the session is fresh): docs/API.md.
PASSKEY_REGISTER_PATHS = frozenset({f"{API_PREFIX}/passkeys/register/options"})
#: Password re-checks inside a live session/device: failures counted per credential and user.
AUTHED_PASSWORD_PATHS = (
    frozenset({f"{API_PREFIX}/users/me/password", f"{API_PREFIX}/users/me/wipe"})
    | PASSKEY_REGISTER_PATHS
)
PASSWORD_PATHS = LOGIN_PATHS | AUTHED_PASSWORD_PATHS
PASSKEY_LOGIN_PATHS = frozenset(
    {f"{API_PREFIX}/passkeys/login/options", f"{API_PREFIX}/passkeys/login/verify"}
)
#: A 2xx here marks the browser as known (``pf_known`` cookie). Not device-login: Electron is
#: Bearer-only and that response sets no cookies (it is still exempt if it sends one).
KNOWN_ISSUING_PATHS = frozenset(
    {f"{API_PREFIX}/auth/login", f"{API_PREFIX}/passkeys/login/verify"}
)
AI_PATHS = frozenset({f"{API_PREFIX}/assistant/messages"})
AI_TOO_MANY = "Слишком много сообщений подряд, подождите немного"
EXEMPT_PATHS = frozenset({f"{API_PREFIX}/health"})

Clock = Callable[[], float]


class TokenBucket:
    """Classic token bucket per key: ``burst`` capacity, refills ``rate_per_minute``."""

    def __init__(
        self,
        rate_per_minute: float,
        burst: int,
        *,
        clock: Clock = time.monotonic,
        max_keys: int = 50_000,
    ) -> None:
        self.rate_per_minute = rate_per_minute
        self.burst = burst
        self.clock = clock
        self.max_keys = max_keys
        self._state: dict[str, tuple[float, float]] = {}

    @property
    def _per_second(self) -> float:
        return max(self.rate_per_minute, 1e-9) / 60.0

    def take(self, key: str, cost: float = 1.0) -> float | None:
        """Consume a token for ``key``; return None if allowed, else seconds to wait."""
        now = self.clock()
        tokens, ts = self._state.get(key, (float(self.burst), now))
        tokens = min(float(self.burst), tokens + (now - ts) * self._per_second)
        if tokens >= cost:
            self._state[key] = (tokens - cost, now)
            if len(self._state) > self.max_keys:
                self._gc(now)
            return None
        self._state[key] = (tokens, now)
        return (cost - tokens) / self._per_second

    def _gc(self, now: float) -> None:
        full_after = self.burst / self._per_second
        for k in [k for k, (_, ts) in self._state.items() if now - ts >= full_after]:
            del self._state[k]

    def reset(self) -> None:
        self._state.clear()


class SlidingWindow:
    """At most ``limit`` events per ``window`` seconds per key (exact, deque of timestamps)."""

    def __init__(
        self, limit: int, window: float, *, clock: Clock = time.monotonic, max_keys: int = 50_000
    ) -> None:
        self.limit = limit
        self.window = window
        self.clock = clock
        self.max_keys = max_keys
        self._events: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._events.get(key)
        if q is None:
            q = deque()
            self._events[key] = q
            if len(self._events) > self.max_keys:
                self._gc(now)
        while q and q[0] <= now - self.window:
            q.popleft()
        return q

    def count(self, key: str) -> int:
        return len(self._prune(key, self.clock()))

    def wait_time(self, key: str) -> float | None:
        """Seconds until one more event is allowed (None = allowed now)."""
        now = self.clock()
        q = self._prune(key, now)
        if len(q) < self.limit:
            return None
        return max(q[0] + self.window - now, 0.001)

    def add(self, key: str) -> None:
        now = self.clock()
        self._prune(key, now).append(now)

    def take(self, key: str) -> float | None:
        wait = self.wait_time(key)
        if wait is None:
            self.add(key)
        return wait

    def clear(self, key: str) -> None:
        self._events.pop(key, None)

    def _gc(self, now: float) -> None:
        for k in [k for k, q in self._events.items() if not q or q[-1] <= now - self.window]:
            del self._events[k]

    def reset(self) -> None:
        self._events.clear()


class RateLimiter:
    """All limiter state of the process. Tests: ``limiter.configure(...)``, ``limiter.reset()``,
    ``limiter.clock = fake``."""

    def __init__(self) -> None:
        self.enabled_override: bool | None = None
        self._clock: Clock = time.monotonic
        self.configure()

    @property
    def enabled(self) -> bool:
        if self.enabled_override is not None:
            return self.enabled_override
        return get_security_settings().ratelimit_on(get_settings())

    @property
    def clock(self) -> Clock:
        return self._clock

    @clock.setter
    def clock(self, fn: Clock) -> None:
        self._clock = fn
        for lim in self._all():
            lim.clock = fn

    def configure(self, **overrides: Any) -> None:
        """(Re)build limiters from security settings; ``overrides`` use the same field names
        without the ``rate_limit_`` prefix (e.g. ``login_per_minute=3``)."""
        s = get_security_settings()

        def v(name: str) -> Any:
            return overrides.get(name, getattr(s, f"rate_limit_{name}"))

        c = self._clock
        self.login_ip = SlidingWindow(v("login_per_minute"), 60, clock=c)
        self.login_failures_global = SlidingWindow(v("login_failures_per_hour"), 3600, clock=c)
        self.login_failures_known = SlidingWindow(
            v("known_device_failures_per_hour"), 3600, clock=c
        )
        self.login_failures_principal = SlidingWindow(
            v("login_failures_per_hour"), 3600, clock=c
        )
        #: wrong passwords per user on AUTHED_PASSWORD_PATHS, whatever the credential
        self.password_failures_user = SlidingWindow(v("login_failures_per_hour"), 3600, clock=c)
        self.failures_ip = SlidingWindow(v("ban_threshold"), v("ban_window_seconds"), clock=c)
        self.ban_seconds = float(v("ban_seconds"))
        self.passkey_ip = SlidingWindow(v("passkey_per_minute"), 60, clock=c)
        self.api = TokenBucket(v("api_per_minute"), v("api_per_minute"), clock=c)
        self.ai = TokenBucket(v("ai_per_minute"), v("ai_per_minute"), clock=c)
        self.ai_user = TokenBucket(v("ai_per_minute"), v("ai_per_minute"), clock=c)
        self.ip_flood = TokenBucket(v("ip_per_minute"), v("ip_per_minute"), clock=c)
        self.bans: dict[str, float] = {}

    def _all(self) -> list[TokenBucket | SlidingWindow]:
        return [
            self.login_ip,
            self.login_failures_global,
            self.login_failures_known,
            self.login_failures_principal,
            self.password_failures_user,
            self.failures_ip,
            self.passkey_ip,
            self.api,
            self.ai,
            self.ai_user,
            self.ip_flood,
        ]

    def reset(self) -> None:
        for lim in self._all():
            lim.reset()
        self.bans.clear()

    # ---- login helpers
    def ban_remaining(self, ip: str) -> float | None:
        until = self.bans.get(ip)
        if until is None:
            return None
        left = until - self._clock()
        if left <= 0:
            self.bans.pop(ip, None)
            return None
        return left

    def failure_bucket(
        self, path: str, principal: str, known: str | None
    ) -> tuple[SlidingWindow, str]:
        """Where failed password attempts of this request are counted (and checked);
        ``principal`` is the request's ``principal_key``."""
        if path in AUTHED_PASSWORD_PATHS:
            return self.login_failures_principal, principal
        if known is not None and self.login_failures_known.wait_time(known) is None:
            return self.login_failures_known, known
        return self.login_failures_global, "global"

    def record_failure(
        self,
        ip: str,
        *,
        password: bool,
        bucket: tuple[SlidingWindow, str] | None = None,
    ) -> None:
        if password:
            window, key = bucket or (self.login_failures_global, "global")
            window.add(key)
        self.failures_ip.add(ip)
        if self.failures_ip.count(ip) >= self.failures_ip.limit:
            self.bans[ip] = self._clock() + self.ban_seconds
            self.failures_ip.clear(ip)
            log.warning("login ban: ip=%s for %ss", ip, int(self.ban_seconds))

    def record_success(self, ip: str, bucket: tuple[SlidingWindow, str] | None = None) -> None:
        self.failures_ip.clear(ip)
        if bucket is not None and bucket[0] is not self.login_failures_global:
            bucket[0].clear(bucket[1])  # known browser / own credential: start afresh

    # ---- per-user limits (route dependencies: only the route knows the user)
    def check_user_password(self, user_id: int) -> None:
        """429 if this user had too many wrong passwords in the last hour (any credential)."""
        if not self.enabled:
            return
        wait = self.password_failures_user.wait_time(user_key(user_id))
        if wait is not None:
            raise _rate_limited(wait, _login_message(wait))

    def take_user_ai(self, user_id: int) -> None:
        """One AI message of this user (60/min over all their credentials) or 429."""
        if not self.enabled:
            return
        wait = self.ai_user.take(user_key(user_id))
        if wait is not None:
            raise _rate_limited(wait, AI_TOO_MANY)


limiter = RateLimiter()


def _retry_after(seconds: float) -> int:
    return max(1, math.ceil(seconds))


def _rate_limited(seconds: float, message: str) -> RateLimited:
    """The same 429 as ``_too_many``, raised from a route dependency."""
    secs = _retry_after(seconds)
    return RateLimited(message, details={"retry_after": secs}, headers={"Retry-After": str(secs)})


def _too_many(scope: Scope, seconds: float, message: str) -> JSONResponse:
    secs = _retry_after(seconds)
    rid = scope.get("state", {}).get("request_id")
    return JSONResponse(
        error_body("rate_limited", message, {"retry_after": secs}, rid),
        status_code=429,
        headers={"Retry-After": str(secs)},
    )


def _login_message(seconds: float) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    return f"Слишком много попыток. Подождите {minutes} мин"


def user_key(user_id: int) -> str:
    return f"u:{user_id}"


def principal_key(headers: Headers, ip: str) -> str:
    """Rate-limit key of the caller without touching the DB: the credential exactly as
    ``services/auth/principal.resolve_principal`` will read it (Authorization wins over cookies;
    X-Telegram-User-Id only with the bot token). A credential authentication would reject
    outright (malformed header, bad Telegram id) is keyed by IP. Raises
    ``MultipleSessionCookies`` (→ 400) like authentication does."""
    auth = headers.get("authorization")
    if auth is not None:
        token = parse_bearer(auth)
        if token is None:
            return "ip:" + ip
        if is_bot_token(token):
            try:
                tg = parse_telegram_id(headers.get(TELEGRAM_USER_HEADER))
            except AppError:
                return "ip:" + ip
            return "a:" + fingerprint(f"{token}|{tg if tg is not None else ''}")
        return "a:" + fingerprint(token)
    value = session_cookie_value(headers)
    if value:
        return "s:" + fingerprint(value)
    return "ip:" + ip


def known_device_nonce(headers: Headers) -> str | None:
    """Nonce of a genuine ``pf_known`` cookie in the request (None if absent/forged)."""
    cookie = headers.get("cookie") or ""
    if KNOWN_DEVICE_COOKIE + "=" not in cookie:
        return None
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == KNOWN_DEVICE_COOKIE:
            nonce = verify_known_device_value(value.strip().strip('"'))
            if nonce is not None:
                return nonce
    return None


async def _principal_key_or_400(scope: Scope, receive: Receive, send: Send, ip: str) -> str | None:
    """``principal_key`` of the request, or None after answering 400 (two session cookies)."""
    try:
        return principal_key(Headers(scope=scope), ip)
    except MultipleSessionCookies as e:
        rid = scope.get("state", {}).get("request_id")
        await JSONResponse(error_body(e.code, e.message, None, rid), status_code=e.status)(
            scope, receive, send
        )
        return None


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not limiter.enabled:
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "").rstrip("/") or "/"
        method: str = scope.get("method", "GET")
        if method == "OPTIONS" or not path.startswith(API_PREFIX) or path in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        client = scope.get("client")
        ip: str = state.get("client_ip") or (client[0] if client else "unknown")

        is_password = method == "POST" and path in PASSWORD_PATHS
        is_passkey = method == "POST" and path in PASSKEY_LOGIN_PATHS
        if is_password or is_passkey:
            # Anonymous logins are keyed by IP; password re-checks by the credential.
            key: str | None = "ip:" + ip
            if path in AUTHED_PASSWORD_PATHS:
                key = await _principal_key_or_400(scope, receive, send, ip)
            if key is not None:
                await self._login_like(scope, receive, send, ip=ip, password=is_password, key=key)
            return

        wait = limiter.ip_flood.take(ip)
        if wait is None:
            key = await _principal_key_or_400(scope, receive, send, ip)
            if key is None:
                return
            if method == "POST" and path in AI_PATHS:
                wait = limiter.ai.take(key)
                if wait is not None:
                    await _too_many(scope, wait, AI_TOO_MANY)(scope, receive, send)
                    return
            wait = limiter.api.take(key)
        if wait is not None:
            await _too_many(scope, wait, "Слишком много запросов, подождите")(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _login_like(
        self, scope: Scope, receive: Receive, send: Send, *, ip: str, password: bool, key: str
    ) -> None:
        ban = limiter.ban_remaining(ip)
        if ban is not None:
            await _too_many(scope, ban, _login_message(ban))(scope, receive, send)
            return
        path: str = scope.get("path", "").rstrip("/")
        known = known_device_nonce(Headers(scope=scope))
        bucket = limiter.failure_bucket(path, key, known) if password else None
        passkey_window = not password or path in PASSKEY_REGISTER_PATHS
        window = limiter.passkey_ip if passkey_window else limiter.login_ip
        wait = window.wait_time(ip)
        if wait is None and bucket is not None:
            wait = bucket[0].wait_time(bucket[1])
        if wait is not None:
            await _too_many(scope, wait, _login_message(wait))(scope, receive, send)
            return
        window.add(ip)

        status: list[int] = []
        issue_known = path in KNOWN_ISSUING_PATHS

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status.append(int(message["status"]))
                if issue_known and 200 <= int(message["status"]) < 300:
                    cookie = known_device_cookie_raw_header(new_known_device_value(known))
                    message = {**message, "headers": [*message.get("headers", []), cookie]}
            await send(message)

        await self.app(scope, receive, _send)
        code = status[0] if status else 500
        if code == 401:
            limiter.record_failure(ip, password=password, bucket=bucket)
            # A 401 after authentication succeeded (get_principal stored the principal) is a
            # wrong password: count it for the user too, whatever credential was used.
            principal = scope.get("state", {}).get("principal")
            if path in AUTHED_PASSWORD_PATHS and principal is not None:
                limiter.password_failures_user.add(user_key(principal.user_id))
            log.warning(
                "auth failure: path=%s ip=%s known=%s", scope.get("path"), ip, known is not None
            )
        elif 200 <= code < 300:
            limiter.record_success(ip, bucket)
