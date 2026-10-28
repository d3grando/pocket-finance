"""debts service fixtures: ledger's deterministic rates / idempotency pass-through / live users,
plus small helpers for debt operations over the REST API."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from tests.core.conftest import (  # noqa: F401  (fixtures re-exported for tests/debts)
    acc_id,
    api,
    cat_id,
    fake_rates,
    idem,
    live_api,
    live_principal,
    live_session,
    live_user,
)


async def op(client: httpx.AsyncClient, expect: int = 201, **body: Any) -> dict[str, Any]:
    """POST /debts/operations; amounts as strings."""
    for k in ("amount", "account_amount"):
        if k in body and body[k] is not None:
            body[k] = str(body[k])
    body.setdefault("currency", "VND")
    r = await client.post("/debts/operations", json=body, headers=idem())
    assert r.status_code == expect, r.text
    return r.json()


async def balance(client: httpx.AsyncClient, account_id: int, asset: str) -> Decimal | None:
    r = await client.get(f"/accounts/{account_id}")
    assert r.status_code == 200, r.text
    for a in r.json()["assets"]:
        if a["asset"] == asset:
            return Decimal(a["balance"]) if a["balance"] is not None else None
    return None


async def debt_bal(client: httpx.AsyncClient, debt_id: int) -> dict[str, Decimal]:
    r = await client.get(f"/debts/{debt_id}")
    assert r.status_code == 200, r.text
    return {b["currency"]: Decimal(b["amount"]) for b in r.json()["debt"]["balances"]}


async def open_bank(client: httpx.AsyncClient, session: AsyncSession, user_id: int,
                    amount: str = "10000000") -> int:
    bank = await acc_id(session, user_id, "Карта")
    r = await client.put(f"/accounts/{bank}/opening", json={"asset": "VND", "amount": amount})
    assert r.status_code == 200, r.text
    return bank


async def undo(client: httpx.AsyncClient, action_id: str) -> dict[str, Any]:
    r = await client.post(f"/actions/{action_id}/undo", headers=idem())
    assert r.status_code == 200, r.text
    return r.json()
