"""Passkeys / Face ID."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Request, Response

from app.api.deps import (
    HumanPrincipalDep,
    PasswordUserRateLimit,
    SessionDep,
    SessionPrincipalDep,
)
from app.api.routes.auth import start_web_session
from app.schemas.auth import (
    MeOut,
    PasskeyLoginOptionsIn,
    PasskeyLoginVerifyIn,
    PasskeyOut,
    PasskeyRegisterOptionsIn,
    PasskeyRegisterVerifyIn,
    PasskeyRename,
    WebAuthnOptionsOut,
)
from app.schemas.common import OkOut
from app.services.auth import passkeys as passkeys_svc
from app.services.auth import users as users_svc

router = APIRouter(prefix="/passkeys", tags=["passkeys"])


@router.post(
    "/register/options", response_model=WebAuthnOptionsOut, dependencies=[PasswordUserRateLimit]
)
async def register_options(
    principal: SessionPrincipalDep,
    session: SessionDep,
    body: Annotated[PasskeyRegisterOptionsIn | None, Body()] = None,
):
    """Only from an active cookie session (ARCH §6). Needs ``password`` unless the session was
    created ≤ 5 min ago: 403 ``reauth_required`` without it, 401 ``wrong_password`` if wrong
    (the same 20/h failure limits as /users/me/password)."""
    user = await users_svc.get_user(session, principal.user_id)
    await users_svc.confirm_password_or_fresh_session(
        session,
        user,
        password=body.password if body is not None else None,
        session_id=principal.session_id,
    )
    challenge_id, options = await passkeys_svc.registration_options(session, user)
    await session.commit()
    return WebAuthnOptionsOut(challenge_id=challenge_id, options=options)


@router.post("/register/verify", response_model=PasskeyOut, status_code=201)
async def register_verify(
    body: PasskeyRegisterVerifyIn,
    principal: SessionPrincipalDep,
    request: Request,
    session: SessionDep,
):
    """The challenge comes from /register/options (re-authenticated there). On success the
    user gets a Telegram notice about the new key."""
    user = await users_svc.get_user(session, principal.user_id)
    pk = await passkeys_svc.verify_registration(
        session, user, body.challenge_id, body.credential, body.name
    )
    await passkeys_svc.notify_passkey_added(
        session, user.id, pk, request.headers.get("user-agent")
    )
    await session.commit()
    return PasskeyOut.model_validate(pk)


@router.post("/login/options", response_model=WebAuthnOptionsOut)
async def login_options(body: PasskeyLoginOptionsIn, session: SessionDep):
    challenge_id, options = await passkeys_svc.authentication_options(session, body.username)
    await session.commit()
    return WebAuthnOptionsOut(challenge_id=challenge_id, options=options)


@router.post("/login/verify", response_model=MeOut)
async def login_verify(
    body: PasskeyLoginVerifyIn, request: Request, response: Response, session: SessionDep
):
    """Same cookies as /auth/login."""
    user = await passkeys_svc.verify_authentication(session, body.challenge_id, body.credential)
    return await start_web_session(request, response, session, user.id)


@router.get("", response_model=list[PasskeyOut])
async def list_passkeys(principal: HumanPrincipalDep, session: SessionDep):
    return [
        PasskeyOut.model_validate(pk)
        for pk in await passkeys_svc.list_passkeys(session, principal.user_id)
    ]


@router.patch("/{passkey_id}", response_model=PasskeyOut)
async def rename_passkey(
    passkey_id: uuid.UUID, body: PasskeyRename, principal: HumanPrincipalDep, session: SessionDep
):
    pk = await passkeys_svc.rename_passkey(session, principal.user_id, passkey_id, body.name)
    await session.commit()
    return PasskeyOut.model_validate(pk)


@router.delete("/{passkey_id}", response_model=OkOut)
async def delete_passkey(passkey_id: uuid.UUID, principal: HumanPrincipalDep, session: SessionDep):
    await passkeys_svc.delete_passkey(session, principal.user_id, passkey_id)
    await session.commit()
    return OkOut()
