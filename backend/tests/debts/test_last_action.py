"""``DebtDetailOut.last_action_id`` — «Отменить последнюю» in the card of a person
undoes the newest action still in effect on the latest operation (DEBTS.md §15)."""

from __future__ import annotations

from decimal import Decimal

from tests.debts.conftest import idem, op, open_bank, undo

D = Decimal


async def _last(api, did: int) -> str | None:  # type: ignore[no-untyped-def]
    r = await api.get(f"/debts/{did}")
    assert r.status_code == 200, r.text
    return r.json()["last_action_id"]


async def test_last_action_follows_edits_and_undos(api, session, user):
    await open_bank(api, session, user.id)
    lend = await op(api, op="lend", person_name="Лёня", amount=500000)
    did = lend["debt"]["id"]
    assert await _last(api, did) == lend["action_id"]
    rep = await op(api, op="repay_in", debt_id=did, amount=100000)
    assert await _last(api, did) == rep["action_id"]
    # the repayment is edited → the edit is the last action in effect, not the 409 creation
    tx = (await api.get(f"/transactions/{rep['transaction']['id']}")).json()
    r = await api.patch(f"/transactions/{tx['id']}", headers=idem(),
                        json={"version": tx["version"], "amount": "150000"})
    assert r.status_code == 200, r.text
    edit = r.json()["action_id"]
    assert await _last(api, did) == edit
    await undo(api, edit)
    assert await _last(api, did) == rep["action_id"]
    # undo of the repayment itself → the loan is the latest operation again
    await undo(api, rep["action_id"])
    assert await _last(api, did) == lend["action_id"]
    bal = (await api.get(f"/debts/{did}")).json()["debt"]["balances"]
    assert [D(b["amount"]) for b in bal] == [D(500000)]


async def test_last_action_none_without_operations(api, session, user):
    await open_bank(api, session, user.id)
    lend = await op(api, op="lend", person_name="Сеня", amount=1000)
    did = lend["debt"]["id"]
    await undo(api, lend["action_id"])
    assert await _last(api, did) is None
