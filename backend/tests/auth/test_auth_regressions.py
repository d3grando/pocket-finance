"""Auth regression tests.

* settings PATCH: SSE ``change.source`` = client (was null); two PATCHes with the same stale
  version are serialized (FOR UPDATE) → exactly one 200, the other 409 (was lost update);
* Idempotency-Key left ``in_progress`` by a crashed request is taken over after the lease
  (was 409 for 48 h); a live one answers 409 with Retry-After;
* X-Telegram-User-Id with Unicode digits → 403 (was 500);
* the global failed-login cap no longer locks the owner out of a known browser, nor out of
  /users/me/password (was: 20 wrong passwords/hour from anyone → everyone gets 429).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import VersionConflict
from app.core.security import KNOWN_DEVICE_COOKIE, new_known_device_value
from app.middleware import idempotency as idem_mod
from app.middleware.ratelimit import limiter
from app.models import IdempotencyKey, User
from app.schemas.settings import SettingsPatch
from app.services.auth import settings as settings_svc
from app.services.auth.bot import parse_telegram_id
from app.services.auth.settings import new_settings_row
from tests.auth.conftest import PASSWORD, FakeClock, bearer, csrf_headers, login
from tests.auth.test_bot import bot_headers
from tests.auth.test_idempotency import idem_app, k  # noqa: F401 (fixture re-export)

# ---------------------------------------------------------------- settings: SSE source


@pytest.fixture
def published(monkeypatch) -> list[dict[str, Any]]:
    from app.services.events import bus

    calls: list[dict[str, Any]] = []

    async def _publish(session, user_id, entities, *, action_id=None, source=None):
        calls.append({"entities": list(entities), "source": source, "action_id": action_id})

    monkeypatch.setattr(bus, "publish", _publish)
    return calls


async def test_settings_change_event_carries_source_web(web, published):
    v = (await web.get("/settings")).json()["version"]
    r = await web.patch(
        "/settings", json={"version": v, "report_daily": True}, headers=csrf_headers(web)
    )
    assert r.status_code == 200, r.text
    assert published == [{"entities": ["settings"], "source": "web", "action_id": None}]


async def test_settings_change_event_carries_source_desktop(
    raw_client, owner, device_token, published
):
    v = (await raw_client.get("/settings", headers=bearer(device_token))).json()["version"]
    r = await raw_client.patch(
        "/settings", json={"version": v, "report_daily": True}, headers=bearer(device_token)
    )
    assert r.status_code == 200, r.text
    assert [c["source"] for c in published] == ["desktop"]


async def test_settings_change_event_carries_source_telegram(raw_client, owner, published):
    v = (await raw_client.get("/settings", headers=bot_headers())).json()["version"]
    r = await raw_client.patch(
        "/settings", json={"version": v, "report_weekly": True}, headers=bot_headers()
    )
    assert r.status_code == 200, r.text
    assert [c["source"] for c in published] == ["telegram"]


async def test_settings_noop_patch_publishes_nothing(web, published):
    v = (await web.get("/settings")).json()["version"]
    r = await web.patch("/settings", json={"version": v, "theme": "system"},
                        headers=csrf_headers(web))
    assert r.status_code == 200 and published == []


# ---------------------------------------------------------------- settings: no lost update


@pytest.fixture
async def committed_user(engine: AsyncEngine):
    """A user + settings committed for real (visible to other connections); removed after."""
    async with AsyncSession(engine, expire_on_commit=False) as s:
        u = User(username=f"race-{uuid.uuid4().hex[:10]}")
        s.add(u)
        await s.flush()
        s.add(new_settings_row(u.id))
        await s.commit()
        uid = u.id
    yield uid
    async with AsyncSession(engine) as s:
        await s.execute(delete(User).where(User.id == uid))
        await s.commit()


async def test_concurrent_stale_patches_one_wins(engine: AsyncEngine, committed_user: int):
    """Two PATCHes with the same version on two connections: the second waits for the row
    lock, sees the bumped version and gets 409 instead of silently overwriting."""
    a = AsyncSession(engine, expire_on_commit=False)
    b = AsyncSession(engine, expire_on_commit=False)
    try:
        st_a = await settings_svc.patch_settings(
            a, committed_user, SettingsPatch(version=1, report_time=time(8, 0)), source="web"
        )
        assert st_a.version == 2  # flushed, not committed: A holds the row lock

        task_b = asyncio.create_task(
            settings_svc.patch_settings(
                b, committed_user, SettingsPatch(version=1, report_time=time(9, 0)),
                source="desktop",
            )
        )
        await asyncio.sleep(0.3)
        assert not task_b.done(), "B must wait for A's row lock"
        await a.commit()
        with pytest.raises(VersionConflict) as ei:
            await task_b
        assert ei.value.details == {"current_version": 2}
        await b.rollback()
    finally:
        await a.close()
        await b.close()
    async with AsyncSession(engine) as s:
        st = await settings_svc.load_settings(s, committed_user)
        assert st is not None and st.version == 2 and st.report_time == time(8, 0)


async def test_concurrent_patches_different_fields_same_version(
    engine: AsyncEngine, committed_user: int
):
    """Different fields with one version: both used to get 200 and the
    version grew by one only."""

    async def one(field: dict[str, Any]) -> str:
        async with AsyncSession(engine, expire_on_commit=False) as s:
            try:
                await settings_svc.patch_settings(
                    s, committed_user, SettingsPatch(version=1, **field)
                )
                await asyncio.sleep(0.05)  # keep the lock a bit so the other one queues
                await s.commit()
                return "ok"
            except VersionConflict:
                await s.rollback()
                return "409"

    res = await asyncio.gather(one({"report_daily": True}), one({"report_weekly": True}))
    assert sorted(res) == ["409", "ok"]
    async with AsyncSession(engine) as s:
        st = await settings_svc.load_settings(s, committed_user)
        assert st is not None and st.version == 2
        assert st.report_daily != st.report_weekly  # exactly one change applied


# ---------------------------------------------------------------- idempotency: abandoned keys


class _Req:
    method = "POST"

    class url:
        path = "/things"
        query = ""


async def _stuck_row(session: AsyncSession, user_id: int, key: str, body: bytes, age: float):
    """An in_progress row as left by a process killed mid-request ``age`` seconds ago."""
    now = datetime.now(UTC)
    session.add(
        IdempotencyKey(
            user_id=user_id,
            key=key,
            method="POST",
            path="/things",
            request_hash=idem_mod._request_hash(_Req, body),
            state="in_progress",
            created_at=now - timedelta(seconds=age),
            expires_at=now + timedelta(hours=48),
        )
    )
    await session.flush()


async def test_abandoned_in_progress_key_is_taken_over(idem_app, session, user):  # noqa: F811
    c, calls = idem_app
    body = b'{"amount":"7"}'
    await _stuck_row(session, user.id, "tg:1:42", body, age=600)
    hdr = {**k("tg:1:42"), "content-type": "application/json"}
    r = await c.post("/things", content=body, headers=hdr)
    assert r.status_code == 201, r.text
    assert calls == ["7"]
    # and the result is stored: a further retry is a replay, not a second execution
    r2 = await c.post("/things", content=body, headers=hdr)
    assert r2.status_code == 201 and r2.headers["Idempotent-Replayed"] == "true"
    assert calls == ["7"]
    row = (
        await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == "tg:1:42"))
    ).scalar_one()
    assert row.state == "done"


async def test_live_in_progress_key_409_with_retry_after(idem_app, session, user):  # noqa: F811
    c, calls = idem_app
    body = b'{"amount":"8"}'
    await _stuck_row(session, user.id, "live-1", body, age=5)
    r = await c.post(
        "/things", content=body, headers={**k("live-1"), "content-type": "application/json"}
    )
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "idempotency_in_progress"
    lease = idem_mod.in_progress_lease().total_seconds()
    assert 1 <= err["details"]["retry_after"] <= lease
    assert int(r.headers["Retry-After"]) == err["details"]["retry_after"]
    assert calls == []


async def test_abandoned_key_with_other_body_is_still_mismatch(idem_app, session, user):  # noqa: F811
    c, calls = idem_app
    await _stuck_row(session, user.id, "mm-1", b'{"amount":"1"}', age=600)
    r = await c.post("/things", json={"amount": "2"}, headers=k("mm-1"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_mismatch"
    assert calls == []


def test_lease_default_and_override(monkeypatch):
    from app.config import get_settings
    from app.core.security_config import get_security_settings

    monkeypatch.setattr(get_security_settings(), "idempotency_lease_seconds", None)
    monkeypatch.setattr(get_settings(), "ai_timeout", 20.0)
    assert idem_mod.in_progress_lease() == timedelta(seconds=60)
    monkeypatch.setattr(get_settings(), "ai_timeout", 45.0)
    assert idem_mod.in_progress_lease() == timedelta(seconds=110)
    monkeypatch.setattr(get_security_settings(), "idempotency_lease_seconds", 30)
    assert idem_mod.in_progress_lease() == timedelta(seconds=30)


async def test_release_falls_back_to_fresh_session(monkeypatch):
    """Request session broken (cancelled mid-query) → the key is deleted on a new session."""
    from app.core.principal import Principal

    class Broken:
        async def rollback(self):
            raise RuntimeError("connection is closed")

    executed: list[str] = []

    class Fresh:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt):
            executed.append(str(stmt).split()[0])

        async def commit(self):
            executed.append("COMMIT")

    import app.db as app_db

    monkeypatch.setattr(app_db, "get_sessionmaker", lambda: (lambda: Fresh()))
    idem = idem_mod.Idempotency(
        Principal(user_id=1, kind="device", source="desktop"), "k1", session=Broken(), owned=True
    )
    await idem.release()
    assert executed == ["DELETE", "COMMIT"]


# ---------------------------------------------------------------- bot: X-Telegram-User-Id


@pytest.mark.parametrize("raw", ["123456789²", "¹", "１２３４", "١٢٣", "-5", "0", "1e3", " ",
                                 "99999999999999999999"])
def test_parse_telegram_id_rejects_non_ascii_digits(raw):
    from app.core.errors import AppError

    with pytest.raises(AppError) as ei:
        parse_telegram_id(raw)
    assert ei.value.status == 403 and ei.value.code == "telegram_forbidden"


def test_parse_telegram_id_ok():
    assert parse_telegram_id(" 123456789 ") == 123456789
    assert parse_telegram_id(None) is None


async def test_unicode_digit_header_is_403_not_500(raw_client, owner):
    for raw in (b"123456789\xb2", b"\xb9"):
        h: dict[str, Any] = {**bot_headers(None), "X-Telegram-User-Id": raw}
        r = await raw_client.get("/auth/me", headers=h)
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "telegram_forbidden"


# ---------------------------------------------------------------- login: no global lock-out


def _xff(ip: str) -> dict[str, str]:
    return {"X-Forwarded-For": ip}


def _fill_global_cap(n: int) -> None:
    for _ in range(n):
        limiter.record_failure("203.0.113.250", password=True)


async def test_login_sets_known_device_cookie(raw_client, owner):
    r = await login(raw_client)
    assert r.status_code == 200
    sc = [h for h in r.headers.get_list("set-cookie") if h.startswith(KNOWN_DEVICE_COOKIE + "=")]
    assert len(sc) == 1
    low = sc[0].lower()
    assert "httponly" in low and "samesite=strict" in low and "path=/api/v1/auth" in low
    assert "max-age=31536000" in low
    # failed login → no cookie
    raw_client.cookies.clear()
    r = await login(raw_client, "wrong-password-1")
    assert r.status_code == 401
    assert not [h for h in r.headers.get_list("set-cookie") if KNOWN_DEVICE_COOKIE in h]


async def test_global_cap_does_not_lock_out_known_browser(
    raw_client, owner, rate_limits: FakeClock
):
    """20 wrong passwords/hour from rotating IPs → unknown clients get 429, but the owner's
    browser that logged in before still logs in with the right password."""
    limiter.configure(login_failures_per_hour=20, ban_threshold=100, login_per_minute=100)
    assert (await login(raw_client)).status_code == 200  # owner's browser becomes known
    known = raw_client.cookies.get(KNOWN_DEVICE_COOKIE)
    assert known

    async with httpx.AsyncClient(
        transport=raw_client._transport, base_url="http://test/api/v1"
    ) as attacker:
        for i in range(20):
            r = await login(attacker, "wrong-password-1", headers=_xff(f"198.51.100.{i + 1}"))
            assert r.status_code == 401
        r = await login(attacker, headers=_xff("198.51.100.200"))  # right password, unknown
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) > 60

    raw_client.cookies.clear()
    raw_client.cookies.set(KNOWN_DEVICE_COOKIE, known, path="/api/v1/auth")
    r = await login(raw_client, headers=_xff("192.0.2.77"))
    assert r.status_code == 200, r.text
    # device-login (Electron re-login from the same browser profile) is not blocked either
    r = await raw_client.post(
        "/auth/device-login",
        json={"password": PASSWORD, "device_name": "PC", "platform": "windows"},
        headers=_xff("192.0.2.77"),
    )
    assert r.status_code == 201, r.text


async def test_forged_known_cookie_gives_no_exemption(raw_client, owner):
    limiter.configure(login_failures_per_hour=3, ban_threshold=100, login_per_minute=100)
    _fill_global_cap(3)
    raw_client.cookies.set(KNOWN_DEVICE_COOKIE, "abc.def", path="/api/v1/auth")
    assert (await login(raw_client)).status_code == 429
    raw_client.cookies.set(KNOWN_DEVICE_COOKIE, new_known_device_value("n1") + "x",
                           path="/api/v1/auth")
    assert (await login(raw_client)).status_code == 429


async def test_known_cookie_failures_are_capped_per_cookie(raw_client, owner):
    """A known browser guessing passwords loses its exemption after 10 failures/hour; its
    failures do not fill the global cap meanwhile."""
    limiter.configure(
        login_failures_per_hour=20, known_device_failures_per_hour=3, ban_threshold=100,
        login_per_minute=100,
    )
    raw_client.cookies.set(KNOWN_DEVICE_COOKIE, new_known_device_value(), path="/api/v1/auth")
    for _ in range(3):
        assert (await login(raw_client, "wrong-password-1")).status_code == 401
    assert limiter.login_failures_global.count("global") == 0
    # 4th failure goes to the global window (exemption used up)
    assert (await login(raw_client, "wrong-password-1")).status_code == 401
    assert limiter.login_failures_global.count("global") == 1
    _fill_global_cap(19)
    assert (await login(raw_client)).status_code == 429


async def test_known_cookie_success_resets_its_failures(raw_client, owner):
    limiter.configure(known_device_failures_per_hour=3, ban_threshold=100, login_per_minute=100)
    assert (await login(raw_client)).status_code == 200
    nonce = raw_client.cookies.get(KNOWN_DEVICE_COOKIE).split(".")[0]
    for _ in range(2):
        assert (await login(raw_client, "wrong-password-1")).status_code == 401
    assert limiter.login_failures_known.count(nonce) == 2
    assert (await login(raw_client)).status_code == 200
    assert limiter.login_failures_known.count(nonce) == 0
    # the same nonce is kept when the cookie is re-issued
    assert raw_client.cookies.get(KNOWN_DEVICE_COOKIE).split(".")[0] == nonce


async def test_global_cap_does_not_block_password_change_in_session(
    web, owner, rate_limits: FakeClock
):
    """/users/me/password needs a live session: the anonymous global cap must not block it."""
    limiter.configure(login_failures_per_hour=3, ban_threshold=100, login_per_minute=100)
    _fill_global_cap(3)
    r = await web.post(
        "/users/me/password",
        json={"current_password": "wrong-password-1", "new_password": "x" * 16},
        headers=csrf_headers(web),
    )
    assert r.status_code == 401 and r.json()["error"]["code"] == "wrong_password"
    r = await web.post(
        "/users/me/password",
        json={"current_password": PASSWORD, "new_password": "new-password-123456",
              "revoke_other_sessions": False},
        headers=csrf_headers(web),
    )
    assert r.status_code == 200, r.text


async def test_password_change_failures_counted_per_session(web, owner):
    limiter.configure(login_failures_per_hour=2, ban_threshold=100, login_per_minute=100)
    for _ in range(2):
        r = await web.post(
            "/users/me/password",
            json={"current_password": "wrong-password-1", "new_password": "x" * 16},
            headers=csrf_headers(web),
        )
        assert r.status_code == 401
    r = await web.post(
        "/users/me/password",
        json={"current_password": PASSWORD, "new_password": "x" * 16},
        headers=csrf_headers(web),
    )
    assert r.status_code == 429
    assert limiter.login_failures_global.count("global") == 0


async def test_passkey_login_issues_known_cookie(web, owner):
    from tests.auth.softwebauthn import SoftAuthenticator
    from tests.auth.test_passkeys import ORIGIN, login_options, register

    auth = SoftAuthenticator()
    await register(web, auth)
    await web.post("/auth/logout", headers=csrf_headers(web))
    web.cookies.clear()
    r = await web.post("/passkeys/login/verify", json={})  # failure → no cookie
    assert r.status_code >= 400
    assert not [h for h in r.headers.get_list("set-cookie") if KNOWN_DEVICE_COOKIE in h]
    data = await login_options(web)
    assertion = auth.get(data["options"], origin=ORIGIN)
    r = await web.post(
        "/passkeys/login/verify",
        json={"challenge_id": data["challenge_id"], "credential": assertion},
    )
    assert r.status_code == 200, r.text
    assert [h for h in r.headers.get_list("set-cookie") if h.startswith(KNOWN_DEVICE_COOKIE)]
