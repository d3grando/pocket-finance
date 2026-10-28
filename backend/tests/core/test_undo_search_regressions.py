"""Regressions: undo of a create after its edits were undone, money formatting,
NUL bytes in /search and in strict request bodies."""

from __future__ import annotations

from app.core.money import format_money
from tests.core.conftest import cat_id, idem

NBSP = " "


async def _post(api, body):  # type: ignore[no-untyped-def]
    r = await api.post("/transactions", json=body, headers=idem())
    assert r.status_code == 201, r.text
    return r.json()


async def _undo(api, action_id):  # type: ignore[no-untyped-def]
    r = await api.post(f"/actions/{action_id}/undo", headers=idem())
    assert r.status_code == 200, r.text
    return r.json()


async def test_undo_create_after_its_edits_were_undone(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    res = await _post(api, {"kind": "expense", "amount": "85000", "currency": "VND",
                            "category_id": food})
    tx = res["transaction"]
    r = await api.patch(f"/transactions/{tx['id']}", headers=idem(),
                        json={"version": tx["version"], "amount": "90000"})
    assert r.status_code == 200, r.text
    await _undo(api, r.json()["action_id"])  # edit undone → back to the created state
    await _undo(api, res["action_id"])  # the create can be undone now (was 409 undo_conflict)
    assert (await api.get(f"/transactions/{tx['id']}")).status_code == 404


async def test_undo_create_after_live_edit_still_conflicts(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    res = await _post(api, {"kind": "expense", "amount": "85000", "currency": "VND",
                            "category_id": food})
    tx = res["transaction"]
    r = await api.patch(f"/transactions/{tx['id']}", headers=idem(),
                        json={"version": tx["version"], "amount": "90000"})
    assert r.status_code == 200, r.text
    r = await api.post(f"/actions/{res['action_id']}/undo", headers=idem())
    assert r.status_code == 409 and r.json()["error"]["code"] == "undo_conflict"


def test_format_money_rounds_before_sign_and_decimals() -> None:
    assert format_money("-0.004", "USD") == "$0.00"
    assert format_money("4990.004", "RUB") == f"4{NBSP}990{NBSP}₽"
    # stablecoins keep up to 6 decimals since 28.10 (the card echoes «20.2552 USDT»)
    assert format_money("12.004", "USDT") == f"12.004{NBSP}USDT"
    assert format_money("12.0000004", "USDT") == f"12{NBSP}USDT"
    assert format_money("-0.4", "VND") == f"0{NBSP}₫"


async def test_nul_in_search_and_strict_bodies_is_422(api, session, user):
    r = await api.get("/search", params={"q": "ко\x00фе"})
    assert r.status_code == 422, r.text
    r = await api.post("/assistant/messages", headers=idem(), json={"text": "кофе\x00 85к"})
    assert r.status_code == 422, r.text
