"""Password login, cookie flags, server-side sessions, sliding renewal, logout."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.core.security import CSRF_COOKIE, SESSION_COOKIE, csrf_token_for, token_hash
from app.models import Session, User
from tests.auth.conftest import PASSWORD, cookie_header, csrf_headers, login

NINETY_DAYS = 90 * 86400


async def test_login_sets_cookies_and_returns_me(raw_client, owner, session):
    r = await login(raw_client)
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["user_id"] == owner.id
    assert me["username"] == "owner"
    assert me["auth"] == "session" and me["source"] == "web"
    assert me["has_passkeys"] is False
    assert me["onboarding_done"] is False
    assert me["csrf_token"] == raw_client.cookies.get(CSRF_COOKIE)

    sess_cookie = cookie_header(r, SESSION_COOKIE).lower()
    assert "httponly" in sess_cookie
    assert "samesite=lax" in sess_cookie
    assert "path=/" in sess_cookie
    assert f"max-age={NINETY_DAYS}" in sess_cookie

    csrf_cookie = cookie_header(r, CSRF_COOKIE).lower()
    assert "httponly" not in csrf_cookie
    assert "samesite=strict" in csrf_cookie
    assert f"max-age={NINETY_DAYS}" in csrf_cookie

    # Only HMAC hashes in the DB; 90-day expiry.
    raw = raw_client.cookies.get(SESSION_COOKIE)
    row = (await session.execute(select(Session).where(Session.user_id == owner.id))).scalar_one()
    assert row.token_hash == token_hash(raw)
    assert row.csrf_hash == token_hash(csrf_token_for(raw))
    assert raw.encode() not in row.token_hash
    delta = row.expires_at - row.created_at
    assert timedelta(days=89, hours=23) < delta <= timedelta(days=90, minutes=1)
    assert str(row.ip) == "127.0.0.1"


async def test_secure_flag_when_cookie_secure(raw_client, owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "cookie_secure", True)
    r = await login(raw_client)
    assert r.status_code == 200
    assert "secure" in cookie_header(r, SESSION_COOKIE).lower()
    assert "secure" in cookie_header(r, CSRF_COOKIE).lower()


async def test_wrong_password_and_unknown_user(raw_client, owner):
    r = await login(raw_client, "nope-nope-nope")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert r.json()["error"]["message"] == "Неверный пароль"
    assert "set-cookie" not in r.headers

    r = await raw_client.post("/auth/login", json={"username": "ghost", "password": PASSWORD})
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "Неверный пароль"


async def test_login_username_case_insensitive(raw_client, owner):
    r = await raw_client.post("/auth/login", json={"username": "OWNER", "password": PASSWORD})
    assert r.status_code == 200


async def test_inactive_user_cannot_login(raw_client, owner, session):
    owner.is_active = False
    await session.flush()
    assert (await login(raw_client)).status_code == 401


async def test_me_requires_auth(raw_client, owner):
    r = await raw_client.get("/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_me_with_session(web):
    r = await web.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["auth"] == "session"
    assert r.json()["csrf_token"] == web.cookies.get(CSRF_COOKIE)


async def test_logout_revokes_and_clears(web, session, owner):
    old = web.cookies.get(SESSION_COOKIE)
    r = await web.post("/auth/logout", headers=csrf_headers(web))
    assert r.status_code == 200 and r.json() == {"ok": True}
    sc = cookie_header(r, SESSION_COOKIE).lower()
    assert "max-age=0" in sc or "expires=thu, 01 jan 1970" in sc
    row = (await session.execute(select(Session).where(Session.user_id == owner.id))).scalar_one()
    await session.refresh(row)
    assert row.revoked_at is not None
    # The old cookie is dead even if the browser keeps it.
    web.cookies.clear()
    web.cookies.set(SESSION_COOKIE, old)
    r = await web.get("/auth/me")
    assert r.status_code == 401
    assert "max-age=0" in cookie_header(r, SESSION_COOKIE).lower()


async def test_expired_session_rejected(web, session, owner):
    row = (await session.execute(select(Session).where(Session.user_id == owner.id))).scalar_one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    r = await web.get("/auth/me")
    assert r.status_code == 401
    assert "войдите снова" in r.json()["error"]["message"]


async def test_sliding_renewal_once_a_day(web, session, owner):
    row = (await session.execute(select(Session).where(Session.user_id == owner.id))).scalar_one()
    # Fresh session: no renewal, no Set-Cookie.
    r = await web.get("/auth/me")
    assert r.status_code == 200
    assert not [h for h in r.headers.get_list("set-cookie") if h.startswith(SESSION_COOKIE)]

    two_days_ago = datetime.now(UTC) - timedelta(days=2)
    row.last_seen_at = two_days_ago
    row.expires_at = two_days_ago + timedelta(days=90)
    await session.flush()
    r = await web.get("/auth/me")
    assert r.status_code == 200
    assert f"max-age={NINETY_DAYS}" in cookie_header(r, SESSION_COOKIE).lower()
    await session.refresh(row)
    assert row.last_seen_at > two_days_ago + timedelta(days=1)
    assert row.expires_at > datetime.now(UTC) + timedelta(days=89)


async def test_sessions_list_and_revoke_other(web, raw_client, owner, session):
    # Second browser session.
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as other:
        assert (await login(other)).status_code == 200
        r = await web.get("/auth/sessions")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 2
        assert sum(1 for s in items if s["is_current"]) == 1
        other_id = next(s["id"] for s in items if not s["is_current"])
        r = await web.delete(f"/auth/sessions/{other_id}", headers=csrf_headers(web))
        assert r.status_code == 200
        assert (await other.get("/auth/me")).status_code == 401
        assert (await web.get("/auth/me")).status_code == 200
        r = await web.delete(f"/auth/sessions/{other_id}x", headers=csrf_headers(web))
        assert r.status_code == 422
        import uuid

        r = await web.delete(f"/auth/sessions/{uuid.uuid4()}", headers=csrf_headers(web))
        assert r.status_code == 404


async def test_sessions_list_forbidden_for_device(raw_client, device_token):
    r = await raw_client.get("/auth/sessions", headers={"Authorization": f"Bearer {device_token}"})
    assert r.status_code == 403


async def test_logout_all(web, device_token, session, owner):
    r = await web.post("/auth/logout-all", headers=csrf_headers(web))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["sessions_revoked"] == 1 and body["devices_revoked"] == 1
    assert (await web.get("/auth/me")).status_code == 401
    r = await web.get("/auth/me", headers={"Authorization": f"Bearer {device_token}"})
    assert r.status_code == 401


async def test_logout_all_keep_current(web, device_token):
    r = await web.post("/auth/logout-all?keep_current=true", headers=csrf_headers(web))
    assert r.status_code == 200
    assert r.json()["sessions_revoked"] == 0 and r.json()["devices_revoked"] == 1
    assert (await web.get("/auth/me")).status_code == 200


async def test_cookie_session_rejected_from_desktop_origin(web):
    r = await web.get("/auth/me", headers={"Origin": "app://pocket"})
    assert r.status_code == 401


async def test_password_rehash_kept_argon2id(raw_client, owner, session):
    assert owner.password_hash.startswith("$argon2id$")
    await login(raw_client)
    u = (await session.execute(select(User).where(User.id == owner.id))).scalar_one()
    assert u.password_hash.startswith("$argon2id$")
