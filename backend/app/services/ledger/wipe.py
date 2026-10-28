"""Full removal of a user's financial data («экспорт и удаление данных»).

``wipe_financial_data`` keeps the user, settings and login methods and re-seeds the default
accounts/categories (POST /users/me/wipe, auth route). ``delete_user`` removes the
user row and everything else through ON DELETE CASCADE."""

from __future__ import annotations

import logging

from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict
from app.models import (
    Account,
    AccountAsset,
    ActionLog,
    AIRequest,
    AuditLog,
    Category,
    Debt,
    IdempotencyKey,
    Merchant,
    MyAccount,
    NotificationOutbox,
    Posting,
    PreviewToken,
    RecurringTransaction,
    Transaction,
    User,
    UserSettings,
)
from app.services.ledger.common import publish, writer_lock

log = logging.getLogger("pocket.ledger.wipe")
_ATTEMPTS = 3


class WipeBusy(Conflict):
    code, message = "conflict", "Сейчас идёт запись операций — повторите удаление через минуту"

# children first (FKs without CASCADE between financial tables)
_ORDER = (
    Posting,
    Transaction,
    RecurringTransaction,
    PreviewToken,
    AuditLog,
    ActionLog,
    AIRequest,
    NotificationOutbox,
    IdempotencyKey,
    MyAccount,
    Merchant,
    Debt,  # debts → their service accounts (FK CASCADE too; listed for the counts)
    AccountAsset,
    Account,
    Category,
)


async def wipe_financial_data(
    session: AsyncSession, user_id: int, *, reseed: bool = True
) -> dict[str, int]:
    """Delete transactions, postings, accounts, categories, merchants, my_accounts, recurring,
    previews, action/audit logs, ai_requests, outbox, idempotency keys. Optionally re-seed
    defaults and reset onboarding. Returns deleted row counts per table (flush, no commit)."""
    await session.flush()
    # no ledger writer of this user can run in between: they take the same lock SHARED before
    # their first row lock, so a concurrent operation either finished before (and is deleted
    # here) or waits and starts on the fresh data.
    await writer_lock(session, user_id, exclusive=True)
    counts: dict[str, int] = {}
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            async with session.begin_nested():
                counts = await _delete_all(session, user_id)
            break
        except (IntegrityError, DBAPIError) as e:
            # a writer outside the ledger lock (should not happen) or a deadlock victim
            if not isinstance(e, IntegrityError) and _sqlstate(e) not in _RETRY_STATES:
                raise
            log.warning("wipe of user %s: attempt %s failed: %s", user_id, attempt,
                        type(e).__name__)
            if attempt == _ATTEMPTS:
                raise WipeBusy() from e
    if reseed:
        from app.services.accounts.seed import seed_default_accounts
        from app.services.categories.seed import seed_defaults

        await seed_defaults(session, user_id)
        await seed_default_accounts(session, user_id)
        st = await session.get(UserSettings, user_id)
        if st is not None:
            st.onboarding_done = False
            st.version = (st.version or 1) + 1
    await session.flush()
    await publish(
        session, user_id,
        {"transactions", "accounts", "categories", "merchants", "settings", "recurring",
         "debts"},
        source="system",
    )
    return counts


_RETRY_STATES = {"40P01", "40001"}  # deadlock_detected, serialization_failure


def _sqlstate(e: DBAPIError) -> str | None:
    orig = getattr(e, "orig", None)
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


async def _delete_all(session: AsyncSession, user_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    await session.execute(
        update(Transaction).where(Transaction.user_id == user_id).values(refund_of_id=None)
    )
    await session.execute(
        update(Category).where(Category.user_id == user_id).values(merged_into_id=None)
    )
    for model in _ORDER:
        res = await session.execute(delete(model).where(model.user_id == user_id))
        counts[model.__tablename__] = res.rowcount or 0
    return counts


async def delete_user(session: AsyncSession, user_id: int) -> None:
    """Remove the user and all their data (ON DELETE CASCADE everywhere)."""
    await session.flush()
    await session.execute(delete(User).where(User.id == user_id))
    session.expunge_all()
