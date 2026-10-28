"""Electron device tokens: issue after password, hash-only storage, list, last_used, revoke,
CORS for the packaged origin (Bearer only)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.security import token_hash
from app.models import DeviceToken
from tests.auth.conftest import PASSWORD, bearer, csrf_headers


async def test_device_login_issues_token_hash_only(raw_client, owner, session):
    r = await raw_client.post(
        "/auth/device-login",
        json={"password": PASSWORD, "device_name": "DESKTOP-ABC", "platform": "windows"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    token = body["token"]
    assert token.startswith("pfd_") and len(token) >= 40
    assert "set-cookie" not in r.headers
    row = (
        await session.execute(
            select(DeviceToken).where(DeviceToken.id == uuid.UUID(body["device_id"]))
        )
    ).scalar_one()
    assert row.token_hash == token_hash(token)
    assert row.name == "DESKTOP-ABC" and row.platform == "windows"


async def test_device_login_wrong_password(raw_client, owner):
    r = await raw_client.post(
        "/auth/device-login",
        json={"password": "bad-password-x", "device_name": "PC", "platform": "windows"},
    )
    assert r.status_code == 401


async def test_device_login_validates_platform(raw_client, owner):
    r = await raw_client.post(
        "/auth/device-login",
        json={"password": PASSWORD, "device_name": "PC", "platform": "android"},
    )
    assert r.status_code == 422


async def test_bearer_device_principal(raw_client, device_token):
    raw_client.cookies.clear()
    r = await raw_client.get("/auth/me", headers=bearer(device_token))
    assert r.status_code == 200
    me = r.json()
    assert me["auth"] == "device" and me["source"] == "desktop"
    assert me["csrf_token"] is None


async def test_bad_bearer(raw_client, owner):
    for tok in ("pfd_doesnotexist", "garbage", ""):
        r = await raw_client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, tok
    r = await raw_client.get("/auth/me", headers={"Authorization": "Basic Zm9vOmJhcg=="})
    assert r.status_code == 401


async def test_last_used_updated_throttled(raw_client, device_token, session):
    dev = (await session.execute(select(DeviceToken))).scalar_one()
    old = datetime.now(UTC) - timedelta(hours=1)
    dev.last_used_at = old
    await session.flush()
    r = await raw_client.get("/auth/me", headers=bearer(device_token))
    assert r.status_code == 200
    await session.refresh(dev)
    assert dev.last_used_at > old + timedelta(minutes=50)


async def test_list_and_revoke_device_from_web(web, device_token):
    r = await web.get("/devices")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    d = items[0]
    assert d["name"] == "DESKTOP-TEST" and d["platform"] == "windows"
    assert d["last_used_at"] is not None and d["is_current"] is False

    r = await web.get("/devices", headers=bearer(device_token))
    assert r.json()[0]["is_current"] is True

    r = await web.delete(f"/devices/{d['id']}", headers=csrf_headers(web))
    assert r.status_code == 200
    r = await web.get("/auth/me", headers=bearer(device_token))
    assert r.status_code == 401
    assert "Устройство отозвано" in r.json()["error"]["message"]
    assert (await web.get("/devices")).json() == []
    r = await web.delete(f"/devices/{uuid.uuid4()}", headers=csrf_headers(web))
    assert r.status_code == 404


async def test_device_logout_revokes_itself(raw_client, device_token):
    raw_client.cookies.clear()
    r = await raw_client.post("/auth/logout", headers=bearer(device_token))
    assert r.status_code == 200
    assert (await raw_client.get("/auth/me", headers=bearer(device_token))).status_code == 401


async def test_cors_preflight_for_packaged_electron(raw_client, owner):
    r = await raw_client.options(
        "/auth/me",
        headers={
            "Origin": "app://pocket",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "app://pocket"


async def test_cors_unknown_origin_not_allowed(raw_client, owner):
    r = await raw_client.options(
        "/auth/me",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in r.headers


async def test_electron_origin_works_with_bearer(raw_client, device_token):
    raw_client.cookies.clear()
    r = await raw_client.get("/auth/me", headers={**bearer(device_token), "Origin": "app://pocket"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "app://pocket"
