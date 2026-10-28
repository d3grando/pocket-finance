"""Security regressions of the auth layer.

* A passkey needs the password unless the session is fresh, is announced in Telegram, and does not
  outlive «log out everywhere», a password change or a CLI password reset (a key added from a
  stolen session used to survive all of them).
* Rate-limit keys are built from the credential exactly as authentication reads it; AI messages
  and password re-checks are also limited per user (switching credentials gave fresh buckets).
* Two ``pf_session`` cookies → 400; server info exposes no Telegram ids; logs mask Telegram ids.
"""

from __future__ import annotations

import io
import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, update
from starlette.datastructures import Headers

from app.cli import build_parser, run
from app.config import get_settings
from app.core.security import SESSION_COOKIE
from app.core.timeutil import utcnow
from app.middleware.ratelimit import limiter, principal_key
from app.middleware.session_cookies import MultipleSessionCookies, session_cookie_value
from app.models import NotificationOutbox, Passkey
from app.models import Session as WebSession
from app.services.auth import settings as settings_svc
from app.services.auth.bot import parse_bearer
from app.services.auth.passkeys import short_user_agent
from app.services.auth.settings import load_settings
from tests.auth.conftest import PASSWORD, bearer, csrf_headers, login
from tests.auth.softwebauthn import SoftAuthenticator
from tests.conftest import OWNER_TG_ID

ORIGIN = "http://localhost:5173"  # PUBLIC_URL default → RP ID "localhost"
BOT = "test-bot-token-0123456789abcdef"  # tests/conftest.py BOT_SERVICE_TOKEN
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)


# ---------------------------------------------------------------- helpers


async def age_sessions(session, seconds: int = 600) -> None:
    """Make every web session older than the 5-minute «fresh login» window."""
    await session.execute(
        update(WebSession).values(created_at=utcnow() - timedelta(seconds=seconds))
    )
    await session.flush()


async def register_options(client: httpx.AsyncClient, password: str | None = None):
    body = {"password": password} if password is not None else None
    return await client.post("/passkeys/register/options", json=body, headers=csrf_headers(client))


async def add_passkey(
    client: httpx.AsyncClient,
    auth: SoftAuthenticator,
    *,
    name: str = "iPhone",
    password: str | None = None,
    user_agent: str | None = None,
) -> dict:
    r = await register_options(client, password)
    assert r.status_code == 200, r.text
    data = r.json()
    headers = csrf_headers(client)
    if user_agent:
        headers["User-Agent"] = user_agent
    r = await client.post(
        "/passkeys/register/verify",
        json={
            "challenge_id": data["challenge_id"],
            "credential": auth.create(data["options"], origin=ORIGIN),
            "name": name,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def passkey_login(client: httpx.AsyncClient, auth: SoftAuthenticator) -> httpx.Response:
    r = await client.post("/passkeys/login/options", json={"username": "owner"})
    assert r.status_code == 200, r.text
    data = r.json()
    return await client.post(
        "/passkeys/login/verify",
        json={
            "challenge_id": data["challenge_id"],
            "credential": auth.get(data["options"], origin=ORIGIN),
        },
    )


async def count_passkeys(session, user_id: int) -> int:
    return int(
        await session.scalar(select(func.count()).where(Passkey.user_id == user_id)) or 0
    )


def bot_headers(tg: int | str = OWNER_TG_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {BOT}", "X-Telegram-User-Id": str(tg)}


# ---------------------------------------------------------------- passkey re-authentication


async def test_old_session_cannot_add_passkey_without_password(web, session, owner):
    await age_sessions(session)
    for body in (None, {}):
        r = await web.post(
            "/passkeys/register/options", json=body, headers=csrf_headers(web)
        )
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "reauth_required"
    r = await register_options(web, "wrong-password-1")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "wrong_password"
    # the right password opens the ceremony and the key really gets registered
    await add_passkey(web, SoftAuthenticator(), password=PASSWORD)
    assert await count_passkeys(session, owner.id) == 1


async def test_fresh_login_adds_passkey_without_password(web, session, owner):
    """The Face ID offer right after a login must keep working without a password prompt."""
    await add_passkey(web, SoftAuthenticator())
    assert await count_passkeys(session, owner.id) == 1


async def test_register_password_is_bounded(web):
    r = await register_options(web, "x" * 300)
    assert r.status_code == 422


async def test_logout_all_removes_passkeys_by_default(web, session, owner):
    auth = SoftAuthenticator()
    await add_passkey(web, auth)
    r = await web.post("/auth/logout-all", headers=csrf_headers(web))
    assert r.status_code == 200, r.text
    web.cookies.clear()
    # the key can no longer sign in (it used to: 200 right after «log out everywhere»)
    assert (await passkey_login(web, auth)).status_code == 401
    assert r.json()["passkeys_removed"] == 1
    assert await count_passkeys(session, owner.id) == 0


async def test_logout_all_can_keep_passkeys(web, session, owner):
    auth = SoftAuthenticator()
    await add_passkey(web, auth)
    r = await web.post("/auth/logout-all?remove_passkeys=false", headers=csrf_headers(web))
    assert r.status_code == 200, r.text
    assert r.json()["passkeys_removed"] == 0
    web.cookies.clear()
    assert (await passkey_login(web, auth)).status_code == 200


async def test_password_change_removes_passkeys_unless_opted_out(web, session, owner):
    limiter.configure(login_per_minute=50)
    await add_passkey(web, SoftAuthenticator())
    r = await web.post(
        "/users/me/password",
        json={
            "current_password": PASSWORD,
            "new_password": "a-brand-new-pass-1",
            "remove_passkeys": False,
        },
        headers=csrf_headers(web),
    )
    assert r.status_code == 200, r.text
    assert await count_passkeys(session, owner.id) == 1
    r = await web.post(
        "/users/me/password",
        json={"current_password": "a-brand-new-pass-1", "new_password": "another-new-pass-2"},
        headers=csrf_headers(web),
    )
    assert r.status_code == 200, r.text
    assert await count_passkeys(session, owner.id) == 0
    assert (await web.get("/auth/me")).status_code == 200  # own session stays signed in


async def test_new_passkey_is_announced_in_telegram(web, session, owner):
    await add_passkey(web, SoftAuthenticator(), name="<b>Ключ</b>", user_agent=IPHONE_UA)
    rows = list(
        (
            await session.execute(
                select(NotificationOutbox).where(NotificationOutbox.user_id == owner.id)
            )
        ).scalars()
    )
    assert len(rows) == 1
    assert rows[0].status == "pending" and rows[0].channel == "telegram"
    text = rows[0].payload["text"]
    assert "Добавлен новый ключ входа «&lt;b&gt;Ключ&lt;/b&gt;»" in text  # HTML-escaped name
    assert "iPhone · Safari" in text
    assert "Выйти везде" in text


async def test_passkey_notice_skipped_without_telegram(web, session, owner, monkeypatch):
    st = await load_settings(session, owner.id)
    st.telegram_user_id = None
    await session.flush()
    monkeypatch.setattr(get_settings(), "telegram_allowed_ids", [])
    await add_passkey(web, SoftAuthenticator())
    n = await session.scalar(
        select(func.count()).where(NotificationOutbox.user_id == owner.id)
    )
    assert n == 0


def test_short_user_agent():
    assert short_user_agent(IPHONE_UA) == "iPhone · Safari"
    win_chrome = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Safari/537.36"
    )
    assert short_user_agent(win_chrome) == "Windows · Chrome"
    assert short_user_agent(win_chrome + " Edg/141.0.0.0") == "Windows · Edge"
    assert short_user_agent(None) == "неизвестный браузер"
    assert short_user_agent("<script>") == "неизвестный браузер"


# ---------------------------------------------------------------- CLI resets drop passkeys


def _factory(session):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


async def _cli(session, *argv: str) -> int:
    return await run(build_parser().parse_args(list(argv)), _factory(session))


def _passkey(user_id: int) -> Passkey:
    return Passkey(user_id=user_id, credential_id=os.urandom(16), public_key=b"k", name="k")


async def test_cli_set_password_removes_passkeys(session, owner, capsys, monkeypatch):
    session.add(_passkey(owner.id))
    await session.flush()
    assert await _cli(session, "set-password", "--password", "first-password-123",
                      "--keep-passkeys") == 0
    assert await count_passkeys(session, owner.id) == 1
    monkeypatch.setattr("sys.stdin", io.StringIO("second-password-123\n"))
    assert await _cli(session, "set-password", "--password-stdin") == 0
    assert await count_passkeys(session, owner.id) == 0
    assert "удалено ключей входа (passkey): 1" in capsys.readouterr().out


async def test_cli_set_password_keep_sessions_still_drops_passkeys(session, owner, capsys):
    session.add(_passkey(owner.id))
    await session.flush()
    assert await _cli(session, "set-password", "--password", "first-password-123",
                      "--keep-sessions") == 0
    assert await count_passkeys(session, owner.id) == 0


async def test_cli_create_owner_force_signs_out_everywhere(
    session, raw_client, owner, capsys
):
    assert (await login(raw_client)).status_code == 200
    session.add(_passkey(owner.id))
    await session.flush()
    code = await _cli(
        session, "create-owner", "--force", "--password", "replacement-password-1"
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Отозвано сессий: 1" in out and "удалено ключей входа (passkey): 1" in out
    assert (await raw_client.get("/auth/me")).status_code == 401
    assert await count_passkeys(session, owner.id) == 0
    row = (await session.execute(select(WebSession))).scalar_one()
    assert row.revoked_at is not None


async def test_cli_logout_all_removes_passkeys(session, owner, capsys):
    session.add(_passkey(owner.id))
    await session.flush()
    assert await _cli(session, "logout-all", "--keep-passkeys") == 0
    assert await count_passkeys(session, owner.id) == 1
    assert await _cli(session, "logout-all") == 0
    assert await count_passkeys(session, owner.id) == 0


# ---------------------------------------------------------------- rate-limit keys


def test_parse_bearer_matches_authentication():
    assert parse_bearer("Bearer abc") == "abc"
    assert parse_bearer("bearer abc") == "abc"
    assert parse_bearer("  BEARER   abc  ") == "abc"
    assert parse_bearer("Bearer") is None
    assert parse_bearer("Bearer   ") is None
    assert parse_bearer("Basic abc") is None
    assert parse_bearer("") is None


def test_principal_key_normalizes_credentials():
    def key(**h: str) -> str:
        return principal_key(Headers(headers=h), "192.0.2.1")

    same = {key(authorization=a) for a in ("Bearer t1", "bearer t1", "Bearer  t1", " Bearer t1 ")}
    assert len(same) == 1
    # a device token ignores X-Telegram-User-Id (authentication does too)
    assert key(authorization="Bearer t1", **{"x-telegram-user-id": "5"}) == key(
        authorization="Bearer t1"
    )
    # the bot token: one bucket per Telegram user, normalized like parse_telegram_id
    bot = f"Bearer {BOT}"
    k1 = key(authorization=bot, **{"x-telegram-user-id": "42"})
    assert k1 == key(authorization=bot, **{"x-telegram-user-id": " 042 "})
    assert k1 != key(authorization=bot, **{"x-telegram-user-id": "43"})
    # credentials authentication rejects outright are keyed by IP
    assert key(authorization=bot, **{"x-telegram-user-id": "4x2"}) == "ip:192.0.2.1"
    assert key(authorization="Basic abc") == "ip:192.0.2.1"
    # cookies: parsed like Starlette (spaces, quotes)
    assert key(cookie="pf_session=abc") == key(cookie='a=1; pf_session = "abc" ')
    assert key(cookie="pf_session=") == "ip:192.0.2.1"
    with pytest.raises(MultipleSessionCookies):
        key(cookie="pf_session=a; pf_session=b")


def test_session_cookie_value_across_headers():
    h = Headers(raw=[(b"cookie", b"x=1; pf_session=abc"), (b"cookie", b"y=2")])
    assert session_cookie_value(h) == "abc"
    h = Headers(raw=[(b"cookie", b"pf_session=abc"), (b"cookie", b"pf_session=abc")])
    with pytest.raises(MultipleSessionCookies):
        session_cookie_value(h)
    assert session_cookie_value(Headers(raw=[])) is None


async def test_bearer_spelling_variants_share_one_bucket(raw_client, owner, device_token):
    limiter.configure(api_per_minute=3)
    raw_client.cookies.clear()
    for auth in (f"Bearer {device_token}", f"bearer {device_token}", f"Bearer  {device_token}"):
        r = await raw_client.get("/auth/me", headers={"Authorization": auth})
        assert r.status_code == 200, r.text
    r = await raw_client.get("/auth/me", headers={"Authorization": f"BEARER {device_token} "})
    assert r.status_code == 429


async def test_device_token_with_varying_telegram_header_shares_bucket(
    raw_client, owner, device_token
):
    limiter.configure(api_per_minute=2)
    raw_client.cookies.clear()
    codes = [
        (
            await raw_client.get(
                "/auth/me", headers={**bearer(device_token), "X-Telegram-User-Id": str(i)}
            )
        ).status_code
        for i in (1, 2, 3)
    ]
    assert codes == [200, 200, 429]


async def test_bot_telegram_id_spellings_share_bucket(raw_client, owner):
    limiter.configure(api_per_minute=2)
    codes = [
        (await raw_client.get("/auth/me", headers=bot_headers(tg))).status_code
        for tg in (OWNER_TG_ID, f"0{OWNER_TG_ID}", f" {OWNER_TG_ID} ")
    ]
    assert codes == [200, 200, 429]


async def test_cookie_spellings_share_one_bucket(web):
    value = web.cookies.get(SESSION_COOKIE)
    assert value
    limiter.configure(api_per_minute=2)
    web.cookies.clear()
    codes = [
        (await web.get("/auth/me", headers={"Cookie": cookie})).status_code
        for cookie in (
            f"{SESSION_COOKIE}={value}",
            f'{SESSION_COOKIE}="{value}"',
            f"other=1; {SESSION_COOKIE} = {value} ",
        )
    ]
    assert codes == [200, 200, 429]


@pytest.mark.parametrize("limiter_on", [True, False])
async def test_two_session_cookies_are_rejected(web, limiter_on):
    value = web.cookies.get(SESSION_COOKIE)
    limiter.enabled_override = limiter_on
    web.cookies.clear()
    for cookie in (
        f"{SESSION_COOKIE}=junk; {SESSION_COOKIE}={value}",
        f"{SESSION_COOKIE}={value}; {SESSION_COOKIE}={value}",
    ):
        r = await web.get("/auth/me", headers={"Cookie": cookie})
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "bad_request"
    r = await web.post(
        "/users/me/password",
        json={"current_password": PASSWORD, "new_password": "a-brand-new-pass-1"},
        headers={"Cookie": f"{SESSION_COOKIE}=junk; {SESSION_COOKIE}={value}"},
    )
    assert r.status_code == 400
    # a password login still works (it is how a confused browser recovers)
    r = await login(web, headers={"Cookie": f"{SESSION_COOKIE}=a; {SESSION_COOKIE}=b"})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- per-user limits


async def test_ai_limit_is_per_user_across_credentials(web, owner, device_token):
    limiter.configure(ai_per_minute=2)
    for i in range(2):
        r = await web.post(
            "/assistant/messages",
            json={"text": "кофе 85"},
            headers={**csrf_headers(web), "Idempotency-Key": f"cookie-{i}"},
        )
        assert r.status_code != 429, r.text
    web.cookies.clear()
    # fresh credentials of the same user: their own buckets are full, the user's is not
    for headers in (bearer(device_token), bot_headers()):
        r = await web.post(
            "/assistant/messages",
            json={"text": "кофе 85"},
            headers={**headers, "Idempotency-Key": "other"},
        )
        assert r.status_code == 429, r.text
        assert r.json()["error"]["code"] == "rate_limited"
        assert int(r.headers["Retry-After"]) >= 1
    # other endpoints are unaffected
    assert (await web.get("/auth/me", headers=bearer(device_token))).status_code == 200


async def test_61st_ai_message_of_a_user_is_429_with_default_limits(web, owner, device_token):
    """Default RATE_LIMIT_AI_PER_MINUTE=60: 60 messages spent through other credentials
    (the user bucket is drained directly to keep the test fast) → the 61st gets 429."""
    for _ in range(60):
        limiter.take_user_ai(owner.id)
    web.cookies.clear()
    r = await web.post(
        "/assistant/messages",
        json={"text": "кофе 85"},
        headers={**bearer(device_token), "Idempotency-Key": "k61"},
    )
    assert r.status_code == 429, r.text


async def test_wrong_passwords_are_limited_per_user(web, session, owner, device_token):
    limiter.configure(login_failures_per_hour=2, login_per_minute=50)
    await age_sessions(session)
    # one wrong password while adding a passkey, one while changing the password (cookie)
    assert (await register_options(web, "wrong-password-1")).status_code == 401
    r = await web.post(
        "/users/me/password",
        json={"current_password": "wrong-password-2", "new_password": "a-brand-new-pass-1"},
        headers=csrf_headers(web),
    )
    assert r.status_code == 401
    web.cookies.clear()
    # a device token of the same user is a fresh credential, but the user is out of attempts
    for path, body in (
        ("/users/me/password", {"current_password": PASSWORD, "new_password": "a-new-pass-12"}),
        ("/users/me/wipe", {"password": PASSWORD, "confirm": "УДАЛИТЬ"}),
    ):
        r = await web.post(path, json=body, headers=bearer(device_token))
        assert r.status_code == 429, (path, r.text)
        assert r.json()["error"]["code"] == "rate_limited"


async def test_per_user_limits_off_with_the_limiter(web, session, owner, device_token):
    limiter.configure(login_failures_per_hour=1, ai_per_minute=1)
    limiter.enabled_override = False
    for _ in range(3):
        r = await web.post(
            "/users/me/password",
            json={"current_password": "wrong-password-1", "new_password": "a-brand-new-pass-1"},
            headers=bearer(device_token),
        )
        assert r.status_code == 401


# ---------------------------------------------------------------- misc


async def test_wipe_password_is_bounded(web):
    r = await web.post(
        "/users/me/wipe",
        json={"password": "x" * 300, "confirm": "УДАЛИТЬ"},
        headers=csrf_headers(web),
    )
    assert r.status_code == 422


async def test_server_info_bot_username_and_ai_switch(web, monkeypatch):
    base = get_settings()
    monkeypatch.setattr(
        settings_svc,
        "get_settings",
        # model_copy skips validators; config.py strips "@" (tests/test_config_defaults.py)
        lambda: base.model_copy(update={"ai_enabled": False, "telegram_bot_username": "pf_bot"}),
    )
    info = (await web.get("/settings/server")).json()
    assert info["ai_configured"] is False
    assert info["telegram_bot_username"] == "pf_bot"
    assert "allowed_telegram_ids" not in info
    monkeypatch.setattr(
        settings_svc,
        "get_settings",
        lambda: base.model_copy(update={"ai_enabled": True, "telegram_bot_username": ""}),
    )
    info = (await web.get("/settings/server")).json()
    assert info["ai_configured"] is True
    assert info["telegram_bot_username"] is None


async def test_bot_logs_mask_telegram_ids(raw_client, owner, session, caplog):
    stranger = 1234567
    with caplog.at_level(logging.INFO, logger="pocket.auth"):
        r = await raw_client.get("/auth/me", headers=bot_headers(stranger))
        assert r.status_code == 403
        st = await load_settings(session, owner.id)
        st.telegram_user_id = None
        await session.flush()
        assert (await raw_client.get("/auth/me", headers=bot_headers())).status_code == 200
    text = "\n".join(rec.getMessage() for rec in caplog.records if rec.name == "pocket.auth")
    assert "…567" in text and f"…{str(OWNER_TG_ID)[-3:]}" in text
    assert str(stranger) not in text and str(OWNER_TG_ID) not in text
