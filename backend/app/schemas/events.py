"""SSE event payloads."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import Schema

Entity = Literal[
    "transactions", "accounts", "categories", "merchants", "settings", "recurring", "stats",
    "debts",  # docs/DEBTS.md (0005); old clients ignore unknown entities
]


class ChangeEvent(Schema):
    """SSE ``event: change``. Also the JSON payload of pg_notify('pf_events', …)."""

    user_id: int = Field(exclude=True)
    entities: list[Entity]
    action_id: uuid.UUID | None = None
    source: str | None = None
    ts: datetime


class HelloEvent(Schema):
    server_time: datetime
    version: str
