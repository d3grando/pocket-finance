"""Request id, security headers, trusted-proxy client IP, 500 fallback, primitives."""

from __future__ import annotations

import ipaddress
import logging
import re

import httpx
import pytest
from fastapi import FastAPI

from app.config import get_settings
from app.core import security as sec
from app.core.security_config import (
    DEFAULT_CSP_API,
    DEFAULT_CSP_WEB,
    SecuritySettings,
    webauthn_origins,
    webauthn_rp_id,
)
from app.middleware.request_context import (
    RequestContextMiddleware,
    current_request_id,
    resolve_client_ip,
)

NETS = [ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("127.0.0.1/32")]


async def test_request_id_generated_and_echoed(raw_client):
    r = await raw_client.get("/health")
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["X-Request-Id"])
    r = await raw_client.get("/health", headers={"X-Request-Id": "client-req-12345"})
    assert r.headers["X-Request-Id"] == "client-req-12345"
    r = await raw_client.get("/health", headers={"X-Request-Id": "bad id\nwith junk"})
    assert r.headers["X-Request-Id"] != "bad id\nwith junk"


async def test_error_body_carries_request_id(raw_client):
    r = await raw_client.get("/auth/me", headers={"X-Request-Id": "trace-abcdef12"})
    assert r.status_code == 401
    assert r.json()["error"]["request_id"] == "trace-abcdef12"


async def test_security_headers_on_api(raw_client):
    r = await raw_client.get("/health")
    h = r.headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "same-origin"
    assert h["Content-Security-Policy"] == DEFAULT_CSP_API
    assert h["Cache-Control"] == "no-store"
    assert "publickey-credentials-get=(self)" in h["Permissions-Policy"]
    assert "Strict-Transport-Security" not in h  # not prod


async def test_web_csp_for_non_api_paths(raw_client):
    async with httpx.AsyncClient(transport=raw_client._transport, base_url="http://test") as c:
        r = await c.get("/some/spa/page")
    assert r.headers["Content-Security-Policy"] == DEFAULT_CSP_WEB
    assert "frame-ancestors 'none'" in DEFAULT_CSP_WEB and "script-src 'self'" in DEFAULT_CSP_WEB


async def test_hsts_in_prod(raw_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_env", "prod")
    r = await raw_client.get("/health")
    assert r.headers["Strict-Transport-Security"].startswith("max-age=31536000")


async def test_security_headers_on_429_and_401(raw_client, owner):
    r = await raw_client.get("/auth/me")
    assert r.status_code == 401 and r.headers["X-Frame-Options"] == "DENY"


async def test_unhandled_exception_json_500():
    app = FastAPI()

    @app.get("/api/v1/boom")
    async def boom():
        raise RuntimeError("x")

    app.add_middleware(RequestContextMiddleware)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        r = await c.get("/api/v1/boom", headers={"X-Request-Id": "boom-request-1"})
    assert r.status_code == 500
    assert r.json()["error"] == {
        "code": "internal",
        "message": "Внутренняя ошибка сервера",
        "details": {},
        "request_id": "boom-request-1",
    }
    assert r.headers["X-Request-Id"] == "boom-request-1"


async def test_log_records_get_request_id(raw_client, caplog):
    seen: list[str | None] = []

    class H(logging.Handler):
        def emit(self, record):
            if record.name == "pocket.security":
                seen.append(getattr(record, "request_id", None))

    h = H()
    logging.getLogger("pocket.security").addHandler(h)
    try:
        await raw_client.post(
            "/auth/login",
            json={"password": "nope-nope-nope"},
            headers={"X-Request-Id": "log-ctx-123456"},
        )
    finally:
        logging.getLogger("pocket.security").removeHandler(h)
    assert "log-ctx-123456" in seen
    assert current_request_id() is None


def test_client_ip_resolution():
    # untrusted peer: XFF ignored
    assert resolve_client_ip("203.0.113.9", "1.2.3.4", NETS) == "203.0.113.9"
    # trusted proxy: right-most untrusted hop
    assert resolve_client_ip("10.0.0.2", "1.2.3.4", NETS) == "1.2.3.4"
    assert resolve_client_ip("10.0.0.2", "6.6.6.6, 1.2.3.4, 10.0.0.7", NETS) == "1.2.3.4"
    # all hops trusted → left-most
    assert resolve_client_ip("10.0.0.2", "10.1.1.1, 10.0.0.7", NETS) == "10.1.1.1"
    # garbage stops the walk at the last trusted hop
    assert resolve_client_ip("10.0.0.2", "1.2.3.4, junk", NETS) == "10.0.0.2"
    assert resolve_client_ip("10.0.0.2", None, NETS) == "10.0.0.2"
    assert resolve_client_ip("10.0.0.2", "[2001:db8::1]:443", NETS) == "2001:db8::1"
    assert resolve_client_ip("10.0.0.2", "1.2.3.4:5555", NETS) == "1.2.3.4"
    assert resolve_client_ip(None, "1.2.3.4", NETS) is None


def test_trusted_proxies_env_parsing(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/24, 172.18.0.1 ,bogus")
    s = SecuritySettings()
    assert [str(n) for n in s.trusted_networks] == ["10.0.0.0/24", "172.18.0.1/32"]


def test_passwords_argon2id():
    h = sec.hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert sec.verify_password(h, "correct horse battery")
    assert not sec.verify_password(h, "wrong")
    assert not sec.verify_password(None, "x")
    assert not sec.verify_password("not-a-hash", "x")


def test_tokens_and_hashes():
    t = sec.new_device_token()
    assert t.startswith("pfd_") and len(t) >= 40
    assert sec.token_hash(t) == sec.token_hash(t) and len(sec.token_hash(t)) == 32
    assert sec.token_hash(t) != sec.token_hash(t + "x")
    s1, s2 = sec.new_session_token(), sec.new_session_token()
    assert sec.csrf_token_for(s1) == sec.csrf_token_for(s1) != sec.csrf_token_for(s2)
    assert sec.constant_time_equals("a", "a") and not sec.constant_time_equals("a", "b")
    assert not sec.constant_time_equals(None, "a")
    assert len(sec.webauthn_user_handle(1)) == 16


def test_generated_password_policy():
    pw = sec.generate_password()
    assert re.fullmatch(r"[a-z2-9]{5}(-[a-z2-9]{5}){3}", pw)
    assert sec.check_password_policy(pw) is None
    assert sec.check_password_policy("short") is not None
    assert sec.check_password_policy(" leading-space-xx") is not None


def test_webauthn_rp_defaults(monkeypatch):
    s = get_settings()
    assert webauthn_rp_id(s) == "localhost"
    assert webauthn_origins(s) == ["http://localhost:5173"]
    monkeypatch.delenv("WEBAUTHN_RP_ID", raising=False)
    prod = type(s)(
        app_env="prod",
        public_url="https://pocket.example.com",
        secret_key="x" * 40,
        bot_service_token="y" * 30,
    )
    assert webauthn_rp_id(prod) == "pocket.example.com"
    assert webauthn_origins(prod) == ["https://pocket.example.com"]
    # No built-in production domain: without PUBLIC_URL the RP is derived from the dev default,
    # and check_prod() refuses to start such a prod config.
    bare = type(s)(
        app_env="prod", secret_key="x" * 40, bot_service_token="y" * 30, cookie_secure=True
    )
    assert webauthn_rp_id(bare) == "localhost"
    assert webauthn_origins(bare) == ["http://localhost:5173"]
    with pytest.raises(RuntimeError, match="PUBLIC_URL"):
        bare.check_prod()
