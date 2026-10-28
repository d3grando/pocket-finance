"""Reports REST."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter

from app.api.deps import PrincipalDep, SessionDep
from app.core.errors import ValidationFailed
from app.core.timeutil import local_today
from app.models import UserSettings
from app.schemas.common import SafeDate
from app.schemas.reports import ReportOut, ReportSendOut
from app.services.events import outbox
from app.services.reports import service as reports

router = APIRouter(prefix="/reports", tags=["reports"])

Kind = Literal["daily", "weekly", "monthly"]


async def _on(session, user_id: int, on: date | None) -> date:  # type: ignore[no-untyped-def]
    if on is not None:
        return on
    st = await session.get(UserSettings, user_id)
    return local_today(st.timezone if st else None)


@router.get("/{kind}", response_model=ReportOut)
async def get_report(
    kind: Kind, principal: PrincipalDep, session: SessionDep, on: SafeDate | None = None
):
    """monthly: ``on`` = 1st → the previous month, otherwise the month of ``on`` to date;
    weekly: Monday → the previous week, otherwise the week to date; daily: the day ``on``."""
    day = await _on(session, principal.user_id, on)
    return await reports.build_report(session, principal.user_id, kind, day)


@router.post("/{kind}/send", response_model=ReportSendOut, status_code=202)
async def send_report(
    kind: Kind, principal: PrincipalDep, session: SessionDep, on: SafeDate | None = None
):
    """Enqueue the report to Telegram (outbox) now. 422 when there is no Telegram chat to send
    to (the bot was never started) — instead of a 202 that silently fails in the outbox."""
    if await outbox.delivery_target(session, principal.user_id) is None:
        raise ValidationFailed(
            "Telegram не привязан — сначала напишите боту /start, потом повторите",
            details={"field": "telegram_user_id"},
        )
    day = await _on(session, principal.user_id, on)
    _, payload = await reports.report_payload(session, principal.user_id, kind, day)
    item = await outbox.enqueue(session, principal.user_id, f"report_{kind}", payload)
    assert item is not None  # no dedupe key → always inserted
    await session.commit()
    return ReportSendOut(outbox_id=item.id)
