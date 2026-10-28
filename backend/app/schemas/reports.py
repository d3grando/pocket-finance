"""Reports schemas."""

from __future__ import annotations

from typing import Literal

from app.schemas.common import DateRange, Schema


class ReportOut(Schema):
    kind: Literal["daily", "weekly", "monthly"]
    range: DateRange
    text_html: str  # Telegram HTML (same text the bot sends)
    text_plain: str


class ReportSendOut(Schema):
    outbox_id: int
