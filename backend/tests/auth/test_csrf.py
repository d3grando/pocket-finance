"""CSRF: signed double-submit for cookie sessions; Bearer/bot are exempt."""

from __future__ import annotations

from app.core.security import CSRF_COOKIE
from tests.auth.conftest import bearer, cookie_header, csrf_headers


async def _settings_version(client, headers=None) -> int:
    r = await client.get("/settings", headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["version"]


async def test_unsafe_without_token_is_403(web):
    v = await _settings_version(web)
    r = await web.patch("/settings", json={"version": v, "theme": "dark"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"


async def test_wrong_token_is_403(web):
    v = await _settings_version(web)
    r = await web.patch(
        "/settings", json={"version": v, "theme": "dark"}, headers={"X-CSRF-Token": "forged"}
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"


async def test_header_must_match_cookie(web):
    good = web.cookies.get(CSRF_COOKIE)
    web.cookies.set(CSRF_COOKIE, "something-else")
    v = await _settings_version(web)
    r = await web.patch(
        "/settings", json={"version": v, "theme": "dark"}, headers={"X-CSRF-Token": good}
    )
    assert r.status_code == 403


async def test_token_of_other_session_rejected(web):
    """A valid-looking token from another session must not work (bound to the session)."""
    import httpx

    from app.main import app
    from tests.auth.conftest import login

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1"
    ) as other:
        await login(other)
        foreign = other.cookies.get(CSRF_COOKIE)
    web.cookies.set(CSRF_COOKIE, foreign)
    v = await _settings_version(web)
    r = await web.patch(
        "/settings", json={"version": v, "theme": "dark"}, headers={"X-CSRF-Token": foreign}
    )
    assert r.status_code == 403


async def test_valid_token_passes(web):
    v = await _settings_version(web)
    r = await web.patch(
        "/settings", json={"version": v, "theme": "dark"}, headers=csrf_headers(web)
    )
    assert r.status_code == 200, r.text
    assert r.json()["theme"] == "dark"


async def test_foreign_origin_rejected_even_with_token(web):
    v = await _settings_version(web)
    h = {**csrf_headers(web), "Origin": "https://evil.example"}
    r = await web.patch("/settings", json={"version": v, "theme": "dark"}, headers=h)
    assert r.status_code == 403
    assert r.json()["error"]["details"]["reason"] == "origin"


async def test_same_origin_header_allowed(web):
    v = await _settings_version(web)
    h = {**csrf_headers(web), "Origin": "http://localhost:5173"}
    r = await web.patch("/settings", json={"version": v, "theme": "light"}, headers=h)
    assert r.status_code == 200, r.text


async def test_get_needs_no_csrf(web):
    assert (await web.get("/settings")).status_code == 200


async def test_bearer_device_needs_no_csrf(raw_client, device_token):
    raw_client.cookies.clear()
    h = bearer(device_token)
    v = await _settings_version(raw_client, h)
    r = await raw_client.patch("/settings", json={"version": v, "theme": "dark"}, headers=h)
    assert r.status_code == 200, r.text


async def test_bearer_wins_over_cookie(web, device_token):
    """With Authorization present the cookie is ignored → no CSRF needed, auth=device."""
    r = await web.post("/auth/logout", headers=bearer(device_token))
    assert r.status_code == 200
    # device revoked, web session still alive
    assert (await web.get("/auth/me", headers=bearer(device_token))).status_code == 401
    assert (await web.get("/auth/me")).status_code == 200


async def test_me_reissues_lost_csrf_cookie(web):
    good = web.cookies.get(CSRF_COOKIE)
    web.cookies.delete(CSRF_COOKIE)
    r = await web.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["csrf_token"] == good
    assert cookie_header(r, CSRF_COOKIE).startswith(f"{CSRF_COOKIE}={good}")
