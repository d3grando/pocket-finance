"""Account aliases / defaults / own handles; filter validation; estimated-charge lookup."""

from __future__ import annotations

from app.schemas.accounts import MyAccountCreate
from app.services.accounts import service as accounts
from app.services.ledger import query
from tests.core.conftest import acc_id, cat_id, idem


async def test_resolve_account_aliases(session, user, principal):
    async def name(ref: str) -> str | None:
        a = await accounts.resolve_account(session, user.id, ref)
        return a.name if a else None

    assert await name("налом") == "Наличные"
    assert await name("НАЛ") == "Наличные"
    assert await name("Кошелёк") == "Кошелёк"
    assert await name("Крипто") == "Кошелёк"
    assert await name("карта руб") == "Карта RUB"
    assert await name("рублевая карта") == "Карта RUB"   # ё = е
    assert await name("карта") == "Карта"                # exact alias beats prefix
    assert await name("бир") == "Биржа"                 # unique prefix
    assert await name("т") is None                      # ambiguous prefix
    assert await name("неизвестный") is None
    await accounts.add_my_account(session, principal, MyAccountCreate(handle="@MyShop"))
    assert await name("@myshop") == "@MyShop"
    ma = await accounts.my_account_by_handle(session, user.id, "@MYSHOP")
    assert ma is not None and ma.handle == "MyShop"
    assert (await accounts.default_account_for(session, user.id, "VND")).name == "Карта"
    assert (await accounts.default_account_for(session, user.id, "RUB")).name == "Карта RUB"
    assert await accounts.default_account_for(session, user.id, "USDT") is None


async def test_bad_filter_is_422(api):
    r = await api.get("/transactions", params={"kind": "nonsense"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = await api.get("/transactions", params={"currency": "x"})
    assert r.status_code == 422


async def test_last_estimated_charge(api, session, user):
    srv = await cat_id(session, user.id, "Серверы/инфра")
    bank = await acc_id(session, user.id, "Карта")
    m = (await api.post("/merchants", json={"name": "vast.ai", "aliases": ["васт"],
                                            "category_id": srv})).json()
    t = (await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "815", "currency": "USD", "category_id": srv,
        "merchant_id": m["id"], "account_id": bank})).json()["transaction"]
    found = await query.last_estimated(session, user.id, merchant_id=m["id"])
    assert found is not None and found.id == t["id"]
    await api.patch(f"/transactions/{t['id']}", json={"version": t["version"],
                                                      "account_amount": "21300000"})
    assert await query.last_estimated(session, user.id, merchant_id=m["id"]) is None
