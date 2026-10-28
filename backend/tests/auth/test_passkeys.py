"""Passkeys / Face ID with a software authenticator (real py_webauthn verification)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.core.security import CSRF_COOKIE, SESSION_COOKIE
from app.models import AuthChallenge, Passkey
from tests.auth.conftest import bearer, csrf_headers
from tests.auth.softwebauthn import SoftAuthenticator

ORIGIN = "http://localhost:5173"  # PUBLIC_URL default → RP ID "localhost"


async def register(client: httpx.AsyncClient, auth: SoftAuthenticator, name="iPhone") -> dict:
    r = await client.post("/passkeys/register/options", headers=csrf_headers(client))
    assert r.status_code == 200, r.text
    data = r.json()
    cred = auth.create(data["options"], origin=ORIGIN)
    r = await client.post(
        "/passkeys/register/verify",
        json={"challenge_id": data["challenge_id"], "credential": cred, "name": name},
        headers=csrf_headers(client),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def login_options(client: httpx.AsyncClient, username: str = "owner") -> dict:
    r = await client.post("/passkeys/login/options", json={"username": username})
    assert r.status_code == 200, r.text
    return r.json()


async def test_registration_options_shape(web):
    r = await web.post("/passkeys/register/options", headers=csrf_headers(web))
    assert r.status_code == 200
    o = r.json()["options"]
    assert o["rp"]["id"] == "localhost"
    assert o["authenticatorSelection"]["userVerification"] == "required"
    assert o["authenticatorSelection"]["residentKey"] == "preferred"
    assert o["attestation"] == "none"
    assert o["user"]["name"] == "owner"
    assert len(o["challenge"]) >= 40


async def test_register_and_login_with_face_id(web, raw_client, session, owner):
    auth = SoftAuthenticator()
    pk = await register(web, auth)
    assert pk["name"] == "iPhone" and pk["last_used_at"] is None
    row = (await session.execute(select(Passkey))).scalar_one()
    assert row.credential_id == auth.credential_id
    assert set(row.transports) == {"internal", "hybrid"}
    assert (await web.get("/auth/me")).json()["has_passkeys"] is True

    # Log out completely, then log in with Face ID instead of the password.
    await web.post("/auth/logout", headers=csrf_headers(web))
    web.cookies.clear()
    data = await login_options(web)
    allow = data["options"]["allowCredentials"]
    assert [c["id"] for c in allow] == [auth.credential_id_b64]
    assert data["options"]["userVerification"] == "required"
    assertion = auth.get(data["options"], origin=ORIGIN)
    r = await web.post(
        "/passkeys/login/verify",
        json={"challenge_id": data["challenge_id"], "credential": assertion},
    )
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["auth"] == "session" and me["has_passkeys"] is True
    assert me["csrf_token"] == web.cookies.get(CSRF_COOKIE)
    assert web.cookies.get(SESSION_COOKIE)
    assert (await web.get("/auth/me")).status_code == 200
    await session.refresh(row)
    assert row.sign_count == 1 and row.last_used_at is not None

    # Replaying the same assertion / challenge fails.
    r = await web.post(
        "/passkeys/login/verify",
        json={"challenge_id": data["challenge_id"], "credential": assertion},
    )
    assert r.status_code == 401


async def test_apple_style_zero_counter(web, owner):
    auth = SoftAuthenticator(counter=False)
    await register(web, auth)
    for _ in range(2):
        data = await login_options(web)
        r = await web.post(
            "/passkeys/login/verify",
            json={
                "challenge_id": data["challenge_id"],
                "credential": auth.get(data["options"], origin=ORIGIN),
            },
        )
        assert r.status_code == 200, r.text


async def test_wrong_origin_rejected(web, owner):
    auth = SoftAuthenticator()
    await register(web, auth)
    data = await login_options(web)
    r = await web.post(
        "/passkeys/login/verify",
        json={
            "challenge_id": data["challenge_id"],
            "credential": auth.get(data["options"], origin="https://evil.example"),
        },
    )
    assert r.status_code == 401


async def test_without_user_verification_rejected(web, owner):
    auth = SoftAuthenticator()
    await register(web, auth)
    data = await login_options(web)
    r = await web.post(
        "/passkeys/login/verify",
        json={
            "challenge_id": data["challenge_id"],
            "credential": auth.get(data["options"], origin=ORIGIN, uv=False),
        },
    )
    assert r.status_code == 401


async def test_registration_without_uv_rejected(web):
    auth = SoftAuthenticator()
    r = await web.post("/passkeys/register/options", headers=csrf_headers(web))
    data = r.json()
    cred = auth.create(data["options"], origin=ORIGIN, uv=False)
    r = await web.post(
        "/passkeys/register/verify",
        json={"challenge_id": data["challenge_id"], "credential": cred},
        headers=csrf_headers(web),
    )
    assert r.status_code == 422


async def test_unknown_credential_and_bad_signature(web, owner):
    real = SoftAuthenticator()
    await register(web, real)
    stranger = SoftAuthenticator()
    stranger.user_handle = real.user_handle
    data = await login_options(web)
    r = await web.post(
        "/passkeys/login/verify",
        json={
            "challenge_id": data["challenge_id"],
            "credential": stranger.get(data["options"], origin=ORIGIN),
        },
    )
    assert r.status_code == 401
    # right credential id, wrong key → signature check fails
    stranger.credential_id = real.credential_id
    data = await login_options(web)
    r = await web.post(
        "/passkeys/login/verify",
        json={
            "challenge_id": data["challenge_id"],
            "credential": stranger.get(data["options"], origin=ORIGIN),
        },
    )
    assert r.status_code == 401


async def test_expired_challenge_is_410(web, owner, session):
    auth = SoftAuthenticator()
    await register(web, auth)
    data = await login_options(web)
    ch = await session.get(AuthChallenge, __import__("uuid").UUID(data["challenge_id"]))
    ch.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    r = await web.post(
        "/passkeys/login/verify",
        json={
            "challenge_id": data["challenge_id"],
            "credential": auth.get(data["options"], origin=ORIGIN),
        },
    )
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "gone"


async def test_login_challenge_cannot_register(web):
    auth = SoftAuthenticator()
    data = await login_options(web)
    reg = await web.post("/passkeys/register/options", headers=csrf_headers(web))
    cred = auth.create(reg.json()["options"], origin=ORIGIN)
    r = await web.post(
        "/passkeys/register/verify",
        json={"challenge_id": data["challenge_id"], "credential": cred},
        headers=csrf_headers(web),
    )
    assert r.status_code == 401


async def test_duplicate_registration_conflict(web):
    auth = SoftAuthenticator()
    await register(web, auth)
    r = await web.post("/passkeys/register/options", headers=csrf_headers(web))
    data = r.json()
    assert [c["id"] for c in data["options"]["excludeCredentials"]] == [auth.credential_id_b64]
    r = await web.post(
        "/passkeys/register/verify",
        json={
            "challenge_id": data["challenge_id"],
            "credential": auth.create(data["options"], origin=ORIGIN),
        },
        headers=csrf_headers(web),
    )
    assert r.status_code == 409


async def test_unknown_user_gets_empty_allow_list(raw_client, owner):
    data = await login_options(raw_client, "ghost")
    assert data["options"].get("allowCredentials", []) == []


async def test_register_requires_cookie_session(raw_client, device_token):
    raw_client.cookies.clear()
    r = await raw_client.post("/passkeys/register/options", headers=bearer(device_token))
    assert r.status_code == 403
    r = await raw_client.post("/passkeys/register/options")
    assert r.status_code == 401


async def test_register_requires_csrf(web):
    r = await web.post("/passkeys/register/options")
    assert r.status_code == 403


async def test_rename_list_delete(web, owner):
    auth = SoftAuthenticator()
    pk = await register(web, auth)
    r = await web.patch(
        f"/passkeys/{pk['id']}", json={"name": "Мой iPhone"}, headers=csrf_headers(web)
    )
    assert r.status_code == 200 and r.json()["name"] == "Мой iPhone"
    items = (await web.get("/passkeys")).json()
    assert [i["name"] for i in items] == ["Мой iPhone"]
    r = await web.delete(f"/passkeys/{pk['id']}", headers=csrf_headers(web))
    assert r.status_code == 200
    assert (await web.get("/passkeys")).json() == []
    assert (await web.get("/auth/me")).json()["has_passkeys"] is False
    # deleted key can no longer log in
    data = await login_options(web)
    r = await web.post(
        "/passkeys/login/verify",
        json={
            "challenge_id": data["challenge_id"],
            "credential": auth.get(data["options"], origin=ORIGIN),
        },
    )
    assert r.status_code == 401
    r = await web.delete(f"/passkeys/{pk['id']}", headers=csrf_headers(web))
    assert r.status_code == 404


async def test_garbage_credentials_do_not_500(web, owner):
    r = await web.post("/passkeys/register/options", headers=csrf_headers(web))
    cid = r.json()["challenge_id"]
    for cred in (
        {"id": "x"},
        {
            "id": "AAAA",
            "rawId": "AAAA",
            "type": "public-key",
            "response": {"clientDataJSON": "!!", "attestationObject": "@@"},
        },
    ):
        r = await web.post(
            "/passkeys/register/verify",
            json={"challenge_id": cid, "credential": cred},
            headers=csrf_headers(web),
        )
        assert r.status_code in (401, 422), r.text
        r = await web.post("/passkeys/register/options", headers=csrf_headers(web))
        cid = r.json()["challenge_id"]
    data = await login_options(web)
    for cred in ({"id": "!!!"}, {}, {"rawId": 5}):
        r = await web.post(
            "/passkeys/login/verify",
            json={"challenge_id": data["challenge_id"], "credential": cred},
        )
        assert r.status_code == 401, r.text
        data = await login_options(web)
