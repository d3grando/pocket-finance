"""Ledger regressions: titles, money guards, actual charge, buy rate, exchange
correction, «С какого?» answered twice, coin from a ₫ account, NUL bytes, XLSX cells."""

from __future__ import annotations

import io
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import select

from app.models import Posting
from app.services.rates import service as rates_service
from tests.core.conftest import acc_id, cat_id, idem


async def _post(api, body, status=201):  # type: ignore[no-untyped-def]
    r = await api.post("/transactions", json=body, headers=idem())
    assert r.status_code == status, r.text
    return r.json()


# ------------------------------------------------------------------------------- title


async def test_title_is_description_or_merchant_else_category(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    soft = await cat_id(session, user.id, "Софт и AI")
    t1 = (await _post(api, {"kind": "expense", "amount": "81000", "currency": "VND",
                            "category_id": food, "description": "обед Highlands"}))
    assert t1["transaction"]["title"] == "обед Highlands"
    t2 = (await _post(api, {"kind": "expense", "amount": "3", "currency": "TON",
                            "category_id": soft, "account_none": True,
                            "counterparty": "@alice", "description": "аккаунт GPT Pro"}))
    assert t2["transaction"]["title"] == "аккаунт GPT Pro · @alice"
    t3 = (await _post(api, {"kind": "expense", "amount": "50000", "currency": "VND",
                            "category_id": food}))
    assert t3["transaction"]["title"] == "Еда"


# ------------------------------------------------------------------------------- money guards


async def test_more_than_8_decimals_is_422_not_500(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    for amount in ("0.000000001", "0.123456789"):
        r = await api.post("/transactions", headers=idem(), json={
            "kind": "expense", "amount": amount, "currency": "BTC", "category_id": food,
            "account_none": True})
        assert r.status_code == 422, r.text
        assert "8 знаков" in r.json()["error"]["message"]
    ok = await _post(api, {"kind": "expense", "amount": "0.12345678", "currency": "BTC",
                           "category_id": food, "account_none": True})
    assert ok["transaction"]["amount"] == "0.12345678"


async def test_huge_amount_overflowing_vnd_is_422(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    # 1e14 TON ≈ 1.4e19 ₫ — fits the input cap (1e19) but not NUMERIC(28,8) after the rate
    r = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "100000000000000", "currency": "TON",
        "category_id": food, "account_none": True})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["message"] == "Слишком большая сумма"


async def test_same_currency_charge_must_equal_amount(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    bank = await acc_id(session, user.id, "Карта")
    r = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "100000", "currency": "VND", "category_id": food,
        "account_id": bank, "account_amount": "150000"})
    assert r.status_code == 422, r.text
    same = await _post(api, {"kind": "expense", "amount": "100000", "currency": "VND",
                             "category_id": food, "account_id": bank,
                             "account_amount": "100000"})
    tx = same["transaction"]
    p = await api.patch(f"/transactions/{tx['id']}",
                        json={"version": tx["version"], "account_amount": "150000"})
    assert p.status_code == 422, p.text


async def test_actual_charge_from_bank_is_the_vnd_cost(api, session, user):
    """«васт списали 21.3м»: ₫ summaries use the real charge, USD stays as written."""
    srv = await cat_id(session, user.id, "Серверы/инфра")
    bank = await acc_id(session, user.id, "Карта")
    tx = (await _post(api, {"kind": "expense", "amount": "815", "currency": "USD",
                            "category_id": srv, "account_id": bank}))["transaction"]
    assert tx["amount_vnd"] == str(815 * 26140) and tx["is_estimated"] is True
    p = await api.patch(f"/transactions/{tx['id']}",
                        json={"version": tx["version"], "account_amount": "21300000"})
    assert p.status_code == 200, p.text
    t2 = p.json()["transaction"]
    assert t2["is_estimated"] is False and t2["account_amount"] == "21300000"
    assert t2["amount_vnd"] == "21300000" and t2["amount_usd"] == "815"


async def test_income_into_bank_uses_buy_rate(api, session, user, monkeypatch):
    real = rates_service.get_rate

    async def get_rate(session, base, quote, on, *, kind="sell", fetch_missing=True):  # type: ignore[no-untyped-def]
        if (base.upper(), quote.upper()) == ("USD", "VND") and kind == "buy":
            return rates_service.RateQuote("USD", "VND", Decimal("25640"), on, "vietcombank",
                                           "buy")
        return await real(session, base, quote, on, kind=kind, fetch_missing=fetch_missing)

    monkeypatch.setattr(rates_service, "get_rate", get_rate)
    clients = await cat_id(session, user.id, "Клиенты", "income")
    bank = await acc_id(session, user.id, "Карта")
    inc = (await _post(api, {"kind": "income", "amount": "100", "currency": "USD",
                             "category_id": clients, "account_id": bank}))["transaction"]
    assert inc["account_amount"] == "2564000" and inc["account_rate"] == "25640"
    assert inc["is_estimated"] is True
    food = await cat_id(session, user.id, "Еда")
    exp = (await _post(api, {"kind": "expense", "amount": "100", "currency": "USD",
                             "category_id": food, "account_id": bank}))["transaction"]
    assert exp["account_amount"] == "2614000"  # charges stay at the sell rate


async def test_exchange_received_amount_can_be_corrected(api, session, user):
    exch = await acc_id(session, user.id, "Биржа")
    bank = await acc_id(session, user.id, "Карта")
    r = await api.post("/exchanges", headers=idem(), json={
        "from_account_id": exch, "to_account_id": bank, "amount": "100", "currency": "USDT",
        "counter_amount": "2500000", "counter_currency": "VND"})
    assert r.status_code == 201, r.text
    tx = r.json()["transaction"]
    p = await api.patch(f"/transactions/{tx['id']}",
                        json={"version": tx["version"], "counter_amount": "2450000"})
    assert p.status_code == 200, p.text
    t2 = p.json()["transaction"]
    assert t2["counter_amount"] == "2450000" and t2["account_rate"] == "24500"
    rows = (await session.execute(
        select(Posting.account_id, Posting.amount).where(Posting.transaction_id == tx["id"])
    )).all()
    assert sorted((a, Decimal(x)) for a, x in rows) == sorted(
        [(exch, Decimal("-100")), (bank, Decimal("2450000"))])
    food = await cat_id(session, user.id, "Еда")
    e = (await _post(api, {"kind": "expense", "amount": "1000", "currency": "VND",
                           "category_id": food}))["transaction"]
    bad = await api.patch(f"/transactions/{e['id']}",
                          json={"version": e["version"], "counter_amount": "5"})
    assert bad.status_code == 422


# ------------------------------------------------------------------------------- accounts


async def test_choose_account_twice_without_version_is_409(api, session, user):
    subs = await cat_id(session, user.id, "Подписки")
    kb = await acc_id(session, user.id, "Кошелёк")
    piggy = await acc_id(session, user.id, "Копилка")
    tx = (await _post(api, {"kind": "expense", "amount": "15", "currency": "USDT",
                            "category_id": subs}))["transaction"]
    assert tx["status"] == "needs_account"
    r1 = await api.post(f"/transactions/{tx['id']}/account", json={"account_id": kb},
                        headers=idem())
    assert r1.status_code == 200, r1.text
    r2 = await api.post(f"/transactions/{tx['id']}/account", json={"account_id": piggy},
                        headers=idem())
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "version_conflict"
    rows = (await session.execute(
        select(Posting.account_id).where(Posting.transaction_id == tx["id"]))).scalars().all()
    assert list(rows) == [kb]
    # an explicit version (web «Сменить счёт») still moves a posted operation
    v = r1.json()["transaction"]["version"]
    r3 = await api.post(f"/transactions/{tx['id']}/account",
                        json={"account_id": piggy, "version": v}, headers=idem())
    assert r3.status_code == 200 and r3.json()["transaction"]["account"]["id"] == piggy


async def test_coin_cannot_be_charged_from_vnd_account(api, session, user):
    gifts = await cat_id(session, user.id, "Подарки")
    bank = await acc_id(session, user.id, "Карта")
    tx = (await _post(api, {"kind": "expense", "amount": "3", "currency": "TON",
                            "category_id": gifts}))["transaction"]
    r = await api.post(f"/transactions/{tx['id']}/account", json={"account_id": bank},
                       headers=idem())
    assert r.status_code == 422 and "нет TON" in r.json()["error"]["message"]
    r2 = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "1", "currency": "TON", "category_id": gifts,
        "account_id": bank})
    assert r2.status_code == 422
    # fiat USD from Карта is still a ≈ charge
    ok = await _post(api, {"kind": "expense", "amount": "15", "currency": "USD",
                           "category_id": gifts, "account_id": bank})
    assert ok["transaction"]["is_estimated"] is True


# ------------------------------------------------------------------------------- NUL bytes


async def test_nul_bytes_are_422(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    for path, params in (("/transactions", {"q": "\x00"}),
                         ("/export/transactions", {"q": "a\x00"}),
                         ("/categories/suggest", {"q": "\x00"}),
                         ("/merchants", {"q": "\x00"}),
                         ("/audit", {"entity_type": "transaction", "entity_id": "\x00"})):
        r = await api.get(path, params=params)
        assert r.status_code == 422, (path, r.status_code, r.text)
    for field in ("description", "counterparty"):
        r = await api.post("/transactions", headers=idem(), json={
            "kind": "expense", "amount": "1000", "currency": "VND", "category_id": food,
            field: "x\x00y"})
        assert r.status_code == 422, (field, r.text)
    r = await api.post("/accounts", json={"name": "a\x00", "kind": "bank", "currency": "VND"})
    assert r.status_code == 422


# ------------------------------------------------------------------------------- XLSX


async def test_xlsx_cells_are_typed_and_not_prefixed(api, session, user):
    from openpyxl import load_workbook

    food = await cat_id(session, user.id, "Еда")
    people = await cat_id(session, user.id, "Переводы людям")
    await _post(api, {"kind": "expense", "amount": "3", "currency": "TON", "account_none": True,
                      "category_id": people, "counterparty": "@bob",
                      "description": "=HYPERLINK(\"http://evil.example\",\"click\")"})
    await _post(api, {"kind": "expense", "amount": "98765432109876.54321", "currency": "VND",
                      "category_id": food, "description": "-скидка 10%"})
    x = await api.get("/export/transactions", params={"format": "xlsx"})
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    ws = wb.active
    head = [c.value for c in ws[1]]
    col = {h: i for i, h in enumerate(head)}
    rows = list(ws.iter_rows(min_row=2))
    ton = next(r for r in rows if r[col["Валюта"]].value == "TON")
    vnd = next(r for r in rows if r[col["Валюта"]].value == "VND")
    assert ton[col["Получатель"]].value == "@bob"
    assert ton[col["Продавец"]].value == "@bob"  # rule learned from the @handle
    cell = ton[col["Назначение"]]
    assert cell.value.startswith("=HYPERLINK") and cell.data_type == "s"  # text, not formula
    assert vnd[col["Назначение"]].value == "-скидка 10%"
    d = ton[col["Дата"]].value
    assert isinstance(d, datetime)  # a real date cell (openpyxl reads dates as datetime)
    assert ton[col["Дата"]].number_format == "yyyy-mm-dd"
    t = ton[col["Время"]].value
    assert isinstance(t, time) and ton[col["Время"]].number_format == "hh:mm"
    assert vnd[col["Сумма"]].value == "98765432109876.54321"  # > 15 digits: exact text
    assert ton[col["Сумма"]].value == 3
    # CSV keeps the formula guard
    c = await api.get("/export/transactions", params={"format": "csv"})
    assert "'=HYPERLINK" in c.content.decode("utf-8-sig")
