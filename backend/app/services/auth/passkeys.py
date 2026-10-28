"""WebAuthn passkeys / Face ID. Library: py_webauthn (``webauthn``).

* RP ID / origins: ``core/security_config.webauthn_rp_id/webauthn_origins`` (WEBAUTHN_RP_ID,
  WEBAUTHN_ORIGINS; default host of PUBLIC_URL).
* ``userVerification=required`` (Face ID), resident key preferred, attestation none.
* Challenges live in ``auth_challenges`` (5 min, single use).
* Login by passkey replaces the password and yields the same cookie session.
* Adding a passkey needs the password unless the session is fresh
  (``users.confirm_password_or_fresh_session``), sends a Telegram notice
  (``notify_passkey_added``), and every passkey is deleted by «log out everywhere» / a password
  change unless the user opts out (``delete_all``): a key added from a stolen session must not
  outlive the session.
"""

from __future__ import annotations

import html
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import get_settings
from app.core.errors import Conflict, Gone, NotFound, Unauthorized, ValidationFailed
from app.core.security import webauthn_user_handle
from app.core.security_config import webauthn_origins, webauthn_rp_id
from app.core.timeutil import tz
from app.models import AuthChallenge, Passkey, User

log = logging.getLogger("pocket.auth")

CHALLENGE_TTL = timedelta(minutes=5)
TIMEOUT_MS = 60_000
FAILED = "Не удалось проверить Face ID — попробуйте ещё раз или войдите по паролю"
_TRANSPORTS = {t.value for t in AuthenticatorTransport}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _descriptor(pk: Passkey) -> PublicKeyCredentialDescriptor:
    transports = [AuthenticatorTransport(t) for t in (pk.transports or []) if t in _TRANSPORTS]
    return PublicKeyCredentialDescriptor(id=pk.credential_id, transports=transports or None)


async def list_passkeys(session: AsyncSession, user_id: int) -> list[Passkey]:
    return list(
        (
            await session.execute(
                select(Passkey).where(Passkey.user_id == user_id).order_by(Passkey.created_at)
            )
        ).scalars()
    )


async def count_passkeys(session: AsyncSession, user_id: int) -> int:
    return int(await session.scalar(select(func.count()).where(Passkey.user_id == user_id)) or 0)


async def _new_challenge(
    session: AsyncSession, *, purpose: str, user_id: int | None
) -> AuthChallenge:
    ch = AuthChallenge(
        user_id=user_id,
        purpose=purpose,
        challenge=secrets.token_bytes(32),
        expires_at=utcnow() + CHALLENGE_TTL,
    )
    session.add(ch)
    await session.flush()
    return ch


async def _consume_challenge(
    session: AsyncSession, challenge_id: uuid.UUID, purpose: str
) -> AuthChallenge:
    """Lock + mark used. Unknown / wrong purpose / reused → 401; expired → 410 gone."""
    ch = (
        await session.execute(
            select(AuthChallenge)
            .where(AuthChallenge.id == challenge_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if ch is None or ch.purpose != purpose or ch.used_at is not None:
        raise Unauthorized(FAILED)
    if ch.expires_at <= utcnow():
        raise Gone("Запрос Face ID устарел — попробуйте ещё раз")
    ch.used_at = utcnow()
    await session.flush()
    # Single use even if the verification below fails (the route would roll back).
    await session.commit()
    return ch


def _options_dict(options: Any) -> dict[str, Any]:
    return json.loads(options_to_json(options))


async def registration_options(
    session: AsyncSession, user: User
) -> tuple[uuid.UUID, dict[str, Any]]:
    """Create auth_challenges(purpose=register) and return (challenge_id, options JSON)."""
    ch = await _new_challenge(session, purpose="register", user_id=user.id)
    existing = await list_passkeys(session, user.id)
    opts = generate_registration_options(
        rp_id=webauthn_rp_id(),
        rp_name=get_settings().webauthn_rp_name,
        user_name=user.username,
        user_id=webauthn_user_handle(user.id),
        user_display_name="Pocket",
        challenge=ch.challenge,
        timeout=TIMEOUT_MS,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[_descriptor(pk) for pk in existing],
    )
    return ch.id, _options_dict(opts)


async def verify_registration(
    session: AsyncSession,
    user: User,
    challenge_id: uuid.UUID,
    credential: dict[str, Any],
    name: str,
) -> Passkey:
    ch = await _consume_challenge(session, challenge_id, "register")
    if ch.user_id != user.id:
        raise Unauthorized(FAILED)
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=ch.challenge,
            expected_rp_id=webauthn_rp_id(),
            expected_origin=webauthn_origins(),
            require_user_verification=True,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as e:
        log.info("passkey registration rejected: %s", e)
        raise ValidationFailed(
            "Не удалось подключить Face ID — попробуйте ещё раз", details={"reason": str(e)}
        ) from e
    transports = credential.get("response", {}).get("transports") or []
    pk = Passkey(
        user_id=user.id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=[t for t in transports if isinstance(t, str) and t in _TRANSPORTS],
        aaguid=verified.aaguid or None,
        name=(name or "Face ID").strip()[:100] or "Face ID",
    )
    try:
        async with session.begin_nested():
            session.add(pk)
            await session.flush()
    except IntegrityError as e:
        raise Conflict("Этот ключ уже подключён", code="already_exists") from e
    return pk


async def authentication_options(
    session: AsyncSession, username: str
) -> tuple[uuid.UUID, dict[str, Any]]:
    """Unknown user → still valid options with an empty allow-list (no user enumeration)."""
    user = (
        await session.execute(select(User).where(User.username == (username or "owner").strip()))
    ).scalar_one_or_none()
    allow: list[PublicKeyCredentialDescriptor] = []
    if user is not None and user.is_active:
        allow = [_descriptor(pk) for pk in await list_passkeys(session, user.id)]
    ch = await _new_challenge(
        session, purpose="login", user_id=user.id if user is not None else None
    )
    opts = generate_authentication_options(
        rp_id=webauthn_rp_id(),
        challenge=ch.challenge,
        timeout=TIMEOUT_MS,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return ch.id, _options_dict(opts)


async def verify_authentication(
    session: AsyncSession, challenge_id: uuid.UUID, credential: dict[str, Any]
) -> User:
    """Verify assertion, bump sign_count/last_used_at; raise Unauthorized on failure."""
    ch = await _consume_challenge(session, challenge_id, "login")
    raw_id = credential.get("rawId") or credential.get("id")
    if not isinstance(raw_id, str):
        raise Unauthorized(FAILED)
    try:
        cred_id = base64url_to_bytes(raw_id)
    except (ValueError, TypeError) as e:
        raise Unauthorized(FAILED) from e
    row = (
        await session.execute(
            select(Passkey, User)
            .join(User, User.id == Passkey.user_id)
            .where(Passkey.credential_id == cred_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        raise Unauthorized(FAILED)
    pk, user = row
    if not user.is_active or (ch.user_id is not None and ch.user_id != user.id):
        raise Unauthorized(FAILED)
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=ch.challenge,
            expected_rp_id=webauthn_rp_id(),
            expected_origin=webauthn_origins(),
            credential_public_key=pk.public_key,
            credential_current_sign_count=pk.sign_count,
            require_user_verification=True,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as e:
        log.info("passkey assertion rejected: %s", e)
        raise Unauthorized(FAILED) from e
    pk.sign_count = verified.new_sign_count
    pk.last_used_at = utcnow()
    await session.flush()
    return user


async def get_passkey(session: AsyncSession, user_id: int, passkey_id: uuid.UUID) -> Passkey:
    pk = (
        await session.execute(
            select(Passkey).where(Passkey.id == passkey_id, Passkey.user_id == user_id)
        )
    ).scalar_one_or_none()
    if pk is None:
        raise NotFound("Ключ не найден")
    return pk


async def rename_passkey(
    session: AsyncSession, user_id: int, passkey_id: uuid.UUID, name: str
) -> Passkey:
    pk = await get_passkey(session, user_id, passkey_id)
    pk.name = name.strip()[:100]
    await session.flush()
    return pk


async def delete_passkey(session: AsyncSession, user_id: int, passkey_id: uuid.UUID) -> None:
    pk = await get_passkey(session, user_id, passkey_id)
    await session.delete(pk)
    await session.flush()


async def delete_all(session: AsyncSession, user_id: int) -> int:
    """Delete every passkey of the user (log out everywhere / password change). Returns count."""
    res = await session.execute(delete(Passkey).where(Passkey.user_id == user_id))
    await session.flush()
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


_UA_OS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Windows", "Windows"),
    ("CrOS", "ChromeOS"),
    ("Macintosh", "macOS"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
)
_UA_BROWSER = (
    ("Edg", "Edge"),
    ("OPR/", "Opera"),
    ("YaBrowser/", "Яндекс Браузер"),
    ("SamsungBrowser/", "Samsung Internet"),
    ("FxiOS/", "Firefox"),
    ("Firefox/", "Firefox"),
    ("CriOS/", "Chrome"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)


def short_user_agent(ua: str | None) -> str:
    """«iPhone · Safari» from a User-Agent (only known names, never the raw header)."""
    ua = ua or ""
    os_name = next((name for token, name in _UA_OS if token in ua), None)
    browser = next((name for token, name in _UA_BROWSER if token in ua), None)
    parts = [p for p in (os_name, browser) if p]
    return " · ".join(parts) if parts else "неизвестный браузер"


async def notify_passkey_added(
    session: AsyncSession, user_id: int, pk: Passkey, user_agent: str | None
) -> bool:
    """Telegram notice «a new sign-in key was added» through the outbox (the bot delivers it).
    Silently skipped when the user has no Telegram chat. Never fails the registration."""
    from app.services.auth.settings import load_settings
    from app.services.events import outbox

    try:
        target = await outbox.delivery_target(session, user_id)
        if target is None:
            return False
        st = await load_settings(session, user_id)
        when = utcnow().astimezone(tz(st.timezone if st is not None else None))
        text = (
            f"🔐 Добавлен новый ключ входа «{html.escape(pk.name)}» "
            f"({html.escape(short_user_agent(user_agent))}, {when:%d.%m.%Y %H:%M}).\n"
            "Если это не вы — Настройки → Безопасность → Выйти везде (ключи удалятся)."
        )
        async with session.begin_nested():
            await outbox.enqueue(session, user_id, "info", outbox.make_payload(text))
        return True
    except Exception:
        log.warning("passkey notice for user %s was not queued", user_id, exc_info=True)
        return False
