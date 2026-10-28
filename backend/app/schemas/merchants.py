"""Merchant rule API schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas.common import Aliases, Id, Schema
from app.schemas.transactions import CleanInput


class MerchantOut(Schema):
    id: int
    name: str
    aliases: list[str]
    handle: str | None = None
    category_id: int | None = None
    category_name: str | None = None
    default_account_id: int | None = None
    hits: int
    last_used_at: datetime | None = None


#: «@shop_name» / «shop_name»: the handle goes into the LLM prompt, so only a Telegram-like
#: name (no markers, quotes or newlines).
HANDLE_PATTERN = r"^(?:@?[A-Za-z0-9_]{1,64})?$"  # "" clears the handle


class MerchantCreate(CleanInput):
    name: str = Field(min_length=1, max_length=128)
    aliases: Aliases = Field(default_factory=list)
    handle: str | None = Field(default=None, max_length=65, pattern=HANDLE_PATTERN)
    category_id: Id | None = None
    default_account_id: Id | None = None


class MerchantPatch(CleanInput):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    aliases: Aliases | None = None
    handle: str | None = Field(default=None, max_length=65, pattern=HANDLE_PATTERN)
    category_id: Id | None = None
    default_account_id: Id | None = None
