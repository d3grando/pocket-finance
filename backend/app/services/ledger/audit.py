"""Audit log writer + JSON snapshots of ORM rows."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, LargeBinary, Numeric, Time, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import normalize
from app.models import AuditLog

# Columns never restored from a snapshot (identity / bookkeeping).
_SKIP_RESTORE = {"id", "user_id", "created_at", "updated_at", "version"}


def _json_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return normalize(v)
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if isinstance(v, (list, tuple)):
        return [_json_value(x) for x in v]
    return v


def snapshot(obj: Any) -> dict[str, Any]:
    """JSON-safe dict of an ORM row's column values (Decimal → str, dates → ISO)."""
    mapper = sa_inspect(obj).mapper
    out: dict[str, Any] = {}
    for attr in mapper.column_attrs:
        out[attr.key] = _json_value(getattr(obj, attr.key))
    return out


def _from_json(col_type: Any, v: Any) -> Any:
    if v is None:
        return None
    if isinstance(col_type, Numeric):
        return Decimal(str(v))
    if isinstance(col_type, DateTime):
        return datetime.fromisoformat(v)
    if isinstance(col_type, Date):
        return date.fromisoformat(v)
    if isinstance(col_type, Time):
        return time.fromisoformat(v)
    if isinstance(col_type, UUID):
        return uuid.UUID(str(v))
    if isinstance(col_type, LargeBinary):
        return bytes.fromhex(v)
    return v


def apply_snapshot(obj: Any, snap: dict[str, Any], *, include_identity: bool = False) -> None:
    """Restore column values from a snapshot (types converted back from JSON)."""
    mapper = sa_inspect(obj).mapper
    for attr in mapper.column_attrs:
        key = attr.key
        if key not in snap:
            continue
        if not include_identity and key in _SKIP_RESTORE:
            continue
        col = attr.columns[0]
        setattr(obj, key, _from_json(col.type, snap[key]))


def from_snapshot(model: type, snap: dict[str, Any]) -> Any:
    """Re-create a (hard-deleted) row from its snapshot, keeping its id (redo of an undo)."""
    obj = model()
    apply_snapshot(obj, snap, include_identity=True)
    if hasattr(obj, "version") and snap.get("version") is not None:
        obj.version = int(snap["version"])
    return obj


async def write_audit(
    session: AsyncSession,
    *,
    user_id: int,
    entity_type: str,
    entity_id: int | str,
    operation: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    source: str,
    action_id: uuid.UUID | None = None,
) -> AuditLog:
    row = AuditLog(
        user_id=user_id,
        entity_type=entity_type,
        entity_id=str(entity_id),
        operation=operation,
        before=before,
        after=after,
        action_id=action_id,
        source=source,
    )
    session.add(row)
    return row


async def history_for(
    session: AsyncSession, user_id: int, entity_type: str, entity_id: int | str
) -> list[AuditLog]:
    rows = await session.scalars(
        select(AuditLog)
        .where(
            AuditLog.user_id == user_id,
            AuditLog.entity_type == entity_type,
            AuditLog.entity_id == str(entity_id),
        )
        .order_by(AuditLog.created_at, AuditLog.id)
    )
    return list(rows)
