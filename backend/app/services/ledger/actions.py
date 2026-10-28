"""Action log: unit of undo + dialogue context. docs/ARCH.md §3.6, §5.1.

Lifecycle: ``begin_action`` inserts the action row (so audit rows can reference it and an
Idempotency-Key is claimed race-free) → services ``record`` undo snapshots into the context →
``finish_action`` writes summary/refs/undo data. ``undo_action`` reverts an action and itself
creates an ``undo`` action (undoing that = redo)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.core.principal import Principal
from app.models import ActionLog
from app.models.enums import ActionKind
from app.schemas.history import UndoResult
from app.schemas.intents import ActionBrief
from app.services.ledger.common import now, writer_lock


class IdempotentReplay(Exception):  # noqa: N818 - control-flow signal, not an error
    """The Idempotency-Key was already used by a completed action: return its response."""

    def __init__(self, action_id: uuid.UUID, status: int, response: Any) -> None:
        super().__init__(str(action_id))
        self.action_id = action_id
        self.status = status
        self.response = response


@dataclass
class ActionContext:
    """Collects undo snapshots while a mutation runs; persisted by ``finish_action``."""

    id: uuid.UUID
    kind: ActionKind
    principal: Principal
    input_text: str | None = None
    ai_request_id: int | None = None
    entity_refs: list[dict[str, Any]] = field(default_factory=list)
    undo_data: list[dict[str, Any]] = field(default_factory=list)
    changed: set[str] = field(default_factory=set)
    is_undoable: bool = True

    def record(
        self,
        entity_type: str,
        entity_id: int | str,
        before: dict[str, Any] | None,
        after_version: int | None,
    ) -> None:
        """Remember the state BEFORE the change (None for created) and the version after it.
        Recording the same entity twice keeps the first ``before`` and the last version."""
        for item in self.undo_data:
            if item["type"] == entity_type and item["id"] == entity_id:
                item["after_version"] = after_version
                return
        self.entity_refs.append({"type": entity_type, "id": entity_id})
        self.undo_data.append(
            {"type": entity_type, "id": entity_id, "before": before, "after_version": after_version}
        )

    @property
    def transaction_ids(self) -> list[int]:
        return [int(r["id"]) for r in self.entity_refs if r["type"] == "transaction"]


async def begin_action(
    session: AsyncSession,
    principal: Principal,
    kind: ActionKind,
    *,
    input_text: str | None = None,
    ai_request_id: int | None = None,
    idempotency_key: str | None = None,
    request_hash: bytes | None = None,
) -> ActionContext:
    """Insert the action row now (placeholder summary).

    With ``idempotency_key``: INSERT … ON CONFLICT DO NOTHING on (user_id, key). A concurrent
    duplicate blocks on the unique index until the first commits; then it gets
    ``IdempotentReplay`` (same body) or 409 ``idempotency_mismatch`` (different body)."""
    await writer_lock(session, principal.user_id)
    aid = uuid.uuid4()
    values = {
        "id": aid,
        "user_id": principal.user_id,
        "source": principal.source,
        "kind": str(kind),
        "summary": "",
        "input_text": input_text,
        "entity_refs": [],
        "undo_data": [],
        "ai_request_id": ai_request_id,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
        # wall clock, not now(): several actions of one DB transaction (one assistant message
        # with many intents) must keep their order in the dialogue context
        "created_at": now(),
    }
    stmt = pg_insert(ActionLog).values(**values)
    if idempotency_key:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["user_id", "idempotency_key"],
            index_where=ActionLog.idempotency_key.isnot(None),
        )
    res = await session.execute(stmt.returning(ActionLog.id))
    if res.scalar_one_or_none() is None:
        prior = await session.scalar(
            select(ActionLog).where(
                ActionLog.user_id == principal.user_id,
                ActionLog.idempotency_key == idempotency_key,
            )
        )
        if prior is None:  # pragma: no cover - deleted between statements
            raise Conflict("Запрос уже выполняется", code="idempotency_in_progress")
        if request_hash is not None and prior.request_hash not in (None, request_hash):
            raise Conflict(
                "Этот Idempotency-Key уже использован с другим запросом",
                code="idempotency_mismatch",
            )
        if prior.response is None:
            raise Conflict("Запрос уже выполняется", code="idempotency_in_progress")
        raise IdempotentReplay(prior.id, prior.response_status or 200, prior.response)
    return ActionContext(
        id=aid, kind=kind, principal=principal, input_text=input_text, ai_request_id=ai_request_id
    )


async def finish_action(
    session: AsyncSession, ctx: ActionContext, summary: str | None = None
) -> ActionLog:
    """Write summary/entity refs/undo data into the action row (flush, no commit).

    First the whole-action invariant of debts: a write-off never exceeds the debt it wrote off
    (``debts.ops.check_forgives_covered``; 422, the caller's transaction is rolled back)."""
    from app.services.debts.ops import check_forgives_covered

    await check_forgives_covered(session, ctx.undo_data)
    if summary is None:
        from app.services.ledger.summaries import action_summary

        summary = await action_summary(session, ctx)
    row = await session.get(ActionLog, ctx.id)
    if row is None:  # pragma: no cover - begin_action always inserts
        raise NotFound("Действие не найдено")
    row.kind = str(ctx.kind)
    row.summary = summary
    row.entity_refs = list(ctx.entity_refs)
    row.undo_data = list(ctx.undo_data)
    row.is_undoable = ctx.is_undoable and bool(ctx.undo_data)
    await session.flush()
    return row


async def discard_action(session: AsyncSession, ctx: ActionContext) -> None:
    """Drop an action that changed nothing (e.g. reconciliation that already matches)."""
    await session.execute(delete(ActionLog).where(ActionLog.id == ctx.id))


async def store_response(
    session: AsyncSession, action_id: uuid.UUID, status: int, payload: Any
) -> None:
    """Persist the API response for ledger-level idempotent replays (before commit)."""
    await session.execute(
        update(ActionLog)
        .where(ActionLog.id == action_id)
        .values(response_status=status, response=payload)
    )


async def undo_action(
    session: AsyncSession, principal: Principal, action_id: uuid.UUID
) -> UndoResult:
    """Revert an action: created → soft-delete, updated → restore ``before``, deleted → restore.
    Version mismatch → 409 ``undo_conflict``. Already undone → status=already_undone (idempotent).
    Creates an ``undo`` action (itself undoable = redo). Publishes change events."""
    from app.services.ledger.undo import perform_undo

    return await perform_undo(session, principal, action_id)


async def last_undoable_action(session: AsyncSession, user_id: int) -> ActionLog | None:
    """Target of a text «отмени» (same rule as the bot's /undo): the latest not-undone action
    that is not itself an undo — a second «отмени» undoes the previous operation instead of
    redoing the first one. Redo stays with the «Вернуть» button (undo of the undo action)."""
    return await session.scalar(
        select(ActionLog)
        .where(
            ActionLog.user_id == user_id,
            ActionLog.undone_at.is_(None),
            ActionLog.is_undoable.is_(True),
            ActionLog.kind != ActionKind.undo,
            ActionLog.summary != "",
        )
        .order_by(ActionLog.created_at.desc())
        .limit(1)
    )


async def recent_action_rows(
    session: AsyncSession, user_id: int, *, limit: int = 10, minutes: int = 30
) -> list[ActionLog]:
    rows = await session.scalars(
        select(ActionLog)
        .where(
            ActionLog.user_id == user_id,
            ActionLog.created_at > now() - timedelta(minutes=minutes),
            ActionLog.undone_at.is_(None),
            ActionLog.summary != "",
        )
        .order_by(ActionLog.created_at.desc())
        .limit(limit)
    )
    return list(rows)


async def recent_actions(
    session: AsyncSession, user_id: int, *, limit: int = 10, minutes: int = 30
) -> list[ActionBrief]:
    """Dialogue context shared by all clients: last ``limit`` non-undone actions in ``minutes``."""
    from app.services.ledger.summaries import briefs_for

    rows = await recent_action_rows(session, user_id, limit=limit, minutes=minutes)
    return await briefs_for(session, rows)


async def list_actions(
    session: AsyncSession, user_id: int, *, limit: int = 50, offset: int = 0
) -> list[ActionLog]:
    rows = await session.scalars(
        select(ActionLog)
        .where(ActionLog.user_id == user_id, ActionLog.summary != "")
        .order_by(ActionLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows)
