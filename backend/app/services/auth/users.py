"""User bootstrap and account management."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import Conflict, Forbidden, Unauthorized, ValidationFailed
from app.core.security import (
    check_password_policy,
    hash_password_async,
    verify_password_async,
)
from app.core.timeutil import utcnow
from app.models import (
    Session,
    User,
    UserSettings,
)
from app.services.auth import devices as devices_svc
from app.services.auth import passkeys as passkeys_svc
from app.services.auth import sessions as sessions_svc
from app.services.auth.settings import get_or_create_settings, new_settings_row

log = logging.getLogger("pocket.auth")

OWNER_USERNAME = "owner"
WIPE_CONFIRM = "УДАЛИТЬ"
#: A cookie session this young may add a passkey without re-entering the password.
FRESH_SESSION = timedelta(seconds=300)
REAUTH_REQUIRED = "Подтвердите паролем, чтобы добавить ключ входа"


def wrong_password() -> Unauthorized:
    """401 with a distinct code so clients do not treat it as «session expired»."""
    return Unauthorized(sessions_svc.WRONG_PASSWORD, code="wrong_password")


async def get_user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized()
    return user


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    return (
        await session.execute(select(User).where(User.username == username.strip()))
    ).scalar_one_or_none()


async def seed_user_data(session: AsyncSession, user_id: int) -> list[str]:
    """Default categories and accounts via ledger seeds (skipped while they are stubs)."""
    done: list[str] = []
    from app.services.accounts import seed as accounts_seed
    from app.services.categories import seed as categories_seed

    for name, fn in (
        ("categories", categories_seed.seed_defaults),
        ("accounts", accounts_seed.seed_default_accounts),
    ):
        await fn(session, user_id)
        done.append(name)
    await session.flush()
    return done


async def _free_telegram_id(session: AsyncSession) -> int | None:
    for tg in get_settings().telegram_allowed_ids:
        used = await session.scalar(
            select(UserSettings.user_id).where(UserSettings.telegram_user_id == tg)
        )
        if used is None:
            return tg
    return None


async def create_user(
    session: AsyncSession,
    username: str,
    *,
    password: str | None = None,
    bind_telegram: bool = False,
    seed: bool = True,
) -> User:
    """New user + settings (+ seeds). 409 if the username exists."""
    username = username.strip()
    if not username or len(username) > 64:
        raise ValidationFailed("Имя пользователя 1–64 символа", details={"field": "username"})
    if await get_user_by_username(session, username) is not None:
        raise Conflict("Такой пользователь уже есть", code="already_exists")
    if password is not None:
        err = check_password_policy(password)
        if err:
            raise ValidationFailed(err, details={"field": "password"})
    user = User(
        username=username,
        password_hash=await hash_password_async(password) if password else None,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    tg = await _free_telegram_id(session) if bind_telegram else None
    session.add(new_settings_row(user.id, telegram_user_id=tg))
    await session.flush()
    if seed:
        await seed_user_data(session, user.id)
    return user


async def ensure_owner(session: AsyncSession, username: str = OWNER_USERNAME) -> User:
    """Create the single user + user_settings if missing; then seed default categories and
    accounts via ledger (categories.seed.seed_defaults, accounts.seed.seed_default_accounts).
    Seeds run only when the user is created. The owner is bound to the first free
    TELEGRAM_ALLOWED_IDS entry. Idempotent."""
    user = await get_user_by_username(session, username)
    if user is None:
        return await create_user(session, username, bind_telegram=True, seed=True)
    st = await get_or_create_settings(session, user.id)
    if st.telegram_user_id is None:
        tg = await _free_telegram_id(session)
        if tg is not None:
            st.telegram_user_id = tg
            st.version = st.version + 1
    return user


async def set_password(session: AsyncSession, user: User, password: str) -> None:
    err = check_password_policy(password)
    if err:
        raise ValidationFailed(err, details={"field": "new_password"})
    user.password_hash = await hash_password_async(password)
    await session.flush()


async def confirm_password_or_fresh_session(
    session: AsyncSession,
    user: User,
    *,
    password: str | None,
    session_id: uuid.UUID | None,
) -> None:
    """Re-authentication before adding a passkey. With ``password``: 401 ``wrong_password``
    unless it is right. Without: allowed only if the cookie session ``session_id`` was created
    ≤ FRESH_SESSION ago (the Face ID offer right after a login), else 403 ``reauth_required``."""
    if password is not None:
        if not await verify_password_async(user.password_hash, password):
            raise wrong_password()
        return
    row = await session.get(Session, session_id) if session_id is not None else None
    if row is None or row.user_id != user.id or utcnow() - row.created_at > FRESH_SESSION:
        raise Forbidden(REAUTH_REQUIRED, code="reauth_required")


async def change_password(
    session: AsyncSession,
    user: User,
    current: str,
    new: str,
    *,
    revoke_others: bool,
    remove_passkeys: bool = True,
    keep_session_id: uuid.UUID | None = None,
    keep_device_id: uuid.UUID | None = None,
) -> None:
    """401 on a wrong current password. ``revoke_others``: every other session AND every other
    device token is revoked (the caller's own session/device stays signed in).
    ``remove_passkeys``: every passkey is deleted (they are re-added with the new password)."""
    if not await verify_password_async(user.password_hash, current):
        raise wrong_password()
    if current == new:
        raise ValidationFailed(
            "Новый пароль совпадает с текущим", details={"field": "new_password"}
        )
    await set_password(session, user, new)
    if revoke_others:
        await sessions_svc.revoke_all_sessions(session, user.id, except_id=keep_session_id)
        await devices_svc.revoke_all_devices(session, user.id, except_id=keep_device_id)
    if remove_passkeys:
        await passkeys_svc.delete_all(session, user.id)


async def logout_everywhere(
    session: AsyncSession,
    user_id: int,
    *,
    keep_session_id: uuid.UUID | None = None,
    keep_device_id: uuid.UUID | None = None,
    remove_passkeys: bool = True,
) -> tuple[int, int, int]:
    """Revoke all sessions and device tokens (optionally except the caller's own) and, unless
    ``remove_passkeys=False``, delete every passkey. Returns (sessions, devices, passkeys)."""
    n_sessions = await sessions_svc.revoke_all_sessions(session, user_id, except_id=keep_session_id)
    n_devices = await devices_svc.revoke_all_devices(session, user_id, except_id=keep_device_id)
    n_passkeys = await passkeys_svc.delete_all(session, user_id) if remove_passkeys else 0
    return n_sessions, n_devices, n_passkeys


async def wipe_financial_data(session: AsyncSession, user: User) -> None:
    """Delete all financial data, re-seed default accounts/categories, reset onboarding
    (delegates to ledger ledger.wipe). Keeps user, settings, sessions, devices, passkeys."""
    from app.services.ledger import wipe as ledger_wipe

    await ledger_wipe.wipe_financial_data(session, user.id)
    log.warning("user %s wiped all financial data", user.id)
