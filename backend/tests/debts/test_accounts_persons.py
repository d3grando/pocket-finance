"""Debt service accounts are hidden and write-protected (docs/DEBTS.md §2); persons: lookup,
ambiguity, rename / handle / aliases / archive / delete / snooze; LLM context without amounts;
statistics, reports, search and «остатки» not changed by debts (§12)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.models import Account, Debt
from app.services.debts import service as debts
from app.services.ledger.wipe import wipe_financial_data
from app.services.stats.balances import asset_balances
from tests.debts.conftest import acc_id, cat_id, debt_bal, idem, op, open_bank

D = Decimal
DEBT_MSG = "Это счёт долга — используйте «Долги»"


async def test_debt_accounts_hidden_from_lists_and_totals(api, session, user):
    await open_bank(api, session, user.id)
    before = (await api.get("/accounts/balances")).json()
    plain = (await api.get("/accounts")).json()
    r = await op(api, op="lend", person_name="Коля", amount=500000)
    debt_acc = r["debt"]["account_id"]
    accs = (await api.get("/accounts")).json()
    assert debt_acc not in {a["id"] for a in accs} and len(accs) == len(plain)
    bal = (await api.get("/accounts/balances")).json()
    assert debt_acc not in {a["id"] for a in bal["accounts"]}
    assert D(bal["total_vnd"]) == D(before["total_vnd"]) - D(500000)  # money left Карта
    with_debts = (await api.get("/accounts", params={"include_debts": "true"})).json()
    acc = next(a for a in with_debts if a["id"] == debt_acc)
    assert acc["kind"] == "debt" and acc["name"] == "Долг: Коля" and acc["icon"] == "HandCoins"
    assert acc["is_multi_asset"] is True
    assert acc["assets"][0]["balance"] == "500000" and acc["assets"][0]["opening_balance"] == "0"
    bal2 = (await api.get("/accounts/balances", params={"include_debts": "true"})).json()
    assert debt_acc in {a["id"] for a in bal2["accounts"]}
    assert bal2["total_vnd"] == bal["total_vnd"] and bal2["unknown_count"] == bal["unknown_count"]
    # «остатки» answer, account resolution and default account never see it
    rows = await asset_balances(session, user.id)
    assert debt_acc not in {x.account_id for x in rows}
    from app.services.accounts import service as accounts

    assert await accounts.resolve_account(session, user.id, "Долг: Коля") is None
    assert (await accounts.default_account_for(session, user.id, "VND")).name == "Карта"
    hits = (await api.get("/search", params={"q": "Долг"})).json()["hits"]
    assert all(h["type"] != "account" for h in hits)
    # the operation itself is found by the person's name
    hits = (await api.get("/search", params={"q": "Коля"})).json()["hits"]
    assert any(h["type"] == "transaction" for h in hits)


async def test_manual_writes_to_debt_accounts_rejected(api, session, user):
    bank = await open_bank(api, session, user.id)
    r = await op(api, op="lend", person_name="Коля", amount=500000)
    da = r["debt"]["account_id"]

    async def bad(resp):  # type: ignore[no-untyped-def]
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["message"] == DEBT_MSG

    await bad(await api.post("/transfers", headers=idem(), json={
        "from_account_id": bank, "to_account_id": da, "amount": "1", "currency": "VND"}))
    await bad(await api.post("/transfers", headers=idem(), json={
        "from_account_id": da, "to_account_id": bank, "amount": "1", "currency": "VND"}))
    await bad(await api.post("/exchanges", headers=idem(), json={
        "from_account_id": da, "to_account_id": bank, "amount": "1", "currency": "USDT",
        "counter_amount": "26000", "counter_currency": "VND"}))
    await bad(await api.post("/adjustments", headers=idem(), json={
        "account_id": da, "asset": "VND", "actual_balance": "0"}))
    food = await cat_id(session, user.id, "Еда")
    await bad(await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "1000", "currency": "VND", "category_id": food,
        "account_id": da}))
    await bad(await api.put(f"/accounts/{da}/opening", json={"asset": "VND", "amount": "5"}))
    acc = (await api.get(f"/accounts/{da}")).json()
    await bad(await api.patch(f"/accounts/{da}", json={"version": acc["version"], "name": "x"}))
    await bad(await api.delete(f"/accounts/{da}"))
    await bad(await api.post("/accounts/onboarding", json={"items": [
        {"account_id": da, "asset": "VND", "amount": "1"}]}))
    await bad(await api.post("/my-accounts", json={"handle": "@me", "account_id": da}))
    # an ordinary expense cannot be moved onto it either
    ex = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "1000", "currency": "VND", "category_id": food})
    tx = ex.json()["transaction"]
    await bad(await api.patch(f"/transactions/{tx['id']}", headers=idem(), json={
        "version": tx["version"], "account_id": da}))
    await bad(await api.post(f"/transactions/{tx['id']}/account", headers=idem(), json={
        "account_id": da, "version": tx["version"]}))
    assert await debt_bal(api, r["debt"]["id"]) == {"VND": D(500000)}


async def test_stats_and_reports_unchanged_by_debts(api, session, user):
    await open_bank(api, session, user.id)
    food = await cat_id(session, user.id, "Еда")
    salary = await cat_id(session, user.id, "Зарплата", "income")
    for kind, amount, cid in (("expense", "150000", food), ("income", "3000000", salary)):
        r = await api.post("/transactions", headers=idem(), json={
            "kind": kind, "amount": amount, "currency": "VND", "category_id": cid})
        assert r.status_code == 201, r.text

    async def snap():  # type: ignore[no-untyped-def]
        s = (await api.get("/stats/summary", params={"currency": "VND"})).json()
        out = {k: s[k]["value"] for k in ("spent_today", "spent_month", "income_month",
                                          "net_month")}
        for kind in ("expense", "income"):
            c = (await api.get("/stats/by-category", params={
                "kind": kind, "currency": "VND"})).json()
            out[kind] = [(i["name"], i["amount"]) for i in c["items"]]
        out["series"] = (await api.get("/stats/series", params={
            "currency": "VND"})).json()["points"]
        rep = (await api.get("/reports/monthly")).json()
        out["report"] = rep["text_plain"]
        return out

    before = await snap()
    kb = await acc_id(session, user.id, "Кошелёк")
    await op(api, op="lend", person_name="Коля", amount=500000)
    await op(api, op="repay_in", person_name="Коля", amount=200000)
    await op(api, op="borrow", person_name="Петя", amount=1000000)
    await op(api, op="repay_out", person_name="Петя", amount=300000)
    await op(api, op="lend", person_name="Вася", amount=50, currency="USDT", account_id=kb)
    await op(api, op="borrow", person_name="Лена", amount=2, currency="TON", account_id=kb)
    after = await snap()
    rep = after.pop("report")
    before_rep = before.pop("report")
    assert after == before
    # the monthly report gets one display-only line
    assert "Долги: мне должны ≈" in rep.replace(" ", " ")
    assert rep.replace("🤝 ", "").count("\n") >= before_rep.count("\n")
    line = next(x for x in rep.splitlines() if x.startswith("🤝"))
    assert "я должен ≈" in line.replace(" ", " ")


async def test_find_debt_lookup_and_ambiguity(api, session, user, principal):
    await open_bank(api, session, user.id)
    a = (await op(api, op="lend", person_name="Коля Петров", amount=100000))["debt"]
    b = (await op(api, op="lend", person_name="Коля Иванов", amount=100000))["debt"]
    assert a["id"] != b["id"]
    # «Ивану Петрову» etc. resolve by the declension-insensitive key
    hit = await debts.find_debt(session, user.id, "у Коли Петрова")
    assert isinstance(hit, Debt) and hit.id == a["id"]
    for d in (a, b):
        cur = (await api.get(f"/debts/{d['id']}")).json()["debt"]
        r = await api.patch(f"/debts/{d['id']}", json={"version": cur["version"],
                                                       "aliases": ["коля"]})
        assert r.status_code == 200, r.text
    hits = await debts.find_debt(session, user.id, "Коля")
    assert isinstance(hits, list) and {d.id for d in hits} == {a["id"], b["id"]}
    r = await op(api, 422, op="repay_in", person_name="Коля", amount=1)
    assert r["error"]["code"] == "ambiguous_target"
    assert {c["name"] for c in r["error"]["details"]["candidates"]} == {
        "Коля Петров", "Коля Иванов"}
    r = await op(api, 422, op="lend", person_name="Коля", amount=1)
    assert r["error"]["code"] == "ambiguous_target"
    assert await debts.find_debt(session, user.id, "Вася") is None
    assert await debts.find_debt(session, user.id, "") is None


async def test_patch_rename_handle_archive_delete_snooze(api, session, user, principal):
    await open_bank(api, session, user.id)
    r = await op(api, op="lend", person_name="Коле", amount=100000)  # dative guess «Коле»
    d = r["debt"]
    r = await api.patch(f"/debts/{d['id']}", json={"version": d["version"], "name": "Николай",
                                                   "handle": "@kolya", "note": "сосед"})
    assert r.status_code == 200, r.text
    d2 = r.json()
    assert d2["name"] == "Николай" and d2["handle"] == "kolya" and d2["note"] == "сосед"
    assert "коле" in d2["aliases"] and d2["version"] == d["version"] + 1
    acc = await session.get(Account, d2["account_id"])
    assert acc.name == "Долг: Николай"
    tx = (await api.get("/transactions")).json()["items"][0]
    assert tx["title"] == "Дал в долг: Николай" and tx["debt"]["name"] == "Николай"
    # stale version → 409; old name still finds the person
    r = await api.patch(f"/debts/{d['id']}", json={"version": d["version"], "note": "x"})
    assert r.status_code == 409
    hit = await debts.find_debt(session, user.id, "@kolya")
    assert isinstance(hit, Debt) and hit.id == d["id"]
    # name / handle clash → 409
    other = (await op(api, op="lend", person_name="Петя", amount=1000))["debt"]
    r = await api.patch(f"/debts/{other['id']}", json={"version": other["version"],
                                                       "name": "николай"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_exists"
    r = await api.patch(f"/debts/{other['id']}", json={"version": other["version"],
                                                       "handle": "KOLYA"})
    assert r.status_code == 409
    # archive only at zero balance
    r = await api.patch(f"/debts/{d['id']}", json={"version": d2["version"], "is_archived": True})
    assert r.status_code == 422 and "Долг не закрыт" in r.json()["error"]["message"]
    await op(api, op="repay_in", debt_id=d["id"], amount=None)
    cur = (await api.get(f"/debts/{d['id']}")).json()["debt"]
    r = await api.patch(f"/debts/{d['id']}", json={"version": cur["version"], "is_archived": True})
    assert r.status_code == 200 and r.json()["is_archived"] is True
    lst = (await api.get("/debts", params={"include_settled": "true"})).json()
    assert d["id"] not in {x["id"] for x in lst["settled"]}
    # a new operation un-archives the person
    r = await op(api, op="lend", person_name="Николай", amount=5000)
    assert r["debt"]["id"] == d["id"] and r["debt"]["is_archived"] is False
    # snooze
    r = await api.post(f"/debts/{d['id']}/snooze", json={"days": 7})
    assert r.status_code == 200, r.text
    assert r.json()["remind_snoozed_until"] is not None
    assert (await api.post(f"/debts/{d['id']}/snooze")).status_code == 200  # default 7
    assert (await api.post("/debts/999999/snooze", json={"days": 7})).status_code == 404
    # delete: with operations → 422; a person without any → deleted with the account
    r = await api.delete(f"/debts/{d['id']}")
    assert r.status_code == 422
    empty = await debts.get_or_create_debt(session, principal, "Гена")
    acc_id_ = empty.account_id
    r = await api.delete(f"/debts/{empty.id}")
    assert r.status_code == 200, r.text
    assert await session.scalar(select(func.count()).where(Account.id == acc_id_)) == 0
    assert (await api.get(f"/debts/{empty.id}")).status_code == 404


async def test_context_debts_names_and_currencies_only(api, session, user):
    await open_bank(api, session, user.id)
    kb = await acc_id(session, user.id, "Кошелёк")
    await op(api, op="lend", person_name="Коля", amount=300000)
    await op(api, op="repay_in", person_name="у Коли", amount=100000)
    await op(api, op="borrow", person_name="Коля", amount=5, currency="USDT", account_id=kb)
    await op(api, op="borrow", person_name="Петя", amount=1000)
    await op(api, op="repay_out", person_name="Петя", amount=None)
    ctx = await debts.context_debts(session, user.id)
    by = {c.name: c for c in ctx}
    kolya = by["Коля"]
    assert kolya.name_key == "кол" and "у коли" in kolya.aliases
    assert [(o.currency, o.direction) for o in kolya.open] == [
        ("VND", "owed_to_me"), ("USDT", "i_owe")]
    assert by["Петя"].open == []
    dumped = str([c.model_dump() for c in ctx])
    assert "300000" not in dumped and "200000" not in dumped and "Decimal" not in dumped


async def test_wipe_removes_debts(api, session, user):
    await open_bank(api, session, user.id)
    await op(api, op="lend", person_name="Коля", amount=300000)
    counts = await wipe_financial_data(session, user.id)
    assert counts["debts"] == 1
    assert await session.scalar(select(func.count()).where(Debt.user_id == user.id)) == 0
    assert (await api.get("/debts")).json()["owed_to_me"] == []


async def test_settings_debt_reminders_defaults_and_patch(api, session, user):
    s = (await api.get("/settings")).json()
    assert s["debt_reminders_enabled"] is False and s["debt_reminder_days"] == 30
    r = await api.patch("/settings", json={"version": s["version"],
                                           "debt_reminders_enabled": True,
                                           "debt_reminder_days": 14})
    assert r.status_code == 200, r.text
    assert r.json()["debt_reminders_enabled"] is True and r.json()["debt_reminder_days"] == 14
    r = await api.patch("/settings", json={"version": r.json()["version"],
                                           "debt_reminder_days": 0})
    assert r.status_code == 422


async def test_full_export_carries_debts_and_csv_unchanged(api, session, user):
    await open_bank(api, session, user.id)
    r = await op(api, op="lend", person_name="Коля", amount=300000)
    full = (await api.get("/export/full")).json()
    acc = next(a for a in full["accounts"] if a["id"] == r["debt"]["account_id"])
    assert acc["kind"] == "debt" and acc["debt"]["name"] == "Коля"
    assert acc["debt"]["id"] == r["debt"]["id"]
    assert all("debt" not in a for a in full["accounts"] if a["kind"] != "debt")
    tx = next(t for t in full["transactions"] if t["id"] == r["transaction"]["id"])
    assert tx["debt_op"] == "lend"
    assert [d["id"] for d in full["debts"]] == [r["debt"]["id"]]  # top-level debts[] (§12)
    assert full["debts"][0]["account_id"] == r["debt"]["account_id"]
    csv = await api.get("/export/transactions", params={"format": "csv"})
    assert csv.status_code == 200 and "Долг: Коля" in csv.text


async def test_merchant_default_account_rejects_debt_account(api, session, user):
    await open_bank(api, session, user.id)
    r = await op(api, op="lend", person_name="Коля", amount=300000)
    debt_acc = r["debt"]["account_id"]
    m = await api.post("/merchants", json={"name": "vast.ai", "default_account_id": debt_acc})
    assert m.status_code == 422, m.text
    assert m.json()["error"]["details"]["field"] == "default_account_id"
    ok = await api.post("/merchants", json={"name": "vast.ai"})
    assert ok.status_code in (200, 201), ok.text
    p = await api.patch(f"/merchants/{ok.json()['id']}", json={"default_account_id": debt_acc})
    assert p.status_code == 422, p.text
    assert p.json()["error"]["details"]["field"] == "default_account_id"
