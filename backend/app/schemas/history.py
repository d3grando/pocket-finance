"""Action log / undo / audit history schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from app.schemas.common import Schema, SourceEnum


class EntityRef(Schema):
    type: str
    id: int | str


class ActionOut(Schema):
    id: uuid.UUID
    kind: str
    summary: str
    source: SourceEnum
    input_text: str | None = None
    entity_refs: list[EntityRef]
    is_undoable: bool
    undone_at: datetime | None = None
    created_at: datetime


class UndoResult(Schema):
    status: Literal["undone", "already_undone"]
    undone_action_id: uuid.UUID
    undo_action_id: uuid.UUID | None
    summary: str
    changed: list[str]


class AuditEntryOut(Schema):
    id: int
    entity_type: str
    entity_id: str
    operation: Literal["create", "update", "delete", "restore", "merge"]
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    action_id: uuid.UUID | None
    source: SourceEnum
    created_at: datetime
