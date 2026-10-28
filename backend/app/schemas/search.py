"""Global quick search (command palette) schemas."""

from __future__ import annotations

from typing import Literal

from app.schemas.common import Schema


class SearchHit(Schema):
    type: Literal["transaction", "category", "account", "merchant"]
    id: int
    title: str
    subtitle: str | None = None
    route: str  # client route to open, e.g. "/operations?tx=123"


class SearchOut(Schema):
    query: str
    hits: list[SearchHit]
