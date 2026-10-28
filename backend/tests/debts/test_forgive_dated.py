"""A write-off is limited by the debt ON ITS DATE (docs/DEBTS.md §4): «займ 100k →
прощение 50k → новый займ 200k; правка старого прощения до 250k» must not eat the later loan.
Also covered: back-dated write-offs, moving one in time, later returns, deleting the loan under
it. Live sessionmaker: a rejected request rolls back for real."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx

from app.core.timeutil import local_today
from tests.debts.conftest import idem

D = Decimal


def _day(n: int) -> str:
    return (local_today("Asia/Ho_Chi_Minh") - timedelta(days=n)).isoformat()


async def _op(api: httpx.AsyncClient, expect: int = 201, **body: Any) -> dict[str, Any]:
    if body.get("amount") is not None:
        body["amount"] = str(body["amount"])
    body.setdefault("currency", "VND")
    body.setdefault("account_none", body["op"] != "forgive")
    r = await api.post("/debts/operations", json=body, headers=idem())
    assert r.status_code == expect, r.text
    return r.json()


async def _patch(api: httpx.AsyncClient, tx_id: int, expect: int = 200, **body: Any
                 ) -> dict[str, Any]:
    tx = (await api.get(f"/transactions/{tx_id}")).json()
    body = {"version": tx["version"],
            **{k: str(v) if k == "amount" else v for k, v in body.items()}}
    r = await api.patch(f"/transactions/{tx_id}", json=body, headers=idem())
    assert r.status_code == expect, r.text
    return r.json()


async def _bal(api: httpx.AsyncClient, debt_id: int) -> dict[str, D]:
    r = await api.get(f"/debts/{debt_id}")
    assert r.status_code == 200, r.text
    return {b["currency"]: D(b["amount"]) for b in r.json()["debt"]["balances"]}


def _err(r: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return r["error"]["message"].replace(" ", " "), r["error"].get("details") or {}


async def test_editing_an_old_write_off_cannot_eat_a_later_loan(live_api, live_user):
    """The report's scenario, back-dated and same-day."""
    api = live_api
    for name, dates in (("Коля", (_day(3), _day(2), _day(1))), ("Петя", (None, None, None))):
        extra = [{"occurred_on": d} if d else {} for d in dates]
        lend = await _op(api, op="lend", person_name=name, amount=100000, **extra[0])
        did = lend["debt"]["id"]
        fg = await _op(api, op="forgive", debt_id=did, amount=50000, **extra[1])
        loan2 = await _op(api, op="lend", debt_id=did, amount=200000, **extra[2])
        assert await _bal(api, did) == {"VND": D(250000)}
        msg, det = _err(await _patch(api, fg["transaction"]["id"], 422, amount=250000))
        assert msg == "Списать можно не больше остатка долга" and det["max"] == "100000"
        assert await _bal(api, did) == {"VND": D(250000)}  # the 200k loan is intact
        assert (await api.get(f"/transactions/{loan2['transaction']['id']}")).status_code == 200
        await _patch(api, fg["transaction"]["id"], amount=100000)  # all of what was owed then
        assert await _bal(api, did) == {"VND": D(200000)}
        await _patch(api, fg["transaction"]["id"], amount=40000)  # smaller: always fine
        assert await _bal(api, did) == {"VND": D(260000)}


async def test_write_off_cannot_cover_a_later_return(live_api, live_user):
    """lend 100k → forgive 50k → he returns 50k (balance 0) → lend 200k: growing the write-off
    to 60k would turn his return into an overpay — limited to 50k."""
    api = live_api
    lend = await _op(api, op="lend", person_name="Вася", amount=100000, occurred_on=_day(4))
    did = lend["debt"]["id"]
    fg = await _op(api, op="forgive", debt_id=did, amount=50000, occurred_on=_day(3))
    await _op(api, op="repay_in", debt_id=did, amount=50000, occurred_on=_day(2))
    await _op(api, op="lend", debt_id=did, amount=200000, occurred_on=_day(1))
    msg, det = _err(await _patch(api, fg["transaction"]["id"], 422, amount=60000))
    assert det["max"] == "50000"
    assert await _bal(api, did) == {"VND": D(200000)}


async def test_moving_a_write_off_before_the_loan_is_422(live_api, live_user):
    api = live_api
    lend = await _op(api, op="lend", person_name="Лена", amount=100000, occurred_on=_day(3))
    did = lend["debt"]["id"]
    fg = await _op(api, op="forgive", debt_id=did, amount=50000, occurred_on=_day(2))
    msg, det = _err(await _patch(api, fg["transaction"]["id"], 422, occurred_on=_day(5)))
    assert det["max"] == "0"
    assert (await api.get(f"/transactions/{fg['transaction']['id']}")).json()[
        "occurred_on"] == _day(2)
    await _patch(api, fg["transaction"]["id"], occurred_on=_day(1))  # later: still covered


async def test_back_dated_write_off_is_limited_by_its_date(live_api, live_user):
    api = live_api
    lend = await _op(api, op="lend", person_name="Маша", amount=100000, occurred_on=_day(3))
    did = lend["debt"]["id"]
    await _op(api, op="lend", debt_id=did, amount=200000, occurred_on=_day(1))
    r = await _op(api, 422, op="forgive", debt_id=did, amount=250000, occurred_on=_day(2))
    msg, det = _err(r)
    assert msg.startswith("Больше остатка на эту дату") and det["max"] == "100000"
    # «whole debt» on that date = what was owed then
    fg = await _op(api, op="forgive", debt_id=did, amount=None, occurred_on=_day(2))
    assert D(fg["transaction"]["amount"]) == D(100000)
    assert await _bal(api, did) == {"VND": D(200000)}
    # before the first loan there was nothing to write off
    r = await _op(api, 422, op="forgive", debt_id=did, amount=1000, occurred_on=_day(6))
    assert _err(r)[0].startswith("На эту дату долга не было")


async def test_changes_before_a_write_off_cannot_uncover_it(live_api, live_user):
    """Deleting the loan a write-off covered, or back-dating a return before it, is blocked
    even when today's balance stays positive because of a later loan."""
    api = live_api
    lend = await _op(api, op="lend", person_name="Гоша", amount=100000, occurred_on=_day(3))
    did = lend["debt"]["id"]
    await _op(api, op="forgive", debt_id=did, amount=100000, occurred_on=_day(2))
    await _op(api, op="lend", debt_id=did, amount=200000, occurred_on=_day(1))
    r = await api.delete(f"/transactions/{lend['transaction']['id']}", headers=idem())
    assert r.status_code == 422, r.text
    msg, det = _err(r.json())
    assert msg.startswith("Сначала отмените списание долга: Гоша")
    assert det["reason"] == "forgive_exceeds_debt"
    assert await _bal(api, did) == {"VND": D(200000)}

    lend = await _op(api, op="lend", person_name="Зина", amount=300000, occurred_on=_day(3))
    did = lend["debt"]["id"]
    await _op(api, op="forgive", debt_id=did, amount=200000, occurred_on=_day(2))
    # a return dated before the write-off: what was written off was partly repaid
    r = await _op(api, 422, op="repay_in", debt_id=did, amount=150000, occurred_on=_day(3))
    assert _err(r)[0].startswith("Сначала отмените списание долга: Зина")
    assert await _bal(api, did) == {"VND": D(100000)}
    # the same return AFTER the write-off is independent of it (an overpay)
    rep = await _op(api, op="repay_in", debt_id=did, amount=150000, occurred_on=_day(1))
    assert rep["excess"] == "50000"
