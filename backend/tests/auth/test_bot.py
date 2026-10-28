"""Bot service token + X-Telegram-User-Id; allowed TG ids; first-contact binding."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.config import get_settings
from app.core.errors import AppError
from app.services.auth.bot import resolve_bot_service
from app.services.auth.settings import load_settings

BOT = "test-bot-token-0123456789abcdef"  # tests/conftest.py BOT_SERVICE_TOKEN
OWNER_TG = 123456789


def bot_headers(tg: int | str | None = OWNER_TG, token: str = BOT) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if tg is not None:
        h["X-Telegram-User-Id"] = str(tg)
    return h


async def test_bot_principal(raw_client, owner):
    r = await raw_client.get("/auth/me", headers=bot_headers())
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["auth"] == "bot" and me["source"] == "telegram" and me["user_id"] == owner.id


async def test_foreign_telegram_id_forbidden(raw_client, owner):
    r = await raw_client.get("/auth/me", headers=bot_headers(12345))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "telegram_forbidden"


async def test_missing_or_bad_telegram_header(raw_client, owner):
    r = await raw_client.get("/auth/me", headers=bot_headers(None))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "telegram_forbidden"
    r = await raw_client.get("/auth/me", headers=bot_headers("12ab"))
    assert r.status_code == 403


async def test_wrong_bot_token(raw_client, owner):
    r = await raw_client.get("/auth/me", headers=bot_headers(token="nope-" + BOT))
    assert r.status_code == 401


async def test_bot_disabled_without_token(raw_client, owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "bot_service_token", "")
    r = await raw_client.get("/auth/me", headers=bot_headers(token=""))
    assert r.status_code == 401


async def test_first_contact_binds_owner(raw_client, owner, session):
    st = await load_settings(session, owner.id)
    st.telegram_user_id = None
    await session.flush()
    r = await raw_client.get("/auth/me", headers=bot_headers())
    assert r.status_code == 200
    st = await load_settings(session, owner.id)
    assert st.telegram_user_id == OWNER_TG


async def test_allowed_but_owner_bound_to_other_id(raw_client, owner, session, monkeypatch):
    monkeypatch.setattr(get_settings(), "telegram_allowed_ids", [OWNER_TG, 777])
    r = await raw_client.get("/auth/me", headers=bot_headers(777))
    assert r.status_code == 403  # owner's slot already has 123456789


async def test_bot_mutation_without_csrf(raw_client, owner):
    v = (await raw_client.get("/settings", headers=bot_headers())).json()["version"]
    r = await raw_client.patch(
        "/settings", json={"version": v, "report_daily": True}, headers=bot_headers()
    )
    assert r.status_code == 200, r.text
    assert r.json()["report_daily"] is True


async def test_bot_is_not_human(raw_client, owner):
    assert (await raw_client.get("/devices", headers=bot_headers())).status_code == 403
    assert (await raw_client.get("/users/me", headers=bot_headers())).status_code == 403


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/bot/outbox/claim",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "query_string": b"",
        }
    )


async def test_resolve_bot_service(session, owner):
    p = await resolve_bot_service(_request(bot_headers(None)), session)
    assert p.kind == "bot" and p.user_id == owner.id and p.telegram_user_id == OWNER_TG
    with pytest.raises(AppError) as e:
        await resolve_bot_service(_request(bot_headers(None, token="bad")), session)
    assert e.value.status == 401
    with pytest.raises(AppError):
        await resolve_bot_service(_request({}), session)
