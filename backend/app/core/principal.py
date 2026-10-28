"""Authenticated principal passed to every service call."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

PrincipalKind = Literal["session", "device", "bot", "system"]
Source = Literal["web", "desktop", "telegram", "system"]


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: int
    kind: PrincipalKind
    source: Source
    session_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    telegram_user_id: int | None = None
