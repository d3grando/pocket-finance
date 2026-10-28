"""ledger test fixtures: deterministic rates, idempotency pass-through, committed ("live")
users for race tests (real parallel connections + commits)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.principal import Principal
from app.models import Account, Category, User, UserSettings
from app.services.rates import service as rates_service
from tests.conftest import seed_test_accounts

# 1 base = rate quote (direct pairs only; the ledger derives inverse / USD pivot itself)
RATES: dict[tuple[str, str], tuple[Decimal, str, str]] = {
    ("USD", "VND"): (Decimal("26140"), "vietcombank", "sell"),
    ("TON", "USD"): (Decimal("5.4"), "coingecko", "mid"),
    ("USDT", "USD"): (Decimal("1"), "coingecko", "mid"),
    ("USD", "RUB"): (Decimal("95"), "cbr", "mid"),
}


@pytest.fixture(autouse=True)
def fake_rates(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], Any]:
    """Replace analytics get_rate with a fixed table (identity for same currency)."""
    table = dict(RATES)

    async def get_rate(
        session: Any, base: str, quote: str, on: date, *, kind: str = "sell",
        fetch_missing: bool = True,
    ) -> rates_service.RateQuote | None:
        b, q = base.upper(), quote.upper()
        if b == q:
            return rates_service.RateQuote(b, q, Decimal(1), on, "identity", kind)
        hit = table.get((b, q))
        if hit is None:
            return None
        return rates_service.RateQuote(b, q, hit[0], on, hit[1], hit[2])

    monkeypatch.setattr(rates_service, "get_rate", get_rate)
    return table


class PassIdem:
    """Stand-in for auth's Idempotency handle: no storage, so these tests exercise the
    ledger-level idempotency (action_log key claim) on its own."""

    def __init__(self, key: str | None) -> None:
        self.key = key
        self.replay: JSONResponse | None = None

    async def save(self, status_code: int, body: Any) -> JSONResponse:
        if isinstance(body, BaseModel):
            body = body.model_dump(mode="json")
        return JSONResponse(body, status_code=status_code)

    async def release(self) -> None:
        return None


async def _pass_idem(request: Request) -> AsyncIterator[PassIdem]:
    yield PassIdem(request.headers.get("Idempotency-Key"))


def _override_idem(app: Any) -> None:
    from app.middleware.idempotency import idempotency_optional, idempotency_required

    app.dependency_overrides[idempotency_required] = _pass_idem
    app.dependency_overrides[idempotency_optional] = _pass_idem


@pytest.fixture
async def api(client: httpx.AsyncClient) -> httpx.AsyncClient:
    """Shared ``client`` (rolled-back session, auth bypassed) + idempotency pass-through."""
    from app.main import app

    _override_idem(app)
    return client


# ------------------------------------------------------------------------------- helpers


async def cat_id(session: AsyncSession, user_id: int, name: str, kind: str = "expense") -> int:
    cid = await session.scalar(
        select(Category.id).where(
            Category.user_id == user_id, Category.name == name, Category.kind == kind
        )
    )
    assert cid is not None, name
    return int(cid)


async def acc_id(session: AsyncSession, user_id: int, name: str) -> int:
    aid = await session.scalar(
        select(Account.id).where(Account.user_id == user_id, Account.name == name)
    )
    assert aid is not None, name
    return int(aid)


def idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


# ------------------------------------------------------------------------------- live DB


@pytest.fixture
async def live_user(engine: AsyncEngine) -> AsyncIterator[User]:
    """A committed user (unique name) with seeds; removed with all data afterwards."""
    from app.services.accounts.seed import seed_default_accounts
    from app.services.categories.seed import seed_defaults
    from app.services.ledger.wipe import delete_user

    async with AsyncSession(engine, expire_on_commit=False) as s:
        u = User(username=f"race-{uuid.uuid4().hex[:10]}")
        s.add(u)
        await s.flush()
        s.add(UserSettings(user_id=u.id))
        await seed_defaults(s, u.id)
        await seed_default_accounts(s, u.id)
        await seed_test_accounts(s, u.id)
        await s.commit()
    yield u
    async with AsyncSession(engine, expire_on_commit=False) as s:
        await delete_user(s, u.id)
        await s.commit()


@pytest.fixture
def live_principal(live_user: User) -> Principal:
    return Principal(user_id=live_user.id, kind="device", source="desktop")


@pytest.fixture
async def live_api(
    engine: AsyncEngine, live_principal: Principal
) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client on the real sessionmaker: every request = own connection + real commit."""
    from app.api.deps_base import get_principal
    from app.db import set_engine
    from app.main import app

    set_engine(engine)

    async def _principal() -> Principal:
        return live_principal

    app.dependency_overrides[get_principal] = _principal
    _override_idem(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def live_session(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    """Factory of independent sessions (each its own connection)."""

    def _make() -> AsyncSession:
        return AsyncSession(engine, expire_on_commit=False)

    return _make
