"""Transfers / exchanges / reconciliation never count as income/expense; balances only when the
opening balance is known; onboarding."""

from __future__ import annotations

from decimal import Decimal

from app.schemas.transactions import TransactionFilter
from app.services.ledger import query
from tests.core.conftest import acc_id, cat_id, idem


def _asset(acc_out, asset):  # type: ignore[no-untyped-def]
    return next(a for a in acc_out["assets"] if a["asset"] == asset)


async def _acc(api, aid):  # type: ignore[no-untyped-def]
    r = await api.get(f"/accounts/{aid}")
    assert r.status_code == 200, r.text
    return r.json()


async def test_opening_unknown_balance_is_null_and_onboarding(api, session, user):
    bank = await acc_id(session, user.id, "Карта")
    bal = (await api.get("/accounts/balances")).json()
    assert bal["total_vnd"] is None and bal["unknown_count"] >= 2
    b = next(a for a in bal["accounts"] if a["id"] == bank)
    assert _asset(b, "VND")["balance"] is None  # «остаток не задан», not 0
    kb = await acc_id(session, user.id, "Кошелёк")
    r = await api.post("/accounts/onboarding", json={"items": [
        {"account_id": bank, "asset": "VND", "amount": "5320000"},
        {"account_id": kb, "asset": "TON", "amount": "10"},
    ]})
    assert r.status_code == 200, r.text
    out = r.json()
    b = next(a for a in out["accounts"] if a["id"] == bank)
    assert _asset(b, "VND")["balance"] == "5320000"
    k = next(a for a in out["accounts"] if a["id"] == kb)
    assert _asset(k, "TON")["balance"] == "10" and _asset(k, "TON")["approx_usd"] == "54"
    assert Decimal(out["total_vnd"]) == Decimal("5320000") + Decimal("54") * 26140
    from app.models import UserSettings

    st = await session.get(UserSettings, user.id)
    assert st.onboarding_done is True


async def test_transfer_moves_money_without_pnl(api, session, user):
    bank = await acc_id(session, user.id, "Карта")
    cash = await acc_id(session, user.id, "Наличные")
    await api.put(f"/accounts/{bank}/opening", json={"asset": "VND", "amount": "10000000"})
    await api.put(f"/accounts/{cash}/opening", json={"asset": "VND", "amount": "0"})
    r = await api.post("/transfers", headers=idem(), json={
        "from_account_id": bank, "to_account_id": cash, "amount": "2000000",
        "currency": "VND", "description": "снял налом"})
    assert r.status_code == 201, r.text
    tx = r.json()["transaction"]
    assert tx["kind"] == "transfer" and tx["category"] is None
    assert tx["counter_amount"] == "2000000" and tx["title"] == "Перевод Карта → Наличные"
    assert _asset(await _acc(api, bank), "VND")["balance"] == "8000000"
    assert _asset(await _acc(api, cash), "VND")["balance"] == "2000000"
    tot = (await api.get("/transactions")).json()["totals"]
    assert tot["expense_vnd"] == "0" and tot["income_vnd"] == "0" and tot["count"] == 1
    # same account / unsupported currency → 422
    same = await api.post("/transfers", headers=idem(), json={
        "from_account_id": bank, "to_account_id": bank, "amount": "1", "currency": "VND"})
    assert same.status_code == 422
    rub = await acc_id(session, user.id, "Карта RUB")
    bad = await api.post("/transfers", headers=idem(), json={
        "from_account_id": bank, "to_account_id": rub, "amount": "1", "currency": "VND"})
    assert bad.status_code == 422


async def test_exchange_stores_actual_rate(api, session, user):
    exch = await acc_id(session, user.id, "Биржа")
    kb = await acc_id(session, user.id, "Кошелёк")
    await api.put(f"/accounts/{exch}/opening", json={"asset": "USDT", "amount": "1000"})
    r = await api.post("/exchanges", headers=idem(), json={
        "from_account_id": exch, "to_account_id": exch, "amount": "100", "currency": "USDT",
        "counter_amount": "18.2", "counter_currency": "TON"})
    assert r.status_code == 201, r.text
    tx = r.json()["transaction"]
    assert tx["kind"] == "exchange" and tx["account_rate"] == "0.182"
    assert tx["title"] == "Обмен USDT → TON · Биржа"
    acc = await _acc(api, exch)
    assert _asset(acc, "USDT")["balance"] == "900"
    assert _asset(acc, "TON")["balance"] is None  # coin added, opening not set
    # cross-account exchange
    r2 = await api.post("/exchanges", headers=idem(), json={
        "from_account_id": exch, "to_account_id": kb, "amount": "50", "currency": "USDT",
        "counter_amount": "9", "counter_currency": "TON"})
    assert r2.status_code == 201
    tot = (await api.get("/transactions")).json()["totals"]
    assert tot["expense_usd"] == "0" and tot["income_usd"] == "0"
    bad = await api.post("/exchanges", headers=idem(), json={
        "from_account_id": exch, "to_account_id": kb, "amount": "1", "currency": "USDT",
        "counter_amount": "1", "counter_currency": "USDT"})
    assert bad.status_code == 422


async def test_reconciliation(api, session, user):
    bank = await acc_id(session, user.id, "Карта")
    food = await cat_id(session, user.id, "Еда")
    # unknown opening → «на карте 5.32м» sets the opening balance (undoable)
    r = await api.post("/adjustments", headers=idem(), json={
        "account_id": bank, "asset": "VND", "actual_balance": "5320000"})
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["kind"] == "opening_set" and res["action_id"] and res["transaction"] is None
    await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "100000", "currency": "VND", "category_id": food})
    # «на карте 5.2м» → adjustment for the difference (−20 000)
    r = await api.post("/adjustments", headers=idem(), json={
        "account_id": bank, "asset": "VND", "actual_balance": "5200000"})
    res = r.json()
    assert res["kind"] == "adjusted", res
    assert res["previous_balance"] == "5220000" and res["delta"] == "-20000"
    assert res["transaction"]["kind"] == "adjustment"
    assert res["transaction"]["account_amount"] == "-20000"
    assert res["transaction"]["amount"] == "20000"
    assert _asset(await _acc(api, bank), "VND")["balance"] == "5200000"
    # matches → nothing written
    r = await api.post("/adjustments", headers=idem(), json={
        "account_id": bank, "asset": "VND", "actual_balance": "5200000"})
    res = r.json()
    assert res["kind"] == "matches" and res["action_id"] is None and res["delta"] == "0"
    # adjustments are not expenses
    tot = (await api.get("/transactions")).json()["totals"]
    assert tot["expense_vnd"] == "100000"
    # undo the adjustment → balance back
    adj_actions = [a for a in (await api.get("/actions")).json() if a["kind"] == "adjust_balance"]
    u = await api.post(f"/actions/{adj_actions[0]['id']}/undo", headers=idem())
    assert u.status_code == 200 and u.json()["status"] == "undone"
    assert _asset(await _acc(api, bank), "VND")["balance"] == "5220000"
    # undo set_opening → balance unknown again
    so = [a for a in (await api.get("/actions")).json() if a["kind"] == "set_opening"][0]
    assert (await api.post(f"/actions/{so['id']}/undo", headers=idem())).status_code == 200
    assert _asset(await _acc(api, bank), "VND")["balance"] is None


async def test_balance_counts_only_postings_after_opening(api, session, user):
    from datetime import UTC, datetime

    cash = await acc_id(session, user.id, "Наличные")
    food = await cat_id(session, user.id, "Еда")
    await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "50000", "currency": "VND", "category_id": food,
        "account_id": cash, "occurred_on": "2026-10-01"})
    r = await api.put(f"/accounts/{cash}/opening", json={
        "asset": "VND", "amount": "1000000", "opening_at": datetime(2026, 10, 10, tzinfo=UTC)
        .isoformat()})
    assert r.status_code == 200, r.text
    assert _asset(r.json(), "VND")["balance"] == "1000000"
    await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "30000", "currency": "VND", "category_id": food,
        "account_id": cash})
    assert _asset(await _acc(api, cash), "VND")["balance"] == "970000"
    # clearing the opening hides the balance again
    r = await api.put(f"/accounts/{cash}/opening", json={"asset": "VND", "amount": None})
    assert _asset(r.json(), "VND")["balance"] is None


async def test_pnl_totals_ignore_internal_moves(api, session, user):
    bank = await acc_id(session, user.id, "Карта")
    cash = await acc_id(session, user.id, "Наличные")
    food = await cat_id(session, user.id, "Еда")
    clients = await cat_id(session, user.id, "Клиенты", "income")
    await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "120000", "currency": "VND", "category_id": food})
    await api.post("/transactions", headers=idem(), json={
        "kind": "income", "amount": "3000000", "currency": "VND", "category_id": clients})
    await api.post("/transfers", headers=idem(), json={
        "from_account_id": bank, "to_account_id": cash, "amount": "500000", "currency": "VND"})
    await api.put(f"/accounts/{bank}/opening", json={"asset": "VND", "amount": "0"})
    await api.post("/adjustments", headers=idem(), json={
        "account_id": bank, "asset": "VND", "actual_balance": "999"})
    tot = await query.totals(session, user.id, TransactionFilter())
    assert tot.expense_vnd == Decimal("120000") and tot.income_vnd == Decimal("3000000")
    assert tot.count == 4
    only_transfers = await query.totals(session, user.id, TransactionFilter(kinds=["transfer"]))
    assert only_transfers.expense_vnd == 0 and only_transfers.income_vnd == 0


async def test_accounts_crud(api, session, user):
    r = await api.post("/accounts", json={"name": "Wise", "kind": "bank", "currency": "USD",
                                          "aliases": ["Вайз"], "opening_balance": "100"})
    assert r.status_code == 201, r.text
    acc = r.json()
    assert acc["aliases"] == ["вайз"] and acc["icon"] == "Landmark"
    assert _asset(acc, "USD")["balance"] == "100"
    dup = await api.post("/accounts", json={"name": "wise", "kind": "bank", "currency": "USD"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "already_exists"
    taken = await api.patch(f"/accounts/{acc['id']}", json={
        "version": acc["version"], "default_for_currencies": ["USD"]})
    assert taken.status_code == 200
    bank = await acc_id(session, user.id, "Карта")
    vnd_taken = await api.post("/accounts", json={
        "name": "VCB", "kind": "bank", "currency": "VND", "default_for_currencies": ["VND"]})
    assert vnd_taken.status_code == 422
    stale = await api.patch(f"/accounts/{acc['id']}", json={"version": 1, "name": "W"})
    assert stale.status_code == 409
    # used account → archived; unused → deleted
    food = await cat_id(session, user.id, "Еда")
    await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "5", "currency": "USD", "category_id": food})
    d = await api.delete(f"/accounts/{acc['id']}")
    assert d.status_code == 200
    assert acc["id"] not in [a["id"] for a in (await api.get("/accounts")).json()]
    arch = (await api.get("/accounts", params={"include_archived": True})).json()
    assert any(a["id"] == acc["id"] and a["is_archived"] for a in arch)
    assert bank in [a["id"] for a in arch]
