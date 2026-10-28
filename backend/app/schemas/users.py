"""User account schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas.common import Schema, StrictInput


class UserOut(Schema):
    id: int
    username: str
    created_at: datetime


class PasswordChangeIn(StrictInput):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)
    revoke_other_sessions: bool = True
    #: also delete every passkey (a key added from a stolen session must not survive)
    remove_passkeys: bool = True


class WipeDataIn(StrictInput):
    """Delete all financial data (keeps the user, settings, auth). Requires password + phrase."""

    password: str = Field(max_length=256)
    confirm: str = Field(max_length=64, description="must equal «УДАЛИТЬ»")
