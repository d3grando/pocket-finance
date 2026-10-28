"""auth fixtures. Base fixtures (engine/session/user/raw_client) — tests/conftest.py."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CSRF_COOKIE, hash_password
from app.middleware.ratelimit import limiter
from app.models import User

PASSWORD = "correct-horse-battery-1"
_HASH_CACHE: dict[str, str] = {}


def password_hash(pw: str = PASSWORD) -> str:
    if pw not in _HASH_CACHE:
        _HASH_CACHE[pw] = hash_password(pw)
    return _HASH_CACHE[pw]


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def rate_limits() -> Iterator[FakeClock]:
    """Rate limiting ON with default limits, fresh state and a controllable clock."""
    clock = FakeClock()
    prev_override, prev_clock = limiter.enabled_override, limiter.clock
    limiter.enabled_override = True
    limiter.clock = clock
    limiter.configure()
    limiter.reset()
    yield clock
    limiter.enabled_override = prev_override
    limiter.clock = prev_clock
    limiter.configure()
    limiter.reset()


@pytest.fixture
async def owner(session: AsyncSession, user: User) -> User:
    """The conftest owner with a known password."""
    user.password_hash = password_hash()
    await session.flush()
    return user


async def login(client: httpx.AsyncClient, password: str = PASSWORD, **kw: Any) -> httpx.Response:
    return await client.post("/auth/login", json={"password": password}, **kw)


def csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get(CSRF_COOKIE) or ""}


@pytest.fixture
async def web(raw_client: httpx.AsyncClient, owner: User) -> AsyncIterator[httpx.AsyncClient]:
    """raw_client logged in by password (cookie session)."""
    r = await login(raw_client)
    assert r.status_code == 200, r.text
    yield raw_client


@pytest.fixture
async def device_token(raw_client: httpx.AsyncClient, owner: User) -> str:
    r = await raw_client.post(
        "/auth/device-login",
        json={"password": PASSWORD, "device_name": "DESKTOP-TEST", "platform": "windows"},
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def set_cookie_headers(r: httpx.Response) -> list[str]:
    return r.headers.get_list("set-cookie")


def cookie_header(r: httpx.Response, name: str) -> str:
    for h in set_cookie_headers(r):
        if h.startswith(name + "="):
            return h
    raise AssertionError(f"no Set-Cookie for {name}: {set_cookie_headers(r)}")
