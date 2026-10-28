"""Auth / devices / passkeys schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.common import Schema, StrictInput


class LoginIn(StrictInput):
    username: str = Field(default="owner", max_length=64)
    password: str = Field(min_length=1, max_length=256)


class MeOut(Schema):
    user_id: int
    username: str
    auth: Literal["session", "device", "bot"]
    source: Literal["web", "desktop", "telegram"]
    onboarding_done: bool
    has_passkeys: bool
    csrf_token: str | None = Field(
        default=None, description="session auth only: same value as the pf_csrf cookie"
    )


class DeviceLoginIn(StrictInput):
    username: str = Field(default="owner", max_length=64)
    password: str = Field(min_length=1, max_length=256)
    device_name: str = Field(min_length=1, max_length=100)
    platform: Literal["windows", "macos", "linux"]


class DeviceLoginOut(Schema):
    token: str = Field(description="pfd_… — store in OS secure storage, shown once")
    device_id: uuid.UUID


class DeviceOut(Schema):
    id: uuid.UUID
    name: str
    platform: str
    created_at: datetime
    last_used_at: datetime | None
    is_current: bool = False


class SessionOut(Schema):
    id: uuid.UUID
    user_agent: str | None
    ip: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    is_current: bool = False


class PasskeyOut(Schema):
    id: uuid.UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None


class WebAuthnOptionsOut(Schema):
    """``options`` is PublicKeyCredentialCreationOptionsJSON / RequestOptionsJSON
    (pass directly to @simplewebauthn/browser startRegistration/startAuthentication)."""

    challenge_id: uuid.UUID
    options: dict[str, Any]


class PasskeyRegisterOptionsIn(StrictInput):
    """POST /passkeys/register/options. The password is required unless the cookie session was
    created ≤ 5 min ago (Face ID offer right after login); no body = ``password: null``.
    Missing on an older session → 403 ``reauth_required``; wrong → 401 ``wrong_password``."""

    password: str | None = Field(default=None, min_length=1, max_length=256)


class PasskeyRegisterVerifyIn(StrictInput):
    challenge_id: uuid.UUID
    credential: dict[str, Any] = Field(description="RegistrationResponseJSON")
    name: str = Field(default="Face ID", max_length=100)


class PasskeyLoginOptionsIn(StrictInput):
    username: str = Field(default="owner", max_length=64)


class PasskeyLoginVerifyIn(StrictInput):
    challenge_id: uuid.UUID
    credential: dict[str, Any] = Field(description="AuthenticationResponseJSON")


class PasskeyRename(StrictInput):
    name: str = Field(min_length=1, max_length=100)


class LogoutAllOut(Schema):
    """POST /auth/logout-all?keep_current=false&remove_passkeys=true — «Выйти со всех
    устройств» (passkeys are removed too unless ``remove_passkeys=false``)."""

    ok: bool = True
    sessions_revoked: int
    devices_revoked: int
    passkeys_removed: int = 0
