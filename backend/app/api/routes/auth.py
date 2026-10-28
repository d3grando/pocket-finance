"""Login/logout/me/device-login/sessions. Rate-limited (ARCH §6)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, Response

from app.api.deps import HumanPrincipalDep, PrincipalDep, SessionDep, SessionPrincipalDep
from app.core.principal import Principal
from app.core.security import CSRF_COOKIE, constant_time_equals, csrf_token_for
from app.core.security_cookies import set_csrf_cookie
from app.schemas.auth import (
    DeviceLoginIn,
    DeviceLoginOut,
    LoginIn,
    LogoutAllOut,
    MeOut,
    SessionOut,
)
from app.schemas.common import OkOut
from app.services.auth import devices as devices_svc
from app.services.auth import passkeys as passkeys_svc
from app.services.auth import sessions as sessions_svc
from app.services.auth import users as users_svc
from app.services.auth.principal import client_ip
from app.services.auth.settings import load_settings

router = APIRouter(prefix="/auth", tags=["auth"])


async def build_me(
    session: SessionDep, principal: Principal, *, csrf_token: str | None = None
) -> MeOut:
    user = await users_svc.get_user(session, principal.user_id)
    st = await load_settings(session, user.id)
    source = principal.source if principal.source != "system" else "web"
    kind = principal.kind if principal.kind != "system" else "session"
    return MeOut(
        user_id=user.id,
        username=user.username,
        auth=kind,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        onboarding_done=bool(st.onboarding_done) if st is not None else False,
        has_passkeys=await passkeys_svc.count_passkeys(session, user.id) > 0,
        csrf_token=csrf_token if principal.kind == "session" else None,
    )


async def start_web_session(
    request: Request, response: Response, session: SessionDep, user_id: int
) -> MeOut:
    """Shared by password login and passkey login: session row + cookies + MeOut."""
    user = await users_svc.get_user(session, user_id)
    row, raw, csrf = await sessions_svc.create_session(
        session, user, user_agent=request.headers.get("user-agent"), ip=client_ip(request)
    )
    await session.commit()
    sessions_svc.set_session_cookies(response, raw, csrf)
    principal = Principal(user_id=user.id, kind="session", source="web", session_id=row.id)
    return await build_me(session, principal, csrf_token=csrf)


@router.post("/login", response_model=MeOut)
async def login(body: LoginIn, request: Request, response: Response, session: SessionDep):
    """Sets pf_session + pf_csrf cookies; returns MeOut with csrf_token. 401 on bad password."""
    user = await sessions_svc.authenticate_password(session, body.username, body.password)
    return await start_web_session(request, response, session, user.id)


@router.post("/logout", response_model=OkOut)
async def logout(
    principal: PrincipalDep, request: Request, response: Response, session: SessionDep
):
    """Session: revoke + clear cookies. Device: revoke this device token. Bot: no-op."""
    if principal.kind == "session" and principal.session_id is not None:
        await sessions_svc.revoke_session(session, principal.user_id, principal.session_id)
        await session.commit()
        sessions_svc.clear_session_cookies(response)
    elif principal.kind == "device" and principal.device_id is not None:
        await devices_svc.revoke_device(session, principal.user_id, principal.device_id)
        await session.commit()
    return OkOut()


@router.post("/logout-all", response_model=LogoutAllOut)
async def logout_all(
    principal: HumanPrincipalDep,
    response: Response,
    session: SessionDep,
    keep_current: bool = Query(default=False, description="не выходить на этом устройстве"),
    remove_passkeys: bool = Query(default=True, description="удалить и все ключи входа (passkey)"),
):
    """«Выйти со всех устройств»: revoke every web session and every Electron device token and,
    unless ``remove_passkeys=false``, delete every passkey (a key added from a stolen session
    must not survive the sign-out)."""
    n_sessions, n_devices, n_passkeys = await users_svc.logout_everywhere(
        session,
        principal.user_id,
        keep_session_id=principal.session_id if keep_current else None,
        keep_device_id=principal.device_id if keep_current else None,
        remove_passkeys=remove_passkeys,
    )
    await session.commit()
    if principal.kind == "session" and not keep_current:
        sessions_svc.clear_session_cookies(response)
    return LogoutAllOut(
        sessions_revoked=n_sessions, devices_revoked=n_devices, passkeys_removed=n_passkeys
    )


@router.get("/me", response_model=MeOut)
async def me(principal: PrincipalDep, request: Request, response: Response, session: SessionDep):
    csrf: str | None = None
    if principal.kind == "session":
        raw = getattr(request.state, "session_token", None)
        if raw:
            csrf = csrf_token_for(raw)
            # Re-issue the readable CSRF cookie if the browser lost it.
            if not constant_time_equals(request.cookies.get(CSRF_COOKIE), csrf):
                set_csrf_cookie(response, csrf)
    return await build_me(session, principal, csrf_token=csrf)


@router.post("/device-login", response_model=DeviceLoginOut, status_code=201)
async def device_login(body: DeviceLoginIn, request: Request, session: SessionDep):
    user = await sessions_svc.authenticate_password(session, body.username, body.password)
    row, raw = await devices_svc.create_device_token(
        session, user, name=body.device_name, platform=body.platform, ip=client_ip(request)
    )
    await session.commit()
    return DeviceLoginOut(token=raw, device_id=row.id)


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(principal: SessionPrincipalDep, session: SessionDep):
    rows = await sessions_svc.list_sessions(session, principal.user_id)
    return [
        SessionOut(
            id=r.id,
            user_agent=r.user_agent,
            ip=str(r.ip) if r.ip is not None else None,
            created_at=r.created_at,
            last_seen_at=r.last_seen_at,
            expires_at=r.expires_at,
            is_current=r.id == principal.session_id,
        )
        for r in rows
    ]


@router.delete("/sessions/{session_id}", response_model=OkOut)
async def revoke_session(
    session_id: uuid.UUID, principal: SessionPrincipalDep, response: Response, session: SessionDep
):
    await sessions_svc.revoke_session(session, principal.user_id, session_id)
    await session.commit()
    if session_id == principal.session_id:
        sessions_svc.clear_session_cookies(response)
    return OkOut()
