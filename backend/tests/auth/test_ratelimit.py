"""Rate limiting: login 5/min/IP + global failures + temporary ban; API and AI buckets."""

from __future__ import annotations

from app.core.security_config import get_security_settings
from app.middleware.ratelimit import SlidingWindow, TokenBucket, limiter
from tests.auth.conftest import PASSWORD, FakeClock, bearer, login


def _xff(ip: str) -> dict[str, str]:
    return {"X-Forwarded-For": ip}


async def test_login_5_per_minute_then_429(raw_client, owner, rate_limits: FakeClock):
    for _ in range(5):
        assert (await login(raw_client, "wrong-password-1")).status_code == 401
    r = await login(raw_client)  # even the right password is blocked now
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["code"] == "rate_limited"
    assert err["message"] == "Слишком много попыток. Подождите 1 мин"
    assert 1 <= int(r.headers["Retry-After"]) <= 60
    assert err["details"]["retry_after"] == int(r.headers["Retry-After"])
    rate_limits.advance(61)
    assert (await login(raw_client)).status_code == 200


async def test_successful_logins_also_count_per_minute(raw_client, owner):
    for _ in range(5):
        assert (await login(raw_client)).status_code == 200
    assert (await login(raw_client)).status_code == 429


async def test_device_login_shares_login_limit(raw_client, owner):
    body = {"password": "x" * 12, "device_name": "PC", "platform": "windows"}
    for _ in range(5):
        assert (await raw_client.post("/auth/device-login", json=body)).status_code == 401
    assert (await raw_client.post("/auth/device-login", json=body)).status_code == 429


async def test_temporary_ban_after_failures(raw_client, owner, rate_limits: FakeClock):
    limiter.configure(ban_threshold=3, ban_seconds=600, login_per_minute=100)
    for _ in range(3):
        assert (await login(raw_client, "wrong-password-1")).status_code == 401
    r = await login(raw_client)
    assert r.status_code == 429
    assert 590 <= int(r.headers["Retry-After"]) <= 600
    assert "Подождите 10 мин" in r.json()["error"]["message"]
    rate_limits.advance(601)
    assert (await login(raw_client)).status_code == 200


async def test_ban_is_per_ip(raw_client, owner):
    limiter.configure(ban_threshold=2, login_per_minute=100)
    for _ in range(2):
        r = await login(raw_client, "wrong-password-1", headers=_xff("203.0.113.5"))
        assert r.status_code == 401
    assert (await login(raw_client, headers=_xff("203.0.113.5"))).status_code == 429
    assert (await login(raw_client, headers=_xff("203.0.113.6"))).status_code == 200


async def test_success_resets_ip_failures(raw_client, owner):
    limiter.configure(ban_threshold=3, login_per_minute=100)
    for _ in range(2):
        await login(raw_client, "wrong-password-1")
    assert (await login(raw_client)).status_code == 200
    for _ in range(2):
        assert (await login(raw_client, "wrong-password-1")).status_code == 401
    assert (await login(raw_client)).status_code == 200


async def test_global_failure_cap(raw_client, owner, rate_limits: FakeClock):
    limiter.configure(login_failures_per_hour=3, ban_threshold=100)
    for i in range(3):
        r = await login(raw_client, "wrong-password-1", headers=_xff(f"198.51.100.{i + 1}"))
        assert r.status_code == 401
    r = await login(raw_client, headers=_xff("198.51.100.99"))
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 60
    rate_limits.advance(3601)
    assert (await login(raw_client, headers=_xff("198.51.100.99"))).status_code == 200


async def test_xff_ignored_from_untrusted_peer(raw_client, owner, monkeypatch):
    monkeypatch.setattr(get_security_settings(), "trusted_proxies", [])
    for i in range(5):
        r = await login(raw_client, "wrong-password-1", headers=_xff(f"192.0.2.{i}"))
        assert r.status_code == 401
    # spoofed XFF does not give a fresh bucket: the peer (127.0.0.1) is limited
    assert (await login(raw_client, headers=_xff("192.0.2.200"))).status_code == 429


async def test_general_api_limit_per_principal(raw_client, owner, device_token):
    limiter.configure(api_per_minute=3)
    raw_client.cookies.clear()
    for _ in range(3):
        assert (await raw_client.get("/auth/me", headers=bearer(device_token))).status_code == 200
    r = await raw_client.get("/auth/me", headers=bearer(device_token))
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in r.headers
    # another principal has its own bucket
    r = await raw_client.get(
        "/auth/me",
        headers={
            "Authorization": "Bearer test-bot-token-0123456789abcdef",
            "X-Telegram-User-Id": "123456789",
        },
    )
    assert r.status_code == 200


async def test_ai_endpoint_limit(raw_client, owner, device_token):
    limiter.configure(ai_per_minute=2)
    raw_client.cookies.clear()
    h = {**bearer(device_token), "Idempotency-Key": "k1"}
    codes = []
    for _ in range(3):
        r = await raw_client.post("/assistant/messages", json={"text": "кофе 85"}, headers=h)
        codes.append(r.status_code)
    assert codes[0] != 429 and codes[1] != 429
    assert codes[2] == 429
    # other endpoints are still fine
    assert (await raw_client.get("/auth/me", headers=bearer(device_token))).status_code == 200


async def test_health_exempt(raw_client):
    limiter.configure(api_per_minute=1, ip_per_minute=1)
    for _ in range(5):
        assert (await raw_client.get("/health")).status_code == 200


async def test_ip_flood_cap(raw_client, owner):
    limiter.configure(ip_per_minute=4)
    codes = [(await raw_client.get("/auth/me")).status_code for _ in range(6)]
    assert codes[:4] == [401] * 4
    assert codes[4:] == [429, 429]


async def test_disabled_limiter_passes_everything(raw_client, owner):
    limiter.enabled_override = False
    for _ in range(8):
        assert (await login(raw_client, "wrong-password-1")).status_code == 401
    assert (await login(raw_client, PASSWORD)).status_code == 200


def test_token_bucket_refill():
    clock = FakeClock()
    b = TokenBucket(60, 2, clock=clock)
    assert b.take("k") is None and b.take("k") is None
    wait = b.take("k")
    assert wait is not None and 0.9 < wait <= 1.0
    clock.advance(1.0)
    assert b.take("k") is None
    assert b.take("other") is None


def test_sliding_window():
    clock = FakeClock()
    w = SlidingWindow(2, 60, clock=clock)
    assert w.take("a") is None
    clock.advance(10)
    assert w.take("a") is None
    wait = w.take("a")
    assert wait is not None and 49 < wait <= 50
    clock.advance(50.1)
    assert w.take("a") is None
