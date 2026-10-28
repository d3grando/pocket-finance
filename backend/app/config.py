"""Server configuration from environment (see docs/ARCH.md §11).

Defaults are neutral and fail-closed: nothing points to somebody else's server, an empty
Telegram allow-list means "nobody", and ``check_prod()`` refuses to start a production
instance without an explicit public address and AI settings (or ``AI_ENABLED=false``).

An empty value (``VAR=`` in .env, or ``${VAR:-}`` in docker-compose.yml) means "not set":
the default below applies.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

log = logging.getLogger("pocket.config")

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def _origin(url: str) -> str:
    p = urlsplit(url)
    if not p.scheme or not p.netloc:
        return url.rstrip("/")
    return f"{p.scheme}://{p.netloc}".lower()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"
    app_version: str = "1.0.0"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://pf:pf@127.0.0.1:5432/pf"
    db_pool_size: int = 10

    secret_key: str = "dev-insecure-secret-key-change-me-0123456789"
    # Dev default; prod requires an explicit https:// address (check_prod).
    public_url: str = "http://localhost:5173"
    # Extra origins allowed to use cookie sessions (PUBLIC_URL's origin is always allowed).
    web_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    desktop_origin: str = "app://pocket"
    cookie_secure: bool = True
    session_ttl_days: int = 90

    # Empty = the host of PUBLIC_URL (core/security_config.webauthn_rp_id).
    webauthn_rp_id: str = ""
    webauthn_rp_name: str = "Pocket"
    # Empty = the origin of PUBLIC_URL.
    webauthn_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Any OpenAI-compatible API. No defaults: in prod set all three or AI_ENABLED=false
    # (then only the rule-based parser works).
    ai_enabled: bool = True
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_timeout: float = 20.0
    ai_max_concurrency: int = 4

    bot_service_token: str = ""
    # Telegram user ids allowed to use the bot / receive notifications. Empty = nobody.
    telegram_allowed_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    # Bot username without "@" (the web shows a link to the bot when set).
    telegram_bot_username: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_]*$")

    default_timezone: str = "Asia/Ho_Chi_Minh"
    coingecko_api_key: str = ""
    rates_http_timeout: float = 5.0
    worker_tick_seconds: int = 30

    @model_validator(mode="before")
    @classmethod
    def _empty_means_unset(cls, data: object) -> object:
        """``VAR=`` behaves exactly like an unset variable (default applies, not in fields_set)."""
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and not v.strip())}
        return data

    @field_validator("web_origins", "webauthn_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @field_validator("telegram_allowed_ids", mode="before")
    @classmethod
    def _split_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return [int(x) for x in v.replace(";", ",").split(",") if x.strip()]
        if isinstance(v, int):
            return [v]
        return v

    @field_validator("telegram_bot_username", mode="before")
    @classmethod
    def _strip_at(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().removeprefix("@")
        return v

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def cors_origins(self) -> list[str]:
        out: list[str] = []
        for o in (_origin(self.public_url), *self.web_origins, self.desktop_origin):
            if o and o not in out:
                out.append(o)
        return out

    def check_prod(self) -> None:
        """Fail fast on insecure or incomplete production config."""
        if not self.is_prod:
            return
        if len(self.secret_key) < 32 or self.secret_key.startswith("dev-insecure"):
            raise RuntimeError("SECRET_KEY must be set (>= 32 chars) in prod")
        if not self.bot_service_token or len(self.bot_service_token) < 24:
            raise RuntimeError("BOT_SERVICE_TOKEN must be set (>= 24 chars) in prod")
        if not self.cookie_secure:
            raise RuntimeError("COOKIE_SECURE must be true in prod")
        url = urlsplit(self.public_url.strip())
        host = (url.hostname or "").lower()
        if url.scheme != "https" or not host or host in _LOOPBACK_HOSTS or host.endswith(
            ".localhost"
        ):
            raise RuntimeError(
                "PUBLIC_URL must be the public https:// address of the site in prod "
                "(e.g. https://pocket.example.com)"
            )
        if self.ai_enabled:
            missing = [
                name
                for name, value in (
                    ("AI_BASE_URL", self.ai_base_url),
                    ("AI_API_KEY", self.ai_api_key),
                    ("AI_MODEL", self.ai_model),
                )
                if not value.strip()
            ]
            if missing:
                raise RuntimeError(
                    f"{', '.join(missing)} not set: set AI_BASE_URL/AI_API_KEY/AI_MODEL "
                    "or AI_ENABLED=false"
                )
        if self.telegram_bot_username and not self.telegram_allowed_ids:
            log.warning(
                "TELEGRAM_ALLOWED_IDS is empty: the Telegram bot answers nobody "
                "and no notifications are sent"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
