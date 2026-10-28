"""Preview + confirm for changes touching > 1 record or found by search.

The preview stores the affected ids+versions; confirm re-checks the versions (409
preview_stale), applies everything in one action (bulk_update / bulk_delete, undoable as a
whole) and marks the token consumed (a repeated confirm returns the same action)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Conflict, Gone, NotFound, ValidationFailed
from app.core.principal import Principal
from app.models import PreviewToken, Transaction
from app.models.enums import ActionKind, PreviewKind
from app.schemas.intents import DeleteTransactionIntent, UpdateTransactionIntent
from app.schemas.transactions import TransactionPatch
from app.services.ledger.actions import begin_action, finish_action
from app.services.ledger.common import now

PREVIEW_TTL = timedelta(minutes=10)

# TransactionChanges fields that map 1:1 to TransactionPatch (no reference resolution needed)
_DIRECT = ("amount", "currency", "account_none", "account_amount", "occurred_on", "description",
           "counterparty", "kind")


class PreviewStale(Conflict):
    code, message = "preview_stale", "Записи изменились — повторите запрос"


class PreviewExpired(Gone):
    code, message = "preview_expired", "Превью устарело — повторите запрос"


def _patch_from_intent(
    intent: UpdateTransactionIntent, patch: dict[str, Any] | None
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    ch = intent.changes
    for k in _DIRECT:
        v = getattr(ch, k, None)
        if v is not None:
            out[k] = v
    if patch:
        out.update({k: v for k, v in patch.items() if k != "version"})
    unresolved = [
        k for k, idk in (("category_ref", "category_id"), ("account_ref", "account_id"))
        if getattr(ch, k) and idk not in out
    ]
    if ch.new_category is not None and "category_id" not in out:
        unresolved.append("new_category")
    if unresolved:
        raise ValidationFailed(
            "Изменение нужно передать с найденными id", details={"unresolved": unresolved}
        )
    # validate now (raises 422 early, before the user confirms)
    TransactionPatch.model_validate({"version": 1, **out})
    return out


async def create_preview(
    session: AsyncSession,
    principal: Principal,
    intent: UpdateTransactionIntent | DeleteTransactionIntent,
    matches: list[Transaction],
    *,
    patch: dict[str, Any] | None = None,
    summary: str | None = None,
) -> PreviewToken:
    """Store preview_tokens row (10 min) with affected ids+versions and Russian summary.
    For updates, refs in ``intent.changes`` must be resolved by the caller and passed as
    ``patch`` (TransactionPatch fields without version, e.g. {"category_id": 7})."""
    if not matches:
        raise NotFound("Ничего не найдено")
    is_update = isinstance(intent, UpdateTransactionIntent)
    stored: dict[str, Any] = {"intent": intent.model_dump(mode="json")}
    if is_update:
        resolved = _patch_from_intent(intent, patch)
        stored["patch"] = TransactionPatch.model_validate(
            {"version": 1, **resolved}
        ).model_dump(mode="json", exclude_unset=True, exclude={"version"})
    n = len(matches)
    tok = PreviewToken(
        id=uuid.uuid4(),
        user_id=principal.user_id,
        kind=PreviewKind.bulk_update if is_update else PreviewKind.bulk_delete,
        intent=stored,
        affected=[{"id": t.id, "version": t.version} for t in matches],
        summary=summary or (f"Изменить операций: {n}" if is_update else f"Удалить операций: {n}"),
        action_source=principal.source,
        expires_at=now() + PREVIEW_TTL,
    )
    session.add(tok)
    await session.flush()
    return tok


async def _load_token(
    session: AsyncSession, principal: Principal, token: uuid.UUID
) -> PreviewToken:
    tok = await session.scalar(
        select(PreviewToken)
        .where(PreviewToken.id == token, PreviewToken.user_id == principal.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if tok is None:
        raise NotFound("Превью не найдено")
    return tok


async def confirm_preview(
    session: AsyncSession, principal: Principal, token: uuid.UUID
) -> tuple[uuid.UUID, int]:
    """Apply atomically. Returns (action_id, count). 410 preview_expired, 409 preview_stale
    (any version changed), already consumed → return the stored result_action_id."""
    from app.services.ledger.common import writer_lock
    from app.services.ledger.transactions import delete_transaction, update_transaction

    await writer_lock(session, principal.user_id)
    tok = await _load_token(session, principal, token)
    if tok.consumed_at is not None:
        if tok.result_action_id is None:
            raise PreviewExpired("Превью отменено")
        return tok.result_action_id, len(tok.affected)
    if tok.expires_at <= now():
        raise PreviewExpired()
    ids = [int(a["id"]) for a in tok.affected]
    rows = {
        t.id: t
        for t in await session.scalars(
            select(Transaction)
            .where(Transaction.id.in_(ids), Transaction.user_id == principal.user_id)
            .order_by(Transaction.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    for a in tok.affected:
        t = rows.get(int(a["id"]))
        if t is None or t.deleted_at is not None or t.version != int(a["version"]):
            raise PreviewStale(details={"transaction_id": int(a["id"])})
    is_update = tok.kind == PreviewKind.bulk_update
    ctx = await begin_action(
        session, principal, ActionKind.bulk_update if is_update else ActionKind.bulk_delete
    )
    try:
        for a in tok.affected:
            tid, ver = int(a["id"]), int(a["version"])
            if is_update:
                p = TransactionPatch.model_validate({"version": ver, **tok.intent.get("patch", {})})
                await update_transaction(session, principal, tid, p, action=ctx)
            else:
                await delete_transaction(session, principal, tid, version=ver, action=ctx)
    except AppError as e:
        if e.code == "version_conflict":
            raise PreviewStale() from e
        raise
    await finish_action(session, ctx, tok.summary if len(ids) > 1 else None)
    tok.consumed_at = now()
    tok.result_action_id = ctx.id
    await session.flush()
    return ctx.id, len(ids)


async def cancel_preview(session: AsyncSession, principal: Principal, token: uuid.UUID) -> None:
    tok = await _load_token(session, principal, token)
    if tok.consumed_at is None:
        tok.consumed_at = now()
        await session.flush()
