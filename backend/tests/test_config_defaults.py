"""Neutral, fail-closed configuration defaults (public release).

Nothing in the defaults may point to a particular installation: no AI gateway, model, domain or
Telegram id. Empty env values (``${VAR:-}`` in docker-compose.yml) mean "not set", and a prod
instance refuses to start without an explicit public address and AI settings.
"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.core.security_config import (
    SecuritySettings,
    web_origins,
    webauthn_origins,
    webauthn_rp_id,
)

ENV_VARS = (
    "APP_ENV", "PUBLIC_URL", "WEB_ORIGINS", "DESKTOP_ORIGIN", "COOKIE_SECURE",
    "WEBAUTHN_RP_ID", "WEBAUTHN_RP_NAME", "WEBAUTHN_ORIGINS",
    "AI_ENABLED", "AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_TIMEOUT",
    "TELEGRAM_ALLOWED_IDS", "TELEGRAM_BOT_USERNAME", "DEFAULT_TIMEZONE",
    "SECRET_KEY", "BOT_SERVICE_TOKEN", "TRUSTED_PROXIES", "RATE_LIMIT_ENABLED",
)
PROD_SECRETS = {"secret_key": "s" * 40, "bot_service_token": "b" * 30}
AI = {"ai_base_url": "https://api.example.com/v1", "ai_api_key": "k", "ai_model": "your-model"}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The test conftest pre-sets some variables; start from an empty environment."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


def test_defaults_point_nowhere(clean_env):
    s = _settings()
    assert (s.ai_base_url, s.ai_api_key, s.ai_model) == ("", "", "")
    assert s.ai_enabled is True
    assert s.telegram_allowed_ids == []
    assert s.telegram_bot_username == ""
    assert s.public_url == "http://localhost:5173"
    assert s.web_origins == []
    assert s.webauthn_origins == []
    assert webauthn_rp_id(s) == "localhost"
    assert webauthn_origins(s) == ["http://localhost:5173"]
    assert s.default_timezone == "Asia/Ho_Chi_Minh"


def test_empty_env_values_mean_unset(clean_env):
    """docker-compose passes optional variables as ``${VAR:-}`` → empty strings."""
    for name in ENV_VARS:
        clean_env.setenv(name, "")
    s = _settings()
    assert s.app_env == "dev"
    assert s.cookie_secure is True
    assert s.ai_enabled is True
    assert s.telegram_allowed_ids == []
    assert s.web_origins == [] and s.webauthn_origins == []
    assert s.public_url == "http://localhost:5173"
    assert "webauthn_rp_id" not in s.model_fields_set
    assert "public_url" not in s.model_fields_set
    sec = SecuritySettings(_env_file=None)  # type: ignore[call-arg]
    assert sec.trusted_proxies == ["127.0.0.1/32", "::1/128"]
    assert sec.rate_limit_enabled is None


def test_empty_rp_id_derives_from_public_url(clean_env):
    clean_env.setenv("PUBLIC_URL", "https://pocket.example.com")
    clean_env.setenv("WEBAUTHN_RP_ID", "")
    clean_env.setenv("WEBAUTHN_ORIGINS", "")
    clean_env.setenv("WEB_ORIGINS", "")
    s = _settings()
    assert webauthn_rp_id(s) == "pocket.example.com"
    assert webauthn_origins(s) == ["https://pocket.example.com"]
    # Only the public origin may use cookie sessions — no dev origin sneaks into prod.
    assert web_origins(s) == {"https://pocket.example.com"}
    assert s.cors_origins == ["https://pocket.example.com", "app://pocket"]


def test_explicit_webauthn_settings_win(clean_env):
    clean_env.setenv("PUBLIC_URL", "https://pocket.example.com")
    clean_env.setenv("WEBAUTHN_RP_ID", "example.com")
    clean_env.setenv("WEBAUTHN_ORIGINS", "https://a.example.com/x, https://b.example.com")
    s = _settings()
    assert webauthn_rp_id(s) == "example.com"
    assert webauthn_origins(s) == ["https://a.example.com", "https://b.example.com"]


def test_telegram_allowed_ids_parsing(clean_env):
    clean_env.setenv("TELEGRAM_ALLOWED_IDS", "")
    assert _settings().telegram_allowed_ids == []
    clean_env.setenv("TELEGRAM_ALLOWED_IDS", "1, 2;3")
    assert _settings().telegram_allowed_ids == [1, 2, 3]
    clean_env.setenv("TELEGRAM_ALLOWED_IDS", "123456789")
    assert _settings().telegram_allowed_ids == [123456789]


def test_bot_username_without_at(clean_env):
    clean_env.setenv("TELEGRAM_BOT_USERNAME", " @your_pocket_bot ")
    assert _settings().telegram_bot_username == "your_pocket_bot"
    clean_env.setenv("TELEGRAM_BOT_USERNAME", "not a username!")
    with pytest.raises(ValidationError):
        _settings()


@pytest.mark.parametrize(
    "url",
    [None, "", "http://pocket.example.com", "https://localhost", "https://127.0.0.1:8443",
     "https://pocket.localhost", "pocket.example.com"],
)
def test_prod_requires_public_https_url(clean_env, url):
    kw: dict[str, object] = {"app_env": "prod", **PROD_SECRETS, **AI}
    if url is not None:
        kw["public_url"] = url
    with pytest.raises(RuntimeError, match="PUBLIC_URL"):
        _settings(**kw).check_prod()


def test_prod_requires_ai_or_explicit_opt_out(clean_env):
    base = {"app_env": "prod", "public_url": "https://pocket.example.com", **PROD_SECRETS}
    with pytest.raises(RuntimeError) as e:
        _settings(**base).check_prod()
    msg = str(e.value)
    assert "AI_BASE_URL, AI_API_KEY, AI_MODEL not set" in msg
    assert "AI_ENABLED=false" in msg
    with pytest.raises(RuntimeError, match=r"^AI_MODEL not set"):
        _settings(**base, ai_base_url="https://api.example.com/v1", ai_api_key="k").check_prod()
    _settings(**base, **AI).check_prod()  # complete AI config: starts


def test_prod_starts_with_ai_disabled(clean_env):
    clean_env.setenv("AI_ENABLED", "false")
    s = _settings(app_env="prod", public_url="https://pocket.example.com", **PROD_SECRETS)
    assert s.ai_enabled is False
    s.check_prod()


def test_prod_empty_allow_list_only_warns(clean_env, caplog):
    kw = {"app_env": "prod", "public_url": "https://pocket.example.com", **PROD_SECRETS, **AI}
    with caplog.at_level(logging.WARNING, logger="pocket.config"):
        _settings(**kw).check_prod()  # no bot configured: silent
        assert not caplog.records
        _settings(**kw, telegram_bot_username="your_pocket_bot").check_prod()
    assert any("TELEGRAM_ALLOWED_IDS is empty" in r.getMessage() for r in caplog.records)


def test_prod_keeps_existing_guards(clean_env):
    ok = {"app_env": "prod", "public_url": "https://pocket.example.com", **PROD_SECRETS, **AI}
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _settings(**{**ok, "secret_key": "short"}).check_prod()
    with pytest.raises(RuntimeError, match="BOT_SERVICE_TOKEN"):
        _settings(**{**ok, "bot_service_token": ""}).check_prod()
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        _settings(**ok, cookie_secure=False).check_prod()
    _settings(**ok).check_prod()


def test_dev_and_test_never_refuse(clean_env):
    _settings().check_prod()
    _settings(app_env="test").check_prod()
