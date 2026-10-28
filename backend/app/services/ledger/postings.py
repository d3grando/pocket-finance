"""Postings = balance movements, rebuilt from a transaction on every change.

Locking: writers of postings take ``FOR SHARE`` on the touched ``account_assets`` rows (writers
don't block each other); reconciliation / opening changes take ``FOR UPDATE`` on the same rows,
so a balance is never computed while another transaction is moving that asset."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccountAsset, Posting, Transaction
from app.models.enums import TxKind, TxStatus


def postings_for(tx: Transaction) -> list[tuple[int, str, Decimal]]:
    """(account_id, asset, signed amount) per ARCH §3.4 table.

    ``needs_account`` = the real account is unknown: an ordinary operation has no postings yet.
    A debt operation (``debt_op``) still posts its debt-account side — the debt exists at once;
    the side with a NULL account is simply skipped (docs/DEBTS.md §3.1)."""
    if tx.deleted_at is not None:
        return []
    if tx.status == TxStatus.needs_account and tx.debt_op is None:
        return []
    out: list[tuple[int, str, Decimal]] = []
    kind = tx.kind
    if kind in (TxKind.expense, TxKind.income, TxKind.refund):
        if tx.account_id is None or tx.account_amount is None or tx.account_asset is None:
            return []
        sign = -1 if kind == TxKind.expense else 1
        out.append((tx.account_id, tx.account_asset, sign * tx.account_amount))
    elif kind in (TxKind.transfer, TxKind.exchange):
        if tx.account_id is not None:
            out.append(
                (
                    tx.account_id,
                    tx.account_asset or tx.currency,
                    -(tx.account_amount if tx.account_amount is not None else tx.amount),
                )
            )
        if tx.counter_account_id is not None and tx.counter_amount is not None:
            out.append((tx.counter_account_id, tx.counter_asset or tx.currency, tx.counter_amount))
    elif kind == TxKind.adjustment:
        if tx.account_id is not None and tx.account_amount:
            out.append((tx.account_id, tx.account_asset or tx.currency, tx.account_amount))
    return [p for p in out if p[2] != 0]


async def lock_assets(
    session: AsyncSession,
    pairs: list[tuple[int, str]] | set[tuple[int, str]],
    mode: Literal["share", "update"] = "share",
) -> list[AccountAsset]:
    """Lock account_assets rows in id order (deadlock-free)."""
    pairs = sorted(set(pairs))
    if not pairs:
        return []
    stmt = (
        select(AccountAsset)
        .where(tuple_(AccountAsset.account_id, AccountAsset.asset).in_(pairs))
        .order_by(AccountAsset.id)
        .execution_options(populate_existing=True)
    )
    stmt = stmt.with_for_update(read=(mode == "share"))
    return list(await session.scalars(stmt))


async def rebuild_postings(session: AsyncSession, tx: Transaction) -> set[int]:
    """Delete + insert this transaction's postings. Returns touched account ids."""
    old = (
        await session.execute(
            select(Posting.account_id, Posting.asset).where(Posting.transaction_id == tx.id)
        )
    ).all()
    new = postings_for(tx)
    pairs = {(a, s) for a, s in old} | {(a, s) for a, s, _ in new}
    await lock_assets(session, pairs, "share")
    if old:
        await session.execute(delete(Posting).where(Posting.transaction_id == tx.id))
    for account_id, asset, amount in new:
        session.add(
            Posting(
                user_id=tx.user_id,
                transaction_id=tx.id,
                account_id=account_id,
                asset=asset,
                amount=amount,
                occurred_at=tx.occurred_at,
            )
        )
    await session.flush()
    return {a for a, _ in pairs}
