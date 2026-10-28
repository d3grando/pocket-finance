"""Export REST. Files are attachments; filters = GET /transactions params."""

from __future__ import annotations

from typing import Literal

import anyio
from fastapi import APIRouter
from fastapi.responses import Response

from app.api.deps import PrincipalDep, SessionDep
from app.api.routes.transactions import FilterDep
from app.core.timeutil import local_today
from app.schemas.export import FullExport
from app.services.ledger import export as exporter
from app.services.ledger.common import user_tz

router = APIRouter(tags=["export"])


@router.get("/export/transactions")
async def export_transactions(
    principal: PrincipalDep,
    session: SessionDep,
    f: FilterDep,
    format: Literal["csv", "xlsx"] = "csv",
):
    """Response with Content-Disposition: attachment;
    filename="pocket-transactions-2026-10-27.csv". limit/offset ignored (all rows)."""
    content, media, filename = await exporter.export_transactions(
        session, principal.user_id, f, format
    )
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/full", response_model=FullExport)
async def export_full(principal: PrincipalDep, session: SessionDep):
    """All data as JSON (attachment pocket-export-YYYY-MM-DD.json)."""
    data = await exporter.export_all_json(session, principal.user_id)
    day = local_today(await user_tz(session, principal.user_id)).isoformat()
    # serialising a large export is CPU work: keep it off the event loop
    content = await anyio.to_thread.run_sync(lambda: data.model_dump_json(indent=2))
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="pocket-export-{day}.json"'},
    )
