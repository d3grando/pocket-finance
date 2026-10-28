"""Electron devices management."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import HumanPrincipalDep, SessionDep
from app.schemas.auth import DeviceOut
from app.schemas.common import OkOut
from app.services.auth import devices as devices_svc

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=list[DeviceOut])
async def list_devices(principal: HumanPrincipalDep, session: SessionDep):
    rows = await devices_svc.list_devices(session, principal.user_id)
    return [
        DeviceOut(
            id=d.id,
            name=d.name,
            platform=d.platform,
            created_at=d.created_at,
            last_used_at=d.last_used_at,
            is_current=d.id == principal.device_id,
        )
        for d in rows
    ]


@router.delete("/{device_id}", response_model=OkOut)
async def revoke_device(device_id: uuid.UUID, principal: HumanPrincipalDep, session: SessionDep):
    """The token stops working immediately (next request of that device → 401)."""
    await devices_svc.revoke_device(session, principal.user_id, device_id)
    await session.commit()
    return OkOut()
