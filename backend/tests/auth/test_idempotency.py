"""Idempotency-Key dependency (ARCH §5.2) on a tiny app with real DB storage."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import IdemDep, IdemOptDep
from app.api.deps_base import get_principal
from app.core.errors import AppError, ValidationFailed, install_error_handlers
from app.core.principal import Principal
from app.db import get_session
from app.models import IdempotencyKey


class Body(BaseModel):
    amount: str
    fail: bool = False


@pytest.fixture
async def idem_app(session: AsyncSession, user):
    app = FastAPI()
    install_error_handlers(app)
    calls: list[str] = []

    @app.post("/things", status_code=201)
    async def create(body: Body, idem: IdemDep):
        if idem.replay is not None:
            return idem.replay
        calls.append(body.amount)
        if body.fail:
            raise ValidationFailed("нельзя")
        if body.amount == "boom":
            raise RuntimeError("crash")
        return await idem.save(201, {"amount": body.amount, "n": len(calls)})

    @app.post("/opt")
    async def opt(body: Body, idem: IdemOptDep):
        if idem.replay is not None:
            return idem.replay
        calls.append(body.amount)
        return await idem.save(200, {"n": len(calls)})

    async def _session() -> AsyncIterator[AsyncSession]:
        yield session

    async def _principal() -> Principal:
        return Principal(user_id=user.id, kind="device", source="desktop")

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = _principal
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, calls


def k(key: str) -> dict[str, str]:
    return {"Idempotency-Key": key}


async def test_missing_key_428(idem_app):
    c, calls = idem_app
    r = await c.post("/things", json={"amount": "1"})
    assert r.status_code == 428
    assert r.json()["error"]["code"] == "idempotency_key_required"
    assert calls == []


async def test_replay_same_body(idem_app, session, user):
    c, calls = idem_app
    r1 = await c.post("/things", json={"amount": "150000"}, headers=k("a1"))
    assert r1.status_code == 201 and r1.json() == {"amount": "150000", "n": 1}
    r2 = await c.post("/things", json={"amount": "150000"}, headers=k("a1"))
    assert r2.status_code == 201 and r2.json() == r1.json()
    assert r2.headers["Idempotent-Replayed"] == "true"
    assert calls == ["150000"]
    row = (
        await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == "a1"))
    ).scalar_one()
    assert row.state == "done" and row.response_status == 201
    assert row.expires_at - row.created_at > timedelta(hours=47)


async def test_mismatch_409(idem_app):
    c, calls = idem_app
    await c.post("/things", json={"amount": "1"}, headers=k("a2"))
    r = await c.post("/things", json={"amount": "2"}, headers=k("a2"))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "idempotency_mismatch"
    assert calls == ["1"]


async def test_in_progress_409(idem_app, session, user):
    c, calls = idem_app
    session.add(
        IdempotencyKey(
            user_id=user.id,
            key="busy",
            method="POST",
            path="/things",
            request_hash=__import__("hashlib").sha256(b"x").digest(),
            state="in_progress",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await session.flush()
    # Same hash as the stored row is needed to reach the in_progress branch:
    from app.middleware import idempotency as mod

    class _Req:
        method = "POST"

        class url:
            path = "/things"
            query = ""

    row = (
        await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == "busy"))
    ).scalar_one()
    row.request_hash = mod._request_hash(_Req, b'{"amount":"5"}')
    await session.flush()
    r = await c.post(
        "/things",
        content=b'{"amount":"5"}',
        headers={**k("busy"), "content-type": "application/json"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "idempotency_in_progress"
    assert calls == []


async def test_failure_releases_key(idem_app, session):
    c, calls = idem_app
    r = await c.post("/things", json={"amount": "1", "fail": True}, headers=k("a3"))
    assert r.status_code == 422
    assert (
        await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == "a3"))
    ).scalar_one_or_none() is None
    r = await c.post("/things", json={"amount": "1", "fail": True}, headers=k("a3"))
    assert r.status_code == 422
    assert calls == ["1", "1"]  # retry executed again


async def test_crash_releases_key(idem_app, session):
    c, calls = idem_app
    r = await c.post("/things", json={"amount": "boom"}, headers=k("a4"))
    assert r.status_code == 500
    assert (
        await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == "a4"))
    ).scalar_one_or_none() is None


async def test_expired_key_is_reused(idem_app, session):
    c, calls = idem_app
    await c.post("/things", json={"amount": "1"}, headers=k("a5"))
    row = (
        await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == "a5"))
    ).scalar_one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    r = await c.post("/things", json={"amount": "2"}, headers=k("a5"))
    assert r.status_code == 201 and r.json()["n"] == 2


async def test_bad_key_400(idem_app):
    c, _ = idem_app
    r = await c.post("/things", json={"amount": "1"}, headers=k("has space"))
    assert r.status_code == 400
    r = await c.post("/things", json={"amount": "1"}, headers=k("x" * 129))
    assert r.status_code == 400


async def test_bot_style_keys(idem_app):
    c, calls = idem_app
    r = await c.post("/things", json={"amount": "1"}, headers=k("tg:123456789:1234"))
    assert r.status_code == 201
    r = await c.post("/things", json={"amount": "1"}, headers=k("tgcb:6512398712398"))
    assert r.status_code == 201
    assert len(calls) == 2


async def test_optional_without_key(idem_app):
    c, calls = idem_app
    assert (await c.post("/opt", json={"amount": "1"})).json() == {"n": 1}
    assert (await c.post("/opt", json={"amount": "1"})).json() == {"n": 2}
    r1 = await c.post("/opt", json={"amount": "1"}, headers=k("o1"))
    r2 = await c.post("/opt", json={"amount": "1"}, headers=k("o1"))
    assert r1.json() == r2.json() == {"n": 3}


async def test_save_serializes_pydantic_and_decimal(session, user):
    from decimal import Decimal

    from app.middleware.idempotency import Idempotency

    class M(BaseModel):
        amount: Decimal

    idem = Idempotency(Principal(user_id=user.id, kind="device", source="desktop"), None)
    resp = await idem.save(200, {"m": M(amount=Decimal("1.50")), "d": Decimal("2")})
    assert resp.body == b'{"m":{"amount":"1.50"},"d":"2"}'
    assert isinstance(AppError, type)
