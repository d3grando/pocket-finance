"""Input limits of the public API:
bounded aliases / handles / colors / icons / texts, per-user object limits (422 limit_reached),
ids and offsets above BIGINT and dates outside 1970…2100 are a 422 — never a 500."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.core.errors import ValidationFailed, install_error_handlers
from app.models import Account
from tests.core.conftest import acc_id, cat_id, idem

BIG = 2**63  # one above PostgreSQL BIGINT


def _code(r: httpx.Response) -> str:
    return r.json()["error"]["code"]


# ------------------------------------------------------------------------------- aliases


async def test_alias_count_and_length_are_limited(api, session, user):
    many = [f"alias{i}" for i in range(51)]
    r = await api.post("/merchants", json={"name": "Shop A", "aliases": many})
    assert r.status_code == 422 and _code(r) == "validation_error"
    r = await api.post("/merchants", json={"name": "Shop A", "aliases": ["x" * 65]})
    assert r.status_code == 422
    r = await api.post("/merchants", json={"name": "Shop A", "aliases": many[:50]})
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert (await api.patch(f"/merchants/{mid}", json={"aliases": many})).status_code == 422

    r = await api.post("/categories", json={"name": "Хобби", "kind": "expense", "aliases": many})
    assert r.status_code == 422
    r = await api.post("/accounts", json={"name": "Wise", "kind": "bank", "currency": "USD",
                                          "aliases": many})
    assert r.status_code == 422
    cash = await acc_id(session, user.id, "Наличные")
    acc = (await api.get(f"/accounts/{cash}")).json()
    r = await api.patch(f"/accounts/{cash}", json={"version": acc["version"], "aliases": many})
    assert r.status_code == 422
    food = await cat_id(session, user.id, "Еда")
    cat = next(c for c in (await api.get("/categories")).json() if c["id"] == food)
    r = await api.patch(f"/categories/{food}", json={"version": cat["version"], "aliases": many})
    assert r.status_code == 422

    d = await api.post("/debts/operations", json={"op": "lend", "person_name": "Коля",
                                                  "amount": "1000", "currency": "VND"},
                       headers=idem())
    assert d.status_code == 201, d.text
    debt = d.json()["debt"]
    r = await api.patch(f"/debts/{debt['id']}", json={"version": debt["version"], "aliases": many})
    assert r.status_code == 422


async def test_merchant_handle_color_icon_and_texts_are_validated(api, session, user):
    # a handle goes into the LLM prompt — only a Telegram-like name
    for bad in ("<<<USER>>>", "shop name", "a\nb", "x" * 70):
        r = await api.post("/merchants", json={"name": f"M {len(bad)}", "handle": bad})
        assert r.status_code == 422, bad
    r = await api.post("/merchants", json={"name": "Good", "handle": "@good_shop1"})
    assert r.status_code == 201 and r.json()["handle"] == "good_shop1"
    # color #RRGGBB, icon = a lucide name
    for body in ({"color": "red"}, {"color": "#12345"}, {"color": "#1234567"},
                 {"icon": "x" * 65}, {"icon": "Bad Icon"}, {"icon": "<svg>"}):
        r = await api.post("/categories", json={"name": "Хобби", "kind": "expense", **body})
        assert r.status_code == 422, body
        r = await api.post("/accounts", json={"name": "Wise", "kind": "bank",
                                              "currency": "USD", **body})
        assert r.status_code == 422, body
    r = await api.post("/categories", json={"name": "Хобби", "kind": "expense",
                                            "color": "#A1b2C3", "icon": "Palette"})
    assert r.status_code == 201, r.text
    # recurring description is bounded like other texts
    base = {"title": "Аренда", "amount": "100", "next_due_on": date.today().isoformat()}
    r = await api.post("/recurring", json={**base, "description": "x" * 501})
    assert r.status_code == 422
    r = await api.post("/recurring", json={**base, "description": "x" * 500})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    r = await api.patch(f"/recurring/{rid}", json={"version": r.json()["version"],
                                                    "description": "y" * 501})
    assert r.status_code == 422


@pytest.mark.parametrize(
    ("module", "attr", "path", "body"),
    [
        ("app.services.accounts.service", "MAX_ACCOUNTS", "/accounts",
         {"name": "Wise", "kind": "bank", "currency": "USD"}),
        ("app.services.categories.service", "MAX_CATEGORIES", "/categories",
         {"name": "Хобби", "kind": "expense"}),
        ("app.services.merchants.service", "MAX_MERCHANTS", "/merchants", {"name": "Shop B"}),
    ],
)
async def test_per_user_object_limits(api, session, user, monkeypatch, module, attr, path, body):
    import importlib

    mod = importlib.import_module(module)
    n = len((await api.get(path)).json())
    monkeypatch.setattr(mod, attr, n + 1)
    r = await api.post(path, json=body)
    assert r.status_code == 201, r.text
    second = {**body, "name": body["name"] + " 2"}
    r = await api.post(path, json=second)
    assert r.status_code == 422 and _code(r) == "limit_reached", r.text
    assert r.json()["error"]["details"]["limit"] == n + 1


async def test_people_limit(api, session, user, monkeypatch):
    from app.services.debts import service as debts

    monkeypatch.setattr(debts, "MAX_PEOPLE", 1)
    body = {"op": "lend", "amount": "1000", "currency": "VND"}
    r = await api.post("/debts/operations", json={**body, "person_name": "Коля"}, headers=idem())
    assert r.status_code == 201, r.text
    # the same person again is fine, a new one is over the limit
    r = await api.post("/debts/operations", json={**body, "person_name": "Коля"}, headers=idem())
    assert r.status_code == 201, r.text
    r = await api.post("/debts/operations", json={**body, "person_name": "Петя"}, headers=idem())
    assert r.status_code == 422 and _code(r) == "limit_reached", r.text


# ------------------------------------------------------------------------------- int64


async def test_ids_and_offsets_above_bigint_are_422(api, session, user):
    for path in (f"/transactions/{BIG}", f"/accounts/{BIG}", f"/debts/{BIG}",
                 f"/transactions/{BIG}/history", "/transactions/0"):
        r = await api.get(path)
        assert r.status_code == 422, (path, r.status_code)
    for params in ({"offset": BIG}, {"category_id": BIG}, {"account_id": BIG},
                   {"merchant_id": BIG}):
        r = await api.get("/transactions", params=params)
        assert r.status_code == 422, params
    assert (await api.get("/actions", params={"offset": BIG})).status_code == 422
    assert (await api.get("/debts/1", params={"offset": BIG})).status_code == 422
    assert (await api.patch(f"/merchants/{BIG}", json={"name": "x"})).status_code == 422
    assert (await api.delete(f"/recurring/{BIG}")).status_code == 422
    food = await cat_id(session, user.id, "Еда")
    for body in ({"category_id": BIG}, {"category_id": food, "merchant_id": BIG},
                 {"category_id": food, "account_id": BIG}):
        r = await api.post("/transactions", headers=idem(), json={
            "kind": "expense", "amount": "1000", "currency": "VND", **body})
        assert r.status_code == 422, (body, r.text)
    r = await api.post("/transfers", headers=idem(), json={
        "from_account_id": BIG, "to_account_id": 1, "amount": "1", "currency": "VND"})
    assert r.status_code == 422


async def test_driver_value_errors_map_to_422_not_500(session):
    """Whatever still reaches the driver out of range (int64, NUMERIC overflow) is a 422;
    other database errors stay a 500."""
    from app.db import get_session

    app = FastAPI()
    install_error_handlers(app)

    async def _session():  # type: ignore[no-untyped-def]
        yield session

    app.dependency_overrides[get_session] = _session

    @app.get("/int64")
    async def _int64() -> dict:
        async with session.begin_nested():
            await session.get(Account, BIG)
        return {}

    @app.get("/numeric")
    async def _numeric() -> dict:
        async with session.begin_nested():
            await session.execute(text("select cast(:v as numeric(5, 2))"), {"v": "123456"})
        return {}

    @app.get("/other")
    async def _other() -> dict:
        async with session.begin_nested():
            await session.execute(text("select * from no_such_table_pf"))
        return {}

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        for path in ("/int64", "/numeric"):
            r = await c.get(path)
            assert r.status_code == 422, (path, r.text)
            assert r.json()["error"]["code"] == "validation_error"
        r = await c.get("/other")
        assert r.status_code == 500 and r.json()["error"]["code"] == "internal"


# ------------------------------------------------------------------------------- dates


async def test_dates_outside_1970_2100_are_422(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    tx = {"kind": "expense", "amount": "1000", "currency": "VND", "category_id": food}
    for extra in ({"occurred_on": "0001-01-01"}, {"occurred_on": "9999-12-31"},
                  {"occurred_at": "0001-01-01T00:00:00+14:00"},
                  {"occurred_at": "9999-12-31T23:59:59-12:00"}):
        r = await api.post("/transactions", headers=idem(), json={**tx, **extra})
        assert r.status_code == 422, (extra, r.text)
    for params in ({"date_from": "0001-01-01"}, {"date_to": "9999-12-31"}):
        assert (await api.get("/transactions", params=params)).status_code == 422
        assert (await api.get("/export/transactions", params=params)).status_code == 422
    for path in ("/stats/by-category", "/stats/series", "/stats/top", "/stats/compare"):
        r = await api.get(path, params={"preset": "custom", "date_from": "0001-01-01",
                                        "date_to": "0001-01-31"})
        assert r.status_code == 422, (path, r.status_code)
    r = await api.get("/stats/compare", params={"preset": "custom", "date_from": "2026-01-01",
                                                "date_to": "2026-01-31",
                                                "prev_date_from": "0001-01-01",
                                                "prev_date_to": "0001-01-31"})
    assert r.status_code == 422
    assert (await api.get("/reports/daily", params={"on": "9999-12-31"})).status_code == 422
    assert (await api.get("/rates/convert", params={"amount": "1", "from_": "USD",
                                                    "to": "VND",
                                                    "on": "0001-01-01"})).status_code == 422
    r = await api.post("/recurring", json={"title": "Аренда", "next_due_on": "9999-12-31"})
    assert r.status_code == 422
    r = await api.post("/debts/operations", headers=idem(), json={
        "op": "lend", "person_name": "Коля", "amount": "1000", "currency": "VND",
        "occurred_on": "0001-01-01"})
    assert r.status_code == 422
    cash = await acc_id(session, user.id, "Наличные")
    r = await api.put(f"/accounts/{cash}/opening", json={
        "asset": "VND", "amount": "1000", "opening_at": "0001-01-01T00:00:00+14:00"})
    assert r.status_code == 422


def test_date_arithmetic_overflow_is_a_validation_error():
    """Below the schemas: period helpers and the recurring schedule never raise OverflowError
    (old rows written before the date limit, LLM output)."""
    from app.models import RecurringTransaction
    from app.schemas.common import DateRange, PeriodSpec
    from app.services.recurring.service import latest_due, next_due
    from app.services.stats.periods import previous_range, resolve_period

    spec = PeriodSpec.model_construct(preset="custom", date_from=date(1, 1, 1),
                                      date_to=date(1, 1, 31))
    rng = DateRange.model_construct(date_from=date(1, 1, 1), date_to=date(1, 1, 31), label="")
    with pytest.raises(ValidationFailed):
        previous_range(spec, rng, date(2026, 10, 28))
    spec = PeriodSpec.model_construct(preset="custom", date_from=date(9999, 12, 1),
                                      date_to=date(9999, 12, 31))
    resolve_period(spec, date(2026, 10, 28))  # in range of the calendar: fine
    r = RecurringTransaction(frequency="monthly", interval=1, day_of_month=15,
                             next_due_on=date(9999, 12, 15))
    with pytest.raises(ValidationFailed):
        next_due(r, date(9999, 12, 15))
    assert latest_due(r, date(9999, 12, 31)) == date(9999, 12, 15)


async def test_resolve_when_overflow_is_422(session, user):
    from app.services.ledger.common import resolve_when

    with pytest.raises(ValidationFailed):
        # 23:00 on 31.12.9999 in UTC is already the year 10000 in Asia/Ho_Chi_Minh
        await resolve_when(session, user.id, None, datetime(9999, 12, 31, 23, 0, tzinfo=UTC))


# -------------------------------------------------------------------------------


async def test_occurred_on_and_occurred_at_must_agree(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    tx = {"kind": "expense", "amount": "1000", "currency": "VND", "category_id": food}
    r = await api.post("/transactions", headers=idem(), json={
        **tx, "occurred_on": "2026-10-20", "occurred_at": "2026-10-25T10:00:00+07:00"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"]["field"] == "occurred_on"
    r = await api.post("/transactions", headers=idem(), json={
        **tx, "occurred_on": "2026-10-25", "occurred_at": "2026-10-25T10:00:00+07:00"})
    assert r.status_code == 201, r.text
    t = r.json()["transaction"]
    assert t["occurred_on"] == "2026-10-25"
    r = await api.patch(f"/transactions/{t['id']}", json={
        "version": t["version"], "occurred_on": "2026-10-01",
        "occurred_at": "2026-10-02T10:00:00+07:00"})
    assert r.status_code == 422, r.text


async def test_opening_balance_in_the_future_is_422(api, session, user):
    cash = await acc_id(session, user.id, "Наличные")
    future = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    r = await api.put(f"/accounts/{cash}/opening",
                      json={"asset": "VND", "amount": "1000", "opening_at": future})
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "opening_at"
    past = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    r = await api.put(f"/accounts/{cash}/opening",
                      json={"asset": "VND", "amount": "1000", "opening_at": past})
    assert r.status_code == 200, r.text
