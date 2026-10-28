"""Fixtures for analytics tests.

* ``user`` is overridden: an owner WITHOUT the default seeds (tests build their own known
  categories/accounts, independent of the default seeds).
* Network is blocked for every test (respx, all routes must be mocked) except ``@pytest.mark.live``.
* ``dataset`` — a known set of operations with hand-computed totals (see test_stats.py).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    AccountAsset,
    Category,
    CurrencyRate,
    Merchant,
    Posting,
    Transaction,
    User,
    UserSettings,
)

FIXTURES = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
OWNER_TG_ID = 123456789
# «now» for most tests: Tue 2026-10-27 12:00 local (05:00 UTC)
NOW = datetime(2026, 10, 27, 5, 0, tzinfo=UTC)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: talks to real rate providers (smoke)")


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> object:
    return json.loads((FIXTURES / name).read_bytes())


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest):
    """Every outgoing httpx request must be mocked (respx) — except live smoke tests."""
    from app.services.rates import providers
    from app.services.rates import service as rates

    rates.reset_fetch_cache()
    providers._ID_CACHE.clear()
    if request.node.get_closest_marker("live"):
        # opt-in: a plain `pytest` (CI, a fresh clone) never goes to the network
        if os.environ.get("PF_LIVE", "0") != "1":
            pytest.skip("live tests are off (set PF_LIVE=1 to run them)")
        yield None
        return
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as mock:
        yield mock


@pytest.fixture
def rx(_no_network):  # type: ignore[no-untyped-def]
    """The active respx router (add routes: ``rx.get(url).respond(...)``)."""
    return _no_network


@pytest.fixture
async def user(session: AsyncSession) -> User:
    u = User(username="owner")
    session.add(u)
    await session.flush()
    session.add(UserSettings(user_id=u.id, telegram_user_id=OWNER_TG_ID))
    await session.flush()
    return u


def at(d: date, hh: int = 12, mm: int = 0) -> datetime:
    """Aware datetime of a local (Asia/Ho_Chi_Minh) wall-clock time."""
    return datetime.combine(d, time(hh, mm), tzinfo=TZ)


@dataclass
class Builder:
    session: AsyncSession
    user_id: int
    cats: dict[str, Category] = field(default_factory=dict)
    accs: dict[str, Account] = field(default_factory=dict)

    async def category(
        self, name: str, kind: str = "expense", is_business: bool = False, **kw: object
    ) -> Category:
        c = Category(
            user_id=self.user_id, name=name, kind=kind, is_business=is_business, version=1, **kw
        )
        self.session.add(c)
        await self.session.flush()
        self.cats[name] = c
        return c

    async def account(
        self,
        name: str,
        currency: str | None = "VND",
        kind: str = "bank",
        *,
        assets: dict[str, tuple[Decimal | None, datetime | None]] | None = None,
        **kw: object,
    ) -> Account:
        a = Account(
            user_id=self.user_id,
            name=name,
            kind=kind,
            currency=currency,
            is_multi_asset=currency is None,
            version=1,
            **kw,
        )
        self.session.add(a)
        await self.session.flush()
        for asset, (opening, opening_at) in (assets or {}).items():
            self.session.add(
                AccountAsset(
                    user_id=self.user_id,
                    account_id=a.id,
                    asset=asset,
                    opening_balance=opening,
                    opening_at=opening_at,
                )
            )
        await self.session.flush()
        await self.session.refresh(a, ["assets"])
        self.accs[name] = a
        return a

    async def tx(
        self,
        kind: str,
        amount: str,
        currency: str,
        on: date,
        *,
        category: str | None = None,
        account: str | None = None,
        usd: str | None = None,
        vnd: str | None = None,
        status: str = "posted",
        hh: int = 12,
        occurred_at: datetime | None = None,
        deleted: bool = False,
        merchant: Merchant | None = None,
        description: str | None = None,
        counterparty: str | None = None,
        postings: list[tuple[str, str, str]] | None = None,  # (account, asset, signed amount)
        **kw: object,
    ) -> Transaction:
        cat = self.cats[category] if category else None
        t = Transaction(
            user_id=self.user_id,
            kind=kind,
            status=status,
            occurred_at=occurred_at or at(on, hh),
            occurred_on=on,
            amount=Decimal(amount),
            currency=currency,
            account_id=self.accs[account].id if account else None,
            category_id=cat.id if cat else None,
            is_business=bool(cat.is_business) if cat else False,
            amount_usd=Decimal(usd) if usd is not None else None,
            amount_vnd=Decimal(vnd) if vnd is not None else None,
            merchant_id=merchant.id if merchant else None,
            description=description,
            counterparty=counterparty,
            source="web",
            deleted_at=at(on, 13) if deleted else None,
            **kw,
        )
        self.session.add(t)
        await self.session.flush()
        for acc, asset, amt in postings or []:
            self.session.add(
                Posting(
                    user_id=self.user_id,
                    transaction_id=t.id,
                    account_id=self.accs[acc].id,
                    asset=asset,
                    amount=Decimal(amt),
                    occurred_at=t.occurred_at,
                )
            )
        await self.session.flush()
        return t


@pytest.fixture
def build(session: AsyncSession, user: User) -> Builder:
    return Builder(session, user.id)


async def add_rate(
    session: AsyncSession,
    base: str,
    quote: str,
    rate: str,
    on: date,
    kind: str = "mid",
    source: str = "open_er_api",
) -> None:
    session.add(
        CurrencyRate(
            base=base, quote=quote, rate=Decimal(rate), rate_date=on, kind=kind, source=source
        )
    )
    await session.flush()


@dataclass
class Dataset:
    b: Builder
    tx: dict[str, Transaction]
    merchant: Merchant


@pytest.fixture
async def dataset(session: AsyncSession, build: Builder) -> Dataset:
    """Known data (USD/VND = 25 000, TON = $5). Expected totals are in test_stats.py."""
    b = build
    await b.category("Еда", icon="Utensils")
    await b.category("Кофе", icon="Coffee")
    await b.category("Серверы/инфра", is_business=True, icon="Server")
    await b.category("Клиенты", "income", is_business=True)
    await b.category("Зарплата", "income")
    old = await b.category("Старая еда")
    old.merged_into_id = b.cats["Еда"].id
    oct1 = datetime(2026, 10, 1, tzinfo=TZ)
    await b.account(
        "Карта", "VND", default_for_currencies=["VND"], assets={"VND": (Decimal(10_000_000), oct1)}
    )
    await b.account("Наличные", "VND", "cash", assets={"VND": (None, None)})
    await b.account(
        "Кошелёк", None, "crypto_wallet", assets={"TON": (Decimal(10), oct1), "USDT": (None, None)}
    )
    vast = Merchant(user_id=b.user_id, name="vast.ai", aliases=["васт", "vast"])
    b.session.add(vast)
    await b.session.flush()
    for base, quote, rate, kind, src in (
        ("USD", "VND", "25000", "sell", "vietcombank"),
        ("TON", "USD", "5", "mid", "coingecko"),
    ):
        await add_rate(b.session, base, quote, rate, date(2026, 10, 1), kind, src)

    d27, d26, d10 = date(2026, 10, 27), date(2026, 10, 26), date(2026, 10, 10)
    tx: dict[str, Transaction] = {}
    T = b.tx
    tx["t1"] = await T("expense", "150000", "VND", d27, category="Еда", account="Карта",
                       usd="6", vnd="150000", postings=[("Карта", "VND", "-150000")],
                       description="обед в Highlands")  # fmt: skip
    tx["t2"] = await T("expense", "50000", "VND", d27, category="Кофе", account="Наличные",
                       usd="2", vnd="50000", postings=[("Наличные", "VND", "-50000")])  # fmt: skip
    tx["t3"] = await T("expense", "20", "USD", d27, category="Серверы/инфра", usd="20",
                       vnd="500000", status="needs_account", merchant=vast)  # fmt: skip
    tx["t4"] = await T("expense", "250000", "VND", d26, category="Еда", account="Карта",
                       usd="10", vnd="250000", postings=[("Карта", "VND", "-250000")])  # fmt: skip
    tx["t5"] = await T("refund", "50000", "VND", d26, category="Еда", account="Карта",
                       usd="2", vnd="50000", postings=[("Карта", "VND", "50000")])  # fmt: skip
    tx["t6"] = await T("transfer", "1000000", "VND", d26, account="Карта", usd="40",
                       vnd="1000000", counter_account_id=b.accs["Наличные"].id,
                       counter_amount=Decimal(1000000), counter_asset="VND",
                       postings=[("Карта", "VND", "-1000000"),
                                 ("Наличные", "VND", "1000000")])  # fmt: skip
    tx["t7"] = await T("exchange", "100", "USDT", d26, account="Кошелёк", usd="100",
                       vnd="2500000", counter_amount=Decimal("2550000"),
                       counter_asset="VND")  # fmt: skip
    tx["t8"] = await T("adjustment", "120000", "VND", d26, account="Карта", usd="4.8",
                       vnd="120000", account_amount=Decimal(-120000),
                       postings=[("Карта", "VND", "-120000")])  # fmt: skip
    tx["t9"] = await T("income", "1000", "USD", d10, category="Клиенты", usd="1000",
                       vnd="25000000", counterparty="клиент")  # fmt: skip
    tx["t10"] = await T("income", "5000000", "VND", d10, category="Зарплата", account="Карта",
                        usd="200", vnd="5000000")  # fmt: skip
    tx["t11"] = await T("expense", "100000", "VND", d10, category="Старая еда", account="Карта",
                        usd="4", vnd="100000")  # fmt: skip
    tx["t12"] = await T("expense", "3", "TON", d10, category="Серверы/инфра", account="Кошелёк",
                        usd="15", vnd="375000", postings=[("Кошелёк", "TON", "-3")],
                        counterparty="@alice")  # fmt: skip
    tx["t13"] = await T("expense", "999999", "VND", d10, category="Еда", usd="40", vnd="999999",
                        deleted=True)  # fmt: skip
    tx["t14"] = await T("expense", "40", "RUB", d10, category="Кофе")
    tx["t15"] = await T("expense", "300000", "VND", date(2026, 9, 15), category="Еда",
                        account="Карта", usd="12", vnd="300000",
                        postings=[("Карта", "VND", "-300000")])  # fmt: skip
    tx["t16"] = await T("income", "5000000", "VND", date(2026, 9, 15), category="Зарплата",
                        usd="200", vnd="5000000")  # fmt: skip
    tx["t17"] = await T("expense", "30000", "VND", date(2026, 9, 5), category="Кофе",
                        usd="1.2", vnd="30000")  # fmt: skip
    return Dataset(b, tx, vast)


def sp(text: str) -> str:
    """NBSP (U+00A0, used by format_money) → plain space, for readable assertions."""
    return text.replace("\u00a0", " ")


def local_now(d: date, hh: int, mm: int = 0) -> datetime:
    return at(d, hh, mm).astimezone(UTC)


__all__ = ["NOW", "TZ", "Builder", "Dataset", "add_rate", "at", "fixture_bytes", "local_now", "sp"]
