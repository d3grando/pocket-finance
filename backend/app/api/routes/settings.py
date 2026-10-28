"""User settings."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import PrincipalDep, SessionDep
from app.schemas.settings import ServerInfoOut, SettingsOut, SettingsPatch
from app.services.auth import settings as settings_svc

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SettingsOut)
async def get_settings_(principal: PrincipalDep, session: SessionDep):
    st = await settings_svc.get_or_create_settings(session, principal.user_id)
    await session.commit()
    return settings_svc.to_out(st)


@router.patch("", response_model=SettingsOut)
async def patch_settings(body: SettingsPatch, principal: PrincipalDep, session: SessionDep):
    """409 version_conflict on stale version. Publishes 'settings' change."""
    st = await settings_svc.patch_settings(
        session, principal.user_id, body, source=principal.source
    )
    out = settings_svc.to_out(st)
    await session.commit()
    return out


@router.get("/server", response_model=ServerInfoOut)
async def server_info(principal: PrincipalDep):
    return settings_svc.server_info()
