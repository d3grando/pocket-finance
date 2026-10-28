"""Business operations through REST: expense/income/refund, needs_account, estimated charge,
multi-asset wallets, recipient/purpose, own accounts, versions, soft delete, history."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.models import AccountAsset, Posting, Transaction
from tests.core.conftest import acc_id, cat_id, idem


async def _create(api, body, status=201):  # type: ignore[no-untyped-def]
    r = await api.post("/transactions", json=body, headers=idem())
    assert r.status_code == status, r.text
    return r.json()


async def _postings(session, tx_id):  # type: ignore[no-untyped-def]
    rows = await session.execute(
        select(Posting.account_id, Posting.asset, Posting.amount).where(
            Posting.transaction_id == tx_id
        )
    )
    return [tuple(r) for r in rows]


async def test_expense_vnd_goes_to_bank_with_rates(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    bank = await acc_id(session, user.id, "Карта")
    res = await _create(api, {"kind": "expense", "amount": "150000", "currency": "VND",
                              "category_id": food, "description": "обед"})
    tx = res["transaction"]
    assert res["action_id"]
    assert tx["status"] == "posted" and tx["account"]["id"] == bank
    assert tx["amount"] == "150000" and isinstance(tx["amount"], str)
    assert tx["account_amount"] == "150000" and tx["account_asset"] == "VND"
    assert tx["is_estimated"] is False
    assert tx["amount_vnd"] == "150000"
    # 150000 / 26140 (VCB sell), stored with 8 decimals
    assert Decimal(tx["amount_usd"]) == (Decimal(150000) / Decimal(26140)).quantize(
        Decimal("1e-8"))
    assert tx["rate_source"] == "vietcombank" and tx["rate_date"] == tx["occurred_on"]
    assert tx["title"] == "обед" and tx["category"]["name"] == "Еда"
    assert await _postings(session, tx["id"]) == [(bank, "VND", Decimal("-150000"))]
    row = await session.get(Transaction, tx["id"])
    assert isinstance(row.amount, Decimal) and isinstance(row.amount_usd, Decimal)


async def test_usd_without_account_needs_account_then_assign(api, session, user):
    subs = await cat_id(session, user.id, "Подписки")
    bank = await acc_id(session, user.id, "Карта")
    tx = (await _create(api, {"kind": "expense", "amount": "15", "currency": "USD",
                              "category_id": subs}))["transaction"]
    assert tx["status"] == "needs_account" and tx["account"] is None
    assert await _postings(session, tx["id"]) == []
    assert (await api.get("/transactions/needs-account/count")).json() == {"count": 1}
    # counted in expenses (totals) but not in balances
    page = (await api.get("/transactions")).json()
    assert Decimal(page["totals"]["expense_usd"]) == Decimal("15")
    r = await api.post(f"/transactions/{tx['id']}/account", json={"account_id": bank},
                       headers=idem())
    assert r.status_code == 200, r.text
    t2 = r.json()["transaction"]
    assert t2["status"] == "posted" and t2["is_estimated"] is True
    assert t2["account_amount"] == "392100" and t2["account_rate"] == "26140"
    assert await _postings(session, tx["id"]) == [(bank, "VND", Decimal("-392100"))]
    assert (await api.get("/transactions/needs-account/count")).json() == {"count": 0}


async def test_actual_charge_fixes_estimate(api, session, user):
    """«васт списали 21.3м»: only the charge changes, the USD amount stays as written."""
    srv = await cat_id(session, user.id, "Серверы/инфра")
    bank = await acc_id(session, user.id, "Карта")
    tx = (await _create(api, {"kind": "expense", "amount": "815", "currency": "USD",
                              "category_id": srv, "account_id": bank}))["transaction"]
    assert tx["is_estimated"] is True
    assert tx["account_amount"] == str(815 * 26140)
    r = await api.patch(f"/transactions/{tx['id']}",
                        json={"version": tx["version"], "account_amount": "21300000"})
    assert r.status_code == 200, r.text
    t2 = r.json()["transaction"]
    assert t2["amount"] == "815" and t2["currency"] == "USD"
    assert t2["amount_usd"] == "815"
    assert t2["account_amount"] == "21300000" and t2["is_estimated"] is False
    assert Decimal(t2["account_rate"]) == (Decimal(21300000) / 815).quantize(Decimal("1e-12"))
    assert t2["version"] == tx["version"] + 1
    assert await _postings(session, tx["id"]) == [(bank, "VND", Decimal("-21300000"))]


async def test_crypto_wallet_adds_coin_on_first_mention(api, session, user):
    soft = await cat_id(session, user.id, "Софт и AI")
    kb = await acc_id(session, user.id, "Кошелёк")
    assert await session.scalar(
        select(func.count()).where(AccountAsset.account_id == kb)) == 0
    tx = (await _create(api, {"kind": "expense", "amount": "3", "currency": "TON",
                              "category_id": soft, "account_id": kb,
                              "counterparty": "@alice",
                              "description": "аккаунт GPT Pro"}))["transaction"]
    assert tx["account_asset"] == "TON" and tx["account_amount"] == "3"
    assert tx["amount_usd"] == "16.2" and tx["is_business"] is True
    assert tx["counterparty"] == "@alice" and tx["description"] == "аккаунт GPT Pro"
    assets = list(await session.scalars(select(AccountAsset).where(AccountAsset.account_id == kb)))
    assert [a.asset for a in assets] == ["TON"] and assets[0].opening_balance is None
    # recipient is remembered → next transfer to @alice suggests the same category
    m = (await api.get("/merchants", params={"q": "alice"})).json()
    assert m[0]["handle"] == "alice" and m[0]["category_name"] == "Софт и AI"
    assert tx["merchant"]["id"] == m[0]["id"]
    # balances: no opening → not shown (None), never 0
    accs = (await api.get("/accounts")).json()
    kb_out = next(a for a in accs if a["id"] == kb)
    assert kb_out["assets"][0]["balance"] is None


async def test_category_change_updates_merchant_rule(api, session, user):
    soft = await cat_id(session, user.id, "Софт и AI")
    goods = await cat_id(session, user.id, "Закупка товара")
    kb = await acc_id(session, user.id, "Кошелёк")
    tx = (await _create(api, {"kind": "expense", "amount": "3", "currency": "TON",
                              "category_id": soft, "account_id": kb,
                              "counterparty": "@seller1"}))["transaction"]
    r = await api.patch(f"/transactions/{tx['id']}",
                        json={"version": tx["version"], "category_id": goods})
    assert r.status_code == 200
    m = (await api.get("/merchants", params={"q": "seller1"})).json()[0]
    assert m["category_id"] == goods and m["hits"] == 1


async def test_without_account_counts_in_expenses_not_in_balances(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    tx = (await _create(api, {"kind": "expense", "amount": "50", "currency": "USDT",
                              "category_id": food, "account_none": True}))["transaction"]
    assert tx["status"] == "posted" and tx["account"] is None
    assert await _postings(session, tx["id"]) == []
    page = (await api.get("/transactions")).json()
    assert Decimal(page["totals"]["expense_usd"]) == Decimal("50")
    assert (await api.get("/transactions/needs-account/count")).json()["count"] == 0
    bad = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "1", "currency": "USDT", "category_id": food,
        "account_none": True, "account_id": await acc_id(session, user.id, "Кошелёк")})
    assert bad.status_code == 422


async def test_refund_is_negative_expense_of_same_category(api, session, user):
    shop = await cat_id(session, user.id, "Покупки")
    e = (await _create(api, {"kind": "expense", "amount": "500000", "currency": "VND",
                             "category_id": shop}))["transaction"]
    r = (await _create(api, {"kind": "refund", "amount": "200000", "currency": "VND",
                             "category_id": shop, "refund_of_id": e["id"]}))["transaction"]
    assert r["kind"] == "refund" and r["title"] == "Возврат · Покупки"
    page = (await api.get("/transactions")).json()
    assert Decimal(page["totals"]["expense_vnd"]) == Decimal("300000")
    assert Decimal(page["totals"]["income_vnd"]) == 0
    bank = await acc_id(session, user.id, "Карта")
    assert await _postings(session, r["id"]) == [(bank, "VND", Decimal("200000"))]
    # refund needs an expense category
    salary = await cat_id(session, user.id, "Зарплата", "income")
    bad = await api.post("/transactions", headers=idem(), json={
        "kind": "refund", "amount": "1", "currency": "VND", "category_id": salary})
    assert bad.status_code == 422


async def test_income_and_kind_category_mismatch(api, session, user):
    clients = await cat_id(session, user.id, "Клиенты", "income")
    food = await cat_id(session, user.id, "Еда")
    tx = (await _create(api, {"kind": "income", "amount": "300", "currency": "USDT",
                              "category_id": clients}))["transaction"]
    assert tx["status"] == "needs_account" and tx["is_business"] is True
    page = (await api.get("/transactions")).json()
    assert Decimal(page["totals"]["income_usd"]) == Decimal("300")
    r = await api.post("/transactions", headers=idem(), json={
        "kind": "income", "amount": "1", "currency": "VND", "category_id": food})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


async def test_own_handle_is_not_an_expense(api, session, user):
    r = await api.post("/my-accounts", json={"handle": "@myshop"})
    assert r.status_code == 201, r.text
    ma = r.json()
    assert ma["handle"] == "myshop"
    accs = (await api.get("/accounts")).json()
    wallet = next(a for a in accs if a["id"] == ma["account_id"])
    assert wallet["name"] == "@myshop" and wallet["is_multi_asset"] is True
    assert (await api.post("/my-accounts", json={"handle": "myshop"})).status_code == 409
    soft = await cat_id(session, user.id, "Софт и AI")
    bad = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "3", "currency": "TON", "category_id": soft,
        "counterparty": "@myshop"})
    assert bad.status_code == 422
    kb = await acc_id(session, user.id, "Кошелёк")
    t = await api.post("/transfers", headers=idem(), json={
        "from_account_id": kb, "to_account_id": ma["account_id"], "amount": "3",
        "currency": "TON"})
    assert t.status_code == 201, t.text
    page = (await api.get("/transactions")).json()
    assert page["totals"]["expense_usd"] == "0" and page["totals"]["income_usd"] == "0"
    assert t.json()["transaction"]["title"] == "Перевод Кошелёк → @myshop"


async def test_version_conflict_and_validation(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    tx = (await _create(api, {"kind": "expense", "amount": "200000", "currency": "VND",
                              "category_id": food}))["transaction"]
    ok = await api.patch(f"/transactions/{tx['id']}", json={"version": 1, "amount": "250000"})
    assert ok.status_code == 200 and ok.json()["transaction"]["amount"] == "250000"
    stale = await api.patch(f"/transactions/{tx['id']}", json={"version": 1, "amount": "1"})
    assert stale.status_code == 409
    err = stale.json()["error"]
    assert err["code"] == "version_conflict" and err["details"]["current_version"] == 2
    # floats are rejected, money is a string
    f = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": 1.5, "currency": "VND", "category_id": food})
    assert f.status_code == 422
    future = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "1", "currency": "VND", "category_id": food,
        "occurred_on": "2099-01-01"})
    assert future.status_code == 422
    neg = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "-5", "currency": "VND", "category_id": food})
    assert neg.status_code == 422


async def test_soft_delete_restore_and_history(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    tx = (await _create(api, {"kind": "expense", "amount": "200000", "currency": "VND",
                              "category_id": food}))["transaction"]
    await api.patch(f"/transactions/{tx['id']}", json={"version": 1, "amount": "250000"})
    d = await api.delete(f"/transactions/{tx['id']}", params={"version": 2})
    assert d.status_code == 200, d.text
    assert await _postings(session, tx["id"]) == []
    assert (await api.get(f"/transactions/{tx['id']}")).status_code == 404
    assert (await api.get("/transactions")).json()["total"] == 0
    again = await api.delete(f"/transactions/{tx['id']}")
    assert again.status_code == 404
    rs = await api.post(f"/transactions/{tx['id']}/restore")
    assert rs.status_code == 200 and rs.json()["transaction"]["amount"] == "250000"
    assert len(await _postings(session, tx["id"])) == 1
    hist = (await api.get(f"/transactions/{tx['id']}/history")).json()
    assert [h["operation"] for h in hist] == ["create", "update", "delete", "restore"]
    assert hist[1]["before"]["amount"] == "200000" and hist[1]["after"]["amount"] == "250000"
    assert hist[0]["action_id"] and hist[0]["source"] == "web"
    audit = (await api.get("/audit", params={"entity_type": "transaction",
                                             "entity_id": str(tx["id"])})).json()
    assert len(audit) == 4


async def test_date_and_timezone(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    tx = (await _create(api, {"kind": "expense", "amount": "70000", "currency": "VND",
                              "category_id": food, "occurred_on": "2026-10-20"}))["transaction"]
    assert tx["occurred_on"] == "2026-10-20"
    # no time given → 12:00 local (Asia/Ho_Chi_Minh), shown in the user's timezone
    assert tx["occurred_at"] == "2026-10-20T12:00:00+07:00"
    got = (await api.get(f"/transactions/{tx['id']}")).json()
    assert got["occurred_at"] == "2026-10-20T12:00:00+07:00"
    r = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "1", "currency": "VND", "category_id": food,
        "occurred_at": "2026-10-20T23:30:00+00:00"})
    assert r.json()["transaction"]["occurred_on"] == "2026-10-21"


async def test_no_rate_stores_nulls_and_cross_currency_needs_rate(api, session, user,
                                                                  fake_rates):
    fake_rates.clear()
    food = await cat_id(session, user.id, "Еда")
    tx = (await _create(api, {"kind": "expense", "amount": "150000", "currency": "VND",
                              "category_id": food}))["transaction"]
    assert tx["amount_vnd"] == "150000" and tx["amount_usd"] is None
    t2 = (await _create(api, {"kind": "expense", "amount": "5", "currency": "TON",
                              "category_id": food, "account_none": True}))["transaction"]
    assert t2["amount_usd"] is None and t2["amount_vnd"] is None and t2["rate_source"] is None
    bank = await acc_id(session, user.id, "Карта")
    r = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "5", "currency": "USD", "category_id": food,
        "account_id": bank})
    assert r.status_code == 503 and r.json()["error"]["code"] == "rates_unavailable"
    r = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "5", "currency": "USD", "category_id": food,
        "account_id": bank, "account_amount": "131000"})
    assert r.status_code == 201 and r.json()["transaction"]["is_estimated"] is False
