"""Settings (defaults, optimistic locking, validation, server info) and user account."""

from __future__ import annotations

from sqlalchemy import func, select

from app.config import get_settings
from app.middleware.ratelimit import limiter
from app.models import Category, DeviceToken, Session, User
from app.services.auth.settings import DEFAULTS, new_settings_row
from tests.auth.conftest import PASSWORD, bearer, csrf_headers, login


async def test_settings_defaults(web):
    r = await web.get("/settings")
    assert r.status_code == 200
    s = r.json()
    assert s["default_currency"] == "VND"
    assert s["analytics_currency"] == "USD"
    assert s["summary_currency"] == "VND"
    assert s["timezone"] == "Asia/Ho_Chi_Minh"
    assert s["theme"] == "system"
    assert s["desktop_hotkey"] == "Ctrl+Alt+Space"
    assert s["report_monthly"] is True
    assert s["report_daily"] is False and s["report_weekly"] is False
    assert s["report_time"] == "10:00:00"
    assert s["small_amount_thousands"] is True
    assert s["telegram_user_id"] == 123456789


def test_new_settings_row_defaults():
    row = new_settings_row(1)
    assert row.timezone == "Asia/Ho_Chi_Minh" and row.report_monthly is True
    assert row.report_daily is False and row.report_weekly is False
    assert str(row.report_time) == "10:00:00"
    # forward-compatible schedule defaults (daily 22:00, weekly Sunday 21:00, reminders on)
    assert str(DEFAULTS["report_daily_time"]) == "22:00:00"
    assert DEFAULTS["report_weekly_day"] == 6 and str(DEFAULTS["report_weekly_time"]) == "21:00:00"
    assert DEFAULTS["reminders_enabled"] is True


async def test_patch_and_version_conflict(web):
    v = (await web.get("/settings")).json()["version"]
    r = await web.patch(
        "/settings",
        json={"version": v, "theme": "dark", "report_weekly": True, "report_time": "09:30"},
        headers=csrf_headers(web),
    )
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["theme"] == "dark" and s["report_weekly"] is True and s["report_time"] == "09:30:00"
    assert s["version"] == v + 1
    r = await web.patch(
        "/settings", json={"version": v, "theme": "light"}, headers=csrf_headers(web)
    )
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "version_conflict" and err["details"]["current_version"] == v + 1


async def test_patch_noop_keeps_version(web):
    v = (await web.get("/settings")).json()["version"]
    r = await web.patch(
        "/settings", json={"version": v, "theme": "system"}, headers=csrf_headers(web)
    )
    assert r.status_code == 200 and r.json()["version"] == v


async def test_patch_validation(web):
    v = (await web.get("/settings")).json()["version"]
    bad = [
        {"timezone": "Mars/Olympus"},
        {"analytics_currency": "RUB"},
        {"desktop_hotkey": "A"},
        {"desktop_hotkey": "Ctrl+"},
        {"telegram_user_id": 42},
        {"theme": "pink"},
        {"unknown_field": 1},
        {"default_currency": "v"},
    ]
    for patch in bad:
        r = await web.patch("/settings", json={"version": v, **patch}, headers=csrf_headers(web))
        assert r.status_code == 422, (patch, r.text)
    r = await web.patch(
        "/settings",
        json={
            "version": v,
            "timezone": "Europe/Moscow",
            "desktop_hotkey": "Ctrl+Shift+P",
            "analytics_currency": "vnd",
        },
        headers=csrf_headers(web),
    )
    assert r.status_code == 200, r.text
    assert r.json()["analytics_currency"] == "VND"


async def test_unbind_telegram(web):
    v = (await web.get("/settings")).json()["version"]
    r = await web.patch(
        "/settings", json={"version": v, "telegram_user_id": None}, headers=csrf_headers(web)
    )
    assert r.status_code == 200 and r.json()["telegram_user_id"] is None


async def test_server_info_has_no_secrets(web):
    r = await web.get("/settings/server")
    assert r.status_code == 200
    info = r.json()
    assert "allowed_telegram_ids" not in info  # Telegram ids are not exposed to any user
    assert info["ai_configured"] is True and info["telegram_bot_configured"] is True
    text = r.text
    s = get_settings()
    for secret in (s.secret_key, s.bot_service_token):
        assert secret not in text
    for tg in s.telegram_allowed_ids:
        assert str(tg) not in text
    assert "api_key" not in text


async def test_users_me(web, owner):
    r = await web.get("/users/me")
    assert r.status_code == 200
    assert r.json()["id"] == owner.id and r.json()["username"] == "owner"


async def test_change_password_flow(web, raw_client, owner, session, device_token):
    # Password endpoints share the 5/min/IP login window; this test makes 8 such calls.
    limiter.configure(login_per_minute=50)
    # second session that must be revoked
    import httpx

    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1"
    ) as other:
        assert (await login(other)).status_code == 200

        r = await web.post(
            "/users/me/password",
            json={"current_password": "wrong-one-123", "new_password": "a-brand-new-pass"},
            headers=csrf_headers(web),
        )
        assert r.status_code == 401 and r.json()["error"]["code"] == "wrong_password"
        r = await web.post(
            "/users/me/password",
            json={"current_password": PASSWORD, "new_password": "short"},
            headers=csrf_headers(web),
        )
        assert r.status_code == 422
        r = await web.post(
            "/users/me/password",
            json={"current_password": PASSWORD, "new_password": "a-brand-new-pass"},
            headers=csrf_headers(web),
        )
        assert r.status_code == 200, r.text
        assert (await web.get("/auth/me")).status_code == 200  # current session kept
        assert (await other.get("/auth/me")).status_code == 401  # other session revoked
        assert (await web.get("/auth/me", headers=bearer(device_token))).status_code == 401

    web.cookies.clear()
    assert (await login(web)).status_code == 401
    assert (await login(web, "a-brand-new-pass")).status_code == 200


async def test_change_password_keep_others(web, device_token):
    r = await web.post(
        "/users/me/password",
        json={
            "current_password": PASSWORD,
            "new_password": "a-brand-new-pass",
            "revoke_other_sessions": False,
        },
        headers=csrf_headers(web),
    )
    assert r.status_code == 200
    assert (await web.get("/auth/me", headers=bearer(device_token))).status_code == 200


async def test_wipe(web, owner, session):
    session.add(Category(user_id=owner.id, name="Тест", kind="expense"))
    await session.flush()
    v = (await web.get("/settings")).json()["version"]
    r = await web.post(
        "/users/me/wipe", json={"password": PASSWORD, "confirm": "нет"}, headers=csrf_headers(web)
    )
    assert r.status_code == 422
    r = await web.post(
        "/users/me/wipe",
        json={"password": "wrong-pass-xx", "confirm": "УДАЛИТЬ"},
        headers=csrf_headers(web),
    )
    assert r.status_code == 401
    r = await web.post(
        "/users/me/wipe",
        json={"password": PASSWORD, "confirm": "удалить"},
        headers=csrf_headers(web),
    )
    assert r.status_code == 200, r.text
    n = await session.scalar(
        select(func.count())
        .select_from(Category)
        .where(Category.user_id == owner.id, Category.name == "Тест")
    )
    assert n == 0
    s = (await web.get("/settings")).json()
    assert s["onboarding_done"] is False and s["version"] == v + 1
    # auth survives
    assert (await web.get("/auth/me")).status_code == 200
    assert await session.scalar(select(func.count()).select_from(User)) >= 1
    assert await session.scalar(select(func.count()).select_from(Session)) >= 1
    assert await session.scalar(select(func.count()).select_from(DeviceToken)) == 0
