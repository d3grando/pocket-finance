"""Undo / redo of actions from their snapshots. ARCH §5.1.

Each undo_data item: {"type", "id", "before": snapshot|None, "after_version": int|None}.
before=None → the entity was created by the action (undo removes it);
otherwise the snapshot is restored. A version mismatch means somebody changed the entity after
the action → 409 undo_conflict, nothing is changed (the whole DB transaction is rolled back)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.core.principal import Principal
from app.models import (
    AccountAsset,
    ActionLog,
    Category,
    Merchant,
    RecurringTransaction,
    Transaction,
)
from app.models.enums import ActionKind
from app.schemas.history import UndoResult
from app.services.ledger.actions import ActionContext, begin_action, finish_action
from app.services.ledger.audit import apply_snapshot, from_snapshot, snapshot, write_audit
from app.services.ledger.common import now, publish, writer_lock
from app.services.ledger.postings import lock_assets, rebuild_postings
from app.services.ledger.summaries import Refs, change_text, tx_summary

MODELS: dict[str, Any] = {
    "transaction": Transaction,
    "account_asset": AccountAsset,
    "category": Category,
    "merchant": Merchant,
    "recurring": RecurringTransaction,
}
ENTITY_EVENTS = {
    "transaction": {"transactions", "accounts"},
    "account_asset": {"accounts"},
    "category": {"categories"},
    "merchant": {"merchants"},
    "recurring": {"recurring"},
}


class UndoConflict(Conflict):
    code, message = "undo_conflict", "Запись уже изменили после этого действия — отменить нельзя"


async def _load(session: AsyncSession, model: Any, user_id: int, entity_id: Any) -> Any:
    stmt = (
        select(model)
        .where(model.id == entity_id, model.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return await session.scalar(stmt)


async def _category_referenced(session: AsyncSession, cid: int) -> bool:
    q = select(
        or_(
            exists().where(Transaction.category_id == cid),
            exists().where(Category.merged_into_id == cid),
        )
    )
    return bool(await session.scalar(q))


async def _net_undone(session: AsyncSession, action: ActionLog) -> bool:
    """True when the action's effect is currently reverted: an odd-length undo chain
    (undone; or undone → redone → undone…)."""
    cur, undone = action, False
    for _ in range(100):
        if cur.undone_at is None or cur.undone_by_action_id is None:
            return undone
        nxt = await session.get(ActionLog, cur.undone_by_action_id)
        if nxt is None:
            return not undone
        cur, undone = nxt, not undone
    return undone


async def _later_changes_reverted(
    session: AsyncSession, row: ActionLog, etype: str, eid: int
) -> bool:
    """Version moved since ``row``, but every later non-undo action on this entity is undone
    (their undos bumped the version) → the entity is back in the state ``row`` left it in."""
    later = (
        await session.scalars(
            select(ActionLog).where(
                ActionLog.user_id == row.user_id,
                ActionLog.id != row.id,
                ActionLog.created_at >= row.created_at,
                ActionLog.kind != ActionKind.undo,
                ActionLog.entity_refs.contains([{"type": etype, "id": eid}]),
            )
        )
    ).all()
    if not later:
        return False  # changed outside the action log → a real conflict
    for a in later:
        if not await _net_undone(session, a):
            return False
    return True


async def _undo_item(
    session: AsyncSession,
    principal: Principal,
    ctx: ActionContext,
    item: dict[str, Any],
    changed: set[str],
    tx_states: dict[int, tuple[Any, Any]],
    row: ActionLog | None = None,
) -> None:
    etype = item["type"]
    model = MODELS.get(etype)
    if model is None:
        raise UndoConflict(f"Неизвестный тип записи: {etype}")
    uid = principal.user_id
    eid = int(item["id"])
    before = item.get("before")
    after_version = item.get("after_version")
    obj = await _load(session, model, uid, eid)
    changed |= ENTITY_EVENTS.get(etype, set())

    if obj is None:
        if before is None:
            return  # created then removed by someone else: nothing to undo
        # hard-deleted by a previous undo → re-create from snapshot (redo)
        obj = from_snapshot(model, before)
        session.add(obj)
        await session.flush()
        ctx.record(etype, eid, None, getattr(obj, "version", None))
        await write_audit(
            session, user_id=uid, entity_type=etype, entity_id=eid, operation="restore",
            before=None, after=snapshot(obj), source=principal.source, action_id=ctx.id,
        )
        return

    current_version = getattr(obj, "version", None)
    if after_version is not None and current_version is not None and (
        int(current_version) != int(after_version)
    ) and not (row is not None and await _later_changes_reverted(session, row, etype, eid)):
        raise UndoConflict(details={"entity_type": etype, "entity_id": eid,
                                    "current_version": current_version})
    now_snap = snapshot(obj)

    if etype == "transaction":
        if obj.debt_op is not None:
            changed.add("debts")  # debt balances moved (docs/DEBTS.md §4)
        # (state before this undo as a detached copy, the live row) — for the summary text
        tx_states[eid] = (from_snapshot(Transaction, now_snap), obj)
        if before is None:
            if obj.deleted_at is not None:
                return
            obj.deleted_at = now()
            op = "delete"
        else:
            apply_snapshot(obj, before)
            op = "restore" if (now_snap.get("deleted_at") and not before.get("deleted_at")) \
                else "update"
        obj.version += 1
        await session.flush()
        await rebuild_postings(session, obj)
        ctx.record(etype, eid, now_snap, obj.version)
    elif etype == "account_asset":
        await lock_assets(session, {(obj.account_id, obj.asset)}, "update")
        if before is None:
            obj.opening_balance = None
            obj.opening_at = None
        else:
            apply_snapshot(obj, before)
        op = "update"
        await session.flush()
        ctx.record(etype, eid, now_snap, None)
    elif etype in ("category", "merchant"):
        if before is None:
            referenced = (
                await _category_referenced(session, eid) if etype == "category" else False
            )
            if etype == "category" and referenced:
                obj.is_archived = True
                obj.version += 1
                op = "update"
                await session.flush()
                ctx.record(etype, eid, now_snap, obj.version)
            else:
                await session.delete(obj)
                op = "delete"
                await session.flush()
                ctx.record(etype, eid, now_snap, None)
        else:
            apply_snapshot(obj, before)
            if hasattr(obj, "version"):
                obj.version += 1
            op = "update"
            await session.flush()
            ctx.record(etype, eid, now_snap, getattr(obj, "version", None))
    else:  # recurring
        if before is not None:
            apply_snapshot(obj, before)
        obj.version += 1
        op = "update"
        await session.flush()
        ctx.record(etype, eid, now_snap, obj.version)

    after = None if op == "delete" and etype != "transaction" else snapshot(obj)
    await write_audit(
        session, user_id=uid, entity_type=etype, entity_id=eid, operation=op,
        before=now_snap, after=after, source=principal.source, action_id=ctx.id,
    )


# root action kind → (phrase when undoing, phrase when redoing)
_PHRASES: dict[str, tuple[str, str]] = {
    ActionKind.update_transaction: ("Отменено изменение", "Возвращено изменение"),
    ActionKind.bulk_update: ("Отменено изменение", "Возвращено изменение"),
    ActionKind.delete_transaction: ("Отменено удаление", "Снова удалено"),
    ActionKind.bulk_delete: ("Отменено удаление", "Снова удалено"),
    ActionKind.restore_transaction: ("Отменено восстановление", "Снова восстановлено"),
    ActionKind.assign_account: ("Отменено назначение счёта", "Снова назначен счёт"),
}
_PLAIN = ("Отменено", "Возвращено")
_DIFF_KINDS = {ActionKind.update_transaction, ActionKind.bulk_update, ActionKind.assign_account}
_STATE_KINDS = {ActionKind.delete_transaction, ActionKind.bulk_delete,
                ActionKind.restore_transaction}
# prefixes that ledger.summaries.action_summary puts in front of a transaction summary
_KIND_PREFIXES = ("Изменено: ", "Удалено: ", "Восстановлено: ")
_UNDO_PREFIXES = ("Отменено: ", "Возвращено: ")


async def _chain(session: AsyncSession, row: ActionLog) -> tuple[ActionLog, int]:
    """Walk undo → undone action back to the original one. Returns (root, depth): depth 1 =
    undoing the original action, 2 = redo (undoing that undo), 3 = undoing the redo…"""
    cur, depth = row, 1
    while cur.kind == ActionKind.undo and depth < 100:
        prev = await session.scalar(
            select(ActionLog).where(
                ActionLog.user_id == cur.user_id, ActionLog.undone_by_action_id == cur.id
            )
        )
        if prev is None:
            break
        cur, depth = prev, depth + 1
    return cur, depth


def _strip(s: str, prefixes: tuple[str, ...]) -> str:
    changed = True
    while changed:
        changed = False
        for p in prefixes:
            if s.startswith(p):
                s, changed = s[len(p):], True
    return s


async def _undo_summary(
    session: AsyncSession, row: ActionLog, tx_states: dict[int, tuple[Any, Any]]
) -> str:
    """«Отменено: 77 000 ₫ · Такси» → (redo) «Возвращено: 77 000 ₫ · Такси» → «Отменено: …»;
    edits: «Отменено изменение: 300 000 ₫ → 250 000 ₫ · Еда» (direction of this step)."""
    root, depth = await _chain(session, row)
    if root.kind == ActionKind.undo:  # chain lost (legacy rows): toggle by the text prefix
        redo = row.summary.startswith("Отменено")
        return f"{_PLAIN[1] if redo else _PLAIN[0]}: {_strip(row.summary, _UNDO_PREFIXES)}"
    redo = depth % 2 == 0
    phrases = _PHRASES.get(root.kind, _PLAIN)
    phrase = phrases[1] if redo else phrases[0]
    tx_ids = [int(r["id"]) for r in (root.entity_refs or []) if r.get("type") == "transaction"]
    if root.kind in (ActionKind.bulk_update, ActionKind.bulk_delete) and len(tx_ids) > 1:
        return f"{phrase} операций: {len(tx_ids)}"
    pair = tx_states.get(tx_ids[0]) if tx_ids else None
    if pair is not None and (root.kind in _DIFF_KINDS or root.kind in _STATE_KINDS):
        old, new = pair
        refs = await Refs.load(session, [old, new])
        body = change_text(old, new, refs) if root.kind in _DIFF_KINDS else tx_summary(new, refs)
        return f"{phrase}: {body}"
    return f"{phrase}: {_strip(root.summary, _KIND_PREFIXES + _UNDO_PREFIXES)}"


async def perform_undo(
    session: AsyncSession, principal: Principal, action_id: uuid.UUID
) -> UndoResult:
    uid = principal.user_id
    await writer_lock(session, uid)
    row = await session.scalar(
        select(ActionLog)
        .where(ActionLog.id == action_id, ActionLog.user_id == uid)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("Действие не найдено")
    if row.undone_at is not None:
        return UndoResult(
            status="already_undone",
            undone_action_id=row.id,
            undo_action_id=row.undone_by_action_id,
            summary=f"Уже отменено: {row.summary}",
            changed=[],
        )
    if not row.is_undoable or not row.undo_data:
        raise UndoConflict("Это действие нельзя отменить")

    ctx = await begin_action(session, principal, ActionKind.undo)
    changed: set[str] = set()
    tx_states: dict[int, tuple[Any, Any]] = {}
    for item in reversed(row.undo_data):
        await _undo_item(session, principal, ctx, item, changed, tx_states, row)

    row.undone_at = now()
    row.undone_by_action_id = ctx.id
    await session.flush()
    summary = await _undo_summary(session, row, tx_states)
    await session.flush()
    from app.services.debts.ops import ForgiveNotCovered

    try:
        await finish_action(session, ctx, summary)
    except ForgiveNotCovered as e:  # redo of a write-off after the debt was returned, etc.
        raise UndoConflict(e.message, details=e.details) from e
    if "transactions" in changed:
        changed.add("stats")
    await publish(session, uid, changed, action_id=ctx.id, source=principal.source)
    return UndoResult(
        status="undone",
        undone_action_id=row.id,
        undo_action_id=ctx.id,
        summary=summary,
        changed=sorted(changed),
    )
