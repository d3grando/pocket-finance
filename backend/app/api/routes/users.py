"""User account."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import HumanPrincipalDep, PasswordUserRateLimit, SessionDep
from app.core.errors import ValidationFailed
from app.core.security import verify_password_async
from app.schemas.common import OkOut
from app.schemas.users import PasswordChangeIn, UserOut, WipeDataIn
from app.services.auth import users as users_svc

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
async def get_me(principal: HumanPrincipalDep, session: SessionDep):
    user = await users_svc.get_user(session, principal.user_id)
    return UserOut.model_validate(user)


@router.post("/me/password", response_model=OkOut, dependencies=[PasswordUserRateLimit])
async def change_password(
    body: PasswordChangeIn, principal: HumanPrincipalDep, session: SessionDep
):
    """401 on a wrong current password. ``revoke_other_sessions`` (default true) signs out every
    other browser session and every other Electron device; ``remove_passkeys`` (default true)
    deletes every passkey."""
    user = await users_svc.get_user(session, principal.user_id)
    await users_svc.change_password(
        session,
        user,
        body.current_password,
        body.new_password,
        revoke_others=body.revoke_other_sessions,
        remove_passkeys=body.remove_passkeys,
        keep_session_id=principal.session_id,
        keep_device_id=principal.device_id,
    )
    await session.commit()
    return OkOut()


@router.post("/me/wipe", response_model=OkOut, dependencies=[PasswordUserRateLimit])
async def wipe(body: WipeDataIn, principal: HumanPrincipalDep, session: SessionDep):
    """Delete all financial data («удаление данных»). confirm must be «УДАЛИТЬ»."""
    if body.confirm.strip().upper() != users_svc.WIPE_CONFIRM:
        raise ValidationFailed(
            "Для подтверждения введите слово «удалить»", details={"field": "confirm"}
        )
    user = await users_svc.get_user(session, principal.user_id)
    if not await verify_password_async(user.password_hash, body.password):
        raise users_svc.wrong_password()
    await users_svc.wipe_financial_data(session, user)
    await session.commit()
    return OkOut()
