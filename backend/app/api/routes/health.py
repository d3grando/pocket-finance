"""GET /api/v1/health — liveness + DB check."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import SessionDep
from app.config import get_settings
from app.core.timeutil import utcnow
from app.schemas.common import Schema

router = APIRouter(tags=["health"])


class HealthOut(Schema):
    status: Literal["ok", "degraded"]
    db: bool
    version: str
    server_time: datetime


@router.get("/health", response_model=HealthOut)
async def health(session: SessionDep) -> HealthOut:
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return HealthOut(
        status="ok" if db_ok else "degraded",
        db=db_ok,
        version=get_settings().app_version,
        server_time=utcnow(),
    )
