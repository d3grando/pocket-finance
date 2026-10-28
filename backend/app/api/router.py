"""Root API router: every route module is mounted here up-front (builders don't edit this)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    accounts,
    assistant,
    auth,
    categories,
    debts,
    devices,
    events,
    export,
    health,
    history,
    merchants,
    outbox,
    passkeys,
    rates,
    recurring,
    reports,
    search,
    settings,
    stats,
    transactions,
    transfers,
    users,
)

api_router = APIRouter(prefix="/api/v1")

for _m in (
    health,
    auth,
    passkeys,
    devices,
    users,
    settings,
    transactions,
    transfers,
    accounts,
    debts,
    categories,
    merchants,
    history,
    export,
    assistant,
    stats,
    rates,
    events,
    recurring,
    reports,
    search,
    outbox,
):
    api_router.include_router(_m.router)
