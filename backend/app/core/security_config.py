"""Security-layer settings that are not in app/config.py.

Read from the same environment / .env. All optional; defaults are production-safe.

| env | default | |
|---|---|---|
| TRUSTED_PROXIES | 127.0.0.1/32,::1/128 | IPs/CIDRs whose X-Forwarded-For is trusted |
| RATE_LIMIT_ENABLED | true (false when APP_ENV=test) | master switch |
| RATE_LIMIT_LOGIN_PER_MINUTE | 5 | /auth/login, /auth/device-login per IP |
| RATE_LIMIT_LOGIN_FAILURES_PER_HOUR | 20 | failed password logins per hour, all IPs together; |
|   | | also per user for wrong passwords on /users/me/password, /users/me/wipe and |
|   | | /passkeys/register/options |
| RATE_LIMIT_BAN_THRESHOLD | 10 | failed logins from one IP within RATE_LIMIT_BAN_WINDOW → ban |
| RATE_LIMIT_BAN_WINDOW_SECONDS | 900 | |
| RATE_LIMIT_BAN_SECONDS | 900 | temporary ban length |
| RATE_LIMIT_PASSKEY_PER_MINUTE | 20 | /passkeys/login/* per IP |
| RATE_LIMIT_API_PER_MINUTE | 300 | any other API call per principal (credential) |
| RATE_LIMIT_AI_PER_MINUTE | 60 | POST /assistant/messages per principal and per user |
| RATE_LIMIT_IP_PER_MINUTE | 1200 | flood cap per client IP, all routes |
| RATE_LIMIT_KNOWN_DEVICE_FAILURES_PER_HOUR | 10 | failures/hour of a known browser (cookie |
|   | | ``pf_known``) before it loses its exemption from the global failure cap |
| IDEMPOTENCY_LEASE_SECONDS | max(60, 2·AI_TIMEOUT+20) | age after which an ``in_progress`` |
|   | | Idempotency-Key counts as abandoned (crash mid-request) and a retry takes it over |
| CSP_API / CSP_WEB | see below | Content-Security-Policy for /api and for static web |
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.config import Settings, get_settings

#: CSP for JSON API responses: nothing may be loaded or framed.
DEFAULT_CSP_API = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
#: CSP for the React SPA / PWA (Vite build: no inline scripts; Tailwind/shadcn need inline styles).
#: Infra mirrors this in Caddy for the static web container.
DEFAULT_CSP_WEB = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
    "manifest-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # NoDecode: plain comma-separated env value, not JSON.
    trusted_proxies: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1/32", "::1/128"]
    )

    rate_limit_enabled: bool | None = None  # None → on, except APP_ENV=test
    rate_limit_login_per_minute: int = 5
    rate_limit_login_failures_per_hour: int = 20
    rate_limit_ban_threshold: int = 10
    rate_limit_ban_window_seconds: int = 900
    rate_limit_ban_seconds: int = 900
    rate_limit_passkey_per_minute: int = 20
    rate_limit_api_per_minute: int = 300
    rate_limit_ai_per_minute: int = 60
    rate_limit_ip_per_minute: int = 1200
    rate_limit_known_device_failures_per_hour: int = 10

    idempotency_lease_seconds: int | None = None  # None → max(60, 2*AI_TIMEOUT + 20)

    csp_api: str = DEFAULT_CSP_API
    csp_web: str = DEFAULT_CSP_WEB
    hsts_seconds: int = 31536000

    @model_validator(mode="before")
    @classmethod
    def _empty_means_unset(cls, data: object) -> object:
        """``VAR=`` behaves like an unset variable (same rule as app/config.py)."""
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and not v.strip())}
        return data

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @property
    def trusted_networks(self) -> list[IPNetwork]:
        nets: list[IPNetwork] = []
        for item in self.trusted_proxies:
            try:
                nets.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                continue
        return nets

    def ratelimit_on(self, app: Settings) -> bool:
        if self.rate_limit_enabled is not None:
            return self.rate_limit_enabled
        return app.app_env != "test"


@lru_cache
def get_security_settings() -> SecuritySettings:
    return SecuritySettings()


def origin_of(url: str) -> str:
    """``https://pocket.example.com/x`` → ``https://pocket.example.com``."""
    p = urlsplit(url)
    if not p.scheme or not p.netloc:
        return url.rstrip("/")
    return f"{p.scheme}://{p.netloc}".lower()


def web_origins(s: Settings | None = None) -> set[str]:
    """Origins allowed to use cookie sessions (same-site web/PWA)."""
    s = s or get_settings()
    return {origin_of(s.public_url), *(origin_of(o) for o in s.web_origins)}


def webauthn_rp_id(s: Settings | None = None) -> str:
    """WEBAUTHN_RP_ID if set; else the host of PUBLIC_URL (``https://pocket.example.com`` →
    ``pocket.example.com``). There is no built-in production domain."""
    s = s or get_settings()
    explicit = s.webauthn_rp_id.strip()
    if explicit:
        return explicit
    return urlsplit(s.public_url).hostname or "localhost"


def webauthn_origins(s: Settings | None = None) -> list[str]:
    """WEBAUTHN_ORIGINS if set; else the origin of PUBLIC_URL."""
    s = s or get_settings()
    if s.webauthn_origins:
        return [origin_of(o) for o in s.webauthn_origins]
    return [origin_of(s.public_url)]
