"""Bot configuration (env). See docs/ARCH.md §11 and bot/README.md."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = ""
    api_base_url: str = "http://api:8000/api/v1"
    bot_service_token: str = ""
    # Telegram user ids the bot answers: "123456789" or "1,2" (comma/semicolon separated, not
    # JSON). Empty = the bot answers nobody (fail-closed).
    telegram_allowed_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    # Public web address for "open in the app" buttons; empty = no link buttons.
    public_url: str = ""
    timezone: str = "Asia/Ho_Chi_Minh"
    outbox_poll_seconds: float = 5.0
    outbox_batch: int = 20
    api_timeout: float = 30.0
    max_text_len: int = 2000
    log_level: str = "INFO"

    @field_validator("telegram_allowed_ids", mode="before")
    @classmethod
    def _ids(cls, v: object) -> object:
        if isinstance(v, str):
            return [int(x) for x in v.replace(";", ",").split(",") if x.strip()]
        if isinstance(v, int):
            return [v]
        return v


@lru_cache
def get_settings() -> BotSettings:
    return BotSettings()
