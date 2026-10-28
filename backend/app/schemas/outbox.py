"""Bot outbox schemas. Consumed by bot/ via HTTP."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import Schema, StrictInput


class OutboxButton(Schema):
    text: str
    callback: str = Field(max_length=64, description="Telegram callback_data, see API.md §Bot")


class OutboxPayload(Schema):
    text: str
    parse_mode: Literal["HTML"] = "HTML"
    buttons: list[list[OutboxButton]] = Field(default_factory=list)


class OutboxItem(Schema):
    id: int
    kind: str
    telegram_user_id: int
    payload: OutboxPayload
    attempts: int


class OutboxClaimOut(Schema):
    items: list[OutboxItem]


class OutboxAckIn(StrictInput):
    ok: bool
    error: str | None = Field(default=None, max_length=1000)
    retry: bool = True
