"""assistant fixtures: fake OpenAI-compatible server, seeded accounts/categories, fake services,
deterministic parser stub. No real LLM calls in tests."""

from __future__ import annotations

import importlib
import os
from collections.abc import AsyncIterator, Callable

import pytest
from sqlalchemy import select

from app.models import Account, AccountAsset, Category, Merchant, MyAccount
from app.schemas.intents import ParseResult
from app.services.ai import client as ai_client
from app.services.categories.seed import DEFAULT_CATEGORIES
from tests.ai.fake_llm import FakeLLM
from tests.ai.fakes import PATCHES

API_KEY = "sk-test-SECRET-should-never-be-logged"

ACCOUNTS = [
    # name, kind, currency, multi, aliases, default_for, assets
    ("Карта", "bank", "VND", False, ["карта", "банк"], ["VND"], ["VND"]),
    ("Наличные", "cash", "VND", False, ["нал", "налом", "наличные", "наличка", "кэш", "cash"], [],
     ["VND"]),
    ("Кошелёк", "crypto_wallet", None, True, ["кошелёк", "кошелька", "крипто"], [],
     ["USDT", "TON"]),
    ("Копилка", "crypto_wallet", None, True, ["копилка"], [], []),
    ("Биржа", "exchange", None, True, ["биржа"], [], ["USDT"]),
    ("Сейф", "crypto_wallet", None, True, ["сейф"], [], []),
    ("Карта RUB", "card", "RUB", False, ["карта руб", "рублёвая карта"], ["RUB"],
     ["RUB"]),
]


@pytest.fixture(scope="session")
def fake_llm_server():
    srv = FakeLLM()
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
async def llm(fake_llm_server: FakeLLM) -> AsyncIterator[FakeLLM]:
    """Fake LLM wired into the process-wide client (timeout 3 s, 1 transport retry)."""
    fake_llm_server.reset()
    ai_client.LLMClient._mode_cache.clear()
    c = ai_client.LLMClient(base_url=fake_llm_server.url, api_key=API_KEY, model="fake-model",
                            timeout=3, retries=1, max_concurrency=4)
    ai_client.set_llm_client(c)
    yield fake_llm_server
    ai_client.set_llm_client(None)
    await c.aclose()
    fake_llm_server.reset()


@pytest.fixture
def fake_services(monkeypatch):
    """Replace ledger/analytics service functions with ORM-backed fakes.

    PF_AI_REAL_SERVICES=1 runs the same tests against the real ledger/analytics services
    (integration check)."""
    if os.environ.get("PF_AI_REAL_SERVICES") == "1":
        return
    # Import every target module (and the whole app) BEFORE patching: a module imported for the
    # first time while another one is already patched would bind the fake via
    # `from ... import name` and keep it forever, leaking into other test packages.
    import app.main  # noqa: F401

    for mod_name in PATCHES:
        importlib.import_module(mod_name)
    for mod_name, funcs in PATCHES.items():
        mod = importlib.import_module(mod_name)
        for name, fn in funcs.items():
            monkeypatch.setattr(mod, name, fn)


@pytest.fixture
def fake_debts(monkeypatch):
    """Debts service stand-in (assistant tests do not depend on the real debts service).
    PF_AI_REAL_DEBTS=1 runs the same scenarios against the real debts service."""
    if os.environ.get("PF_AI_REAL_DEBTS") == "1":
        return
    import app.main  # noqa: F401
    from tests.ai.fake_debts import PATCHES as DEBT_PATCHES

    for mod_name, funcs in DEBT_PATCHES.items():
        mod = importlib.import_module(mod_name)
        for name, fn in funcs.items():
            monkeypatch.setattr(mod, name, fn)


@pytest.fixture
def parser_stub(monkeypatch) -> Callable[[ParseResult | Callable], None]:
    """Deterministic parser. Default: always needs_llm (independent of parser progress)."""
    import app.services.parser as parser_pkg

    def _set(result):
        fn = result if callable(result) else (lambda text, ctx: result)
        monkeypatch.setattr(parser_pkg, "parse", fn)

    _set(ParseResult(needs_llm=True, confidence=0.0))
    return _set


@pytest.fixture
async def seeded(session, user):
    """Default accounts/categories (idempotent: skipped if ledger seeds already ran) +
    merchant rule vast.ai → Серверы/инфра and own handle @myshop → wallet."""
    uid = user.id
    have_acc = await session.scalar(select(Account.id).where(Account.user_id == uid).limit(1))
    if have_acc is None:
        for i, (name, kind, cur, multi, aliases, dflt, assets) in enumerate(ACCOUNTS):
            a = Account(user_id=uid, name=name, kind=kind, currency=cur, is_multi_asset=multi,
                        aliases=aliases, default_for_currencies=dflt, sort_order=i)
            session.add(a)
            await session.flush()
            for asset in assets:
                session.add(AccountAsset(user_id=uid, account_id=a.id, asset=asset))
    have_cat = await session.scalar(select(Category.id).where(Category.user_id == uid).limit(1))
    if have_cat is None:
        for i, (name, kind, biz, icon, aliases) in enumerate(DEFAULT_CATEGORIES):
            session.add(Category(user_id=uid, name=name, kind=kind, is_business=biz, icon=icon,
                                 aliases=aliases, is_system=True, sort_order=i))
    await session.flush()
    accs = {a.name: a for a in (await session.scalars(
        select(Account).where(Account.user_id == uid))).all()}
    cats = {(c.kind, c.name): c for c in (await session.scalars(
        select(Category).where(Category.user_id == uid))).all()}
    vast = Merchant(user_id=uid, name="vast.ai", aliases=["васт", "vast"],
                    category_id=cats[("expense", "Серверы/инфра")].id, hits=5)
    wallet = Account(user_id=uid, name="@myshop", kind="crypto_wallet", is_multi_asset=True,
                     aliases=["myshop"], sort_order=50)
    session.add_all([vast, wallet])
    await session.flush()
    session.add(MyAccount(user_id=uid, handle="myshop", label="второй акк",
                          account_id=wallet.id))
    await session.flush()
    return {"accounts": accs, "categories": cats, "vast": vast, "wallet": wallet, "user": user}
