"""Executes validated debt intents (docs/DEBTS.md §5.4/§8) through the debts
service only: ``get_or_create_debt`` (learns the written form as an alias) → ``record_op`` in the
message's shared DB transaction with one ``action_log.kind='debt'`` action (undo, «нет, 250к»,
idempotency as for every operation). ``debts_query`` → ``list_debts`` → answer card.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select

from app.models import Debt, Transaction
from app.models.enums import ActionKind
from app.schemas.debts import DebtBrief, DebtOpCreate
from app.schemas.transactions import TransactionOut
from app.services.ai import debt_cards as dc
from app.services.ai.validator import ValidatedIntent
from app.services.debts import service as debts_svc

if TYPE_CHECKING:
    from app.services.ai.dispatcher import _Dispatcher

log = logging.getLogger("pocket.ai")
DEBT_ENTITIES = {"transactions", "accounts", "stats", "debts"}


async def run_debt(d: _Dispatcher, vi: ValidatedIntent) -> None:
    from app.services.ai.dispatcher import account_options

    r = vi.resolved
    act = await d._begin(ActionKind.debt)
    debt = await debts_svc.get_or_create_debt(
        d.s, d.p, r["person_name"], handle=r.get("handle"), written_form=r.get("written_form"),
        action=act,
    )
    data = DebtOpCreate(
        op=r["debt_op"], debt_id=debt.id, amount=r.get("amount"), currency=r["currency"],
        account_id=r.get("account_id"), account_none=bool(r.get("account_none")),
        description=r.get("description"), occurred_on=r.get("occurred_on"),
        occurred_at=r.get("occurred_at"), direction=r.get("direction"),
    )
    res = await debts_svc.record_op(d.s, d.p, data, raw_text=d.raw, ai_request_id=d.ai_id,
                                    action=act)
    op = r["debt_op"]
    tx = res.transaction
    aid = await d._finish(act, dc.op_summary(op, res.debt.name, tx.amount, tx.currency, tx.kind))
    d.changed.update(DEBT_ENTITIES)
    if res.needs_account:
        d.slots.append(dc.needs_account_card(res, aid, account_options(d.ctx, tx.currency),
                                             op=op))
    else:
        d.slots.append(dc.created_card(res, aid, tz_name=d.ctx.timezone, today=d.ctx.today,
                                       op=op))


async def run_query(d: _Dispatcher, vi: ValidatedIntent) -> None:
    it, r = vi.intent, vi.resolved
    out = await debts_svc.list_debts(d.s, d.uid)
    if r.get("name"):
        d.slots.append(dc.person_answer_card(out, r.get("debt_id"), r["name"]))
    else:
        d.slots.append(dc.debts_answer_card(out, direction=getattr(it, "direction", None)))


# ------------------------------------------------------------------ edits of debt operations


async def debt_of(session: Any, tx: Transaction) -> Debt | None:
    """The person of a debt operation (``transactions.debt_op`` set): by its service account."""
    if not getattr(tx, "debt_op", None):
        return None
    ids = [i for i in (tx.account_id, tx.counter_account_id) if i is not None]
    if not ids:
        return None
    return await session.scalar(
        select(Debt).where(Debt.user_id == tx.user_id,
                           or_(*[Debt.account_id == i for i in ids])).limit(1)
    )


async def brief_for(session: Any, tx: Transaction, out: TransactionOut) -> DebtBrief | None:
    """``debt`` of an updated card («нет, 300к» on a debt operation): balance after the edit."""
    op = out.debt.op if out.debt else getattr(tx, "debt_op", None)
    if not op:
        return None
    debt = await debt_of(session, tx)
    if debt is None:
        return None
    bals = await debts_svc.balances_of(session, debt)
    bal = Decimal(str(bals.get(out.currency, bals.get(out.currency.upper(), 0)) or 0))
    excess = dc.excess_of(op, Decimal(out.amount), bal)  # «нет, 500к» into an overpayment
    return DebtBrief(debt_id=debt.id, name=debt.name, op=op, currency=out.currency,
                     balance_after=bal, excess=excess)


def relabel(card: Any, out: TransactionOut, brief: DebtBrief) -> None:
    """Debt wording for an updated / deleted card: «300 000 ₫ · Дал в долг: Коля». An edit into
    an overpayment says so like the created card: «Излишек … — теперь ты должен: Лёва» + the
    excess button (``UpdatedCard`` has no ``note``, so the text goes to the subtitle)."""
    title = dc.debt_title(out, brief.op, brief.name)
    if card.kind == "deleted":
        card.title = f"Удалено: {title}"
    else:
        card.title = title
        if card.kind == "updated":
            card.debt = brief
            parts = [dc.balance_text(brief.op, brief.name, brief.balance_after, brief.currency)]
            if brief.excess:
                parts.append(dc.excess_text(brief.op, brief.name, brief.excess, brief.currency))
                card.buttons.insert(0, dc.excess_button(brief.op, brief.name, brief.excess,
                                                        brief.currency))
            extra = " · ".join(parts)
            card.subtitle = f"{card.subtitle} · {extra}" if card.subtitle else extra


__all__ = ["DEBT_ENTITIES", "brief_for", "debt_of", "relabel", "run_debt", "run_query"]
