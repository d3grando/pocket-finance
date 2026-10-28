"""Read models: TransactionOut, Russian titles/summaries, action briefs."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import format_money
from app.core.timeutil import tz
from app.models import Account, AccountAsset, ActionLog, Category, Debt, Merchant, Transaction
from app.models.enums import DEBT_OUTFLOW, AccountKind, ActionKind, DebtOp, TxKind
from app.schemas.intents import ActionBrief
from app.schemas.transactions import (
    AccountRef,
    CategoryRef,
    MerchantRef,
    TransactionOut,
    TxDebtRef,
)


def debt_account_id(tx: Transaction) -> int | None:
    """The debt service account of a debt operation (docs/DEBTS.md §3.1): the transfer target
    for lend/repay_out, the source (``account_id``) for repay_in/borrow/forgive."""
    if tx.debt_op is None:
        return None
    return tx.counter_account_id if tx.debt_op in DEBT_OUTFLOW else tx.account_id


class Refs:
    """Bulk-loaded related rows for a set of transactions."""

    def __init__(self) -> None:
        self.tz: str | None = None
        self.accounts: dict[int, Account] = {}
        self.categories: dict[int, Category] = {}
        self.merchants: dict[int, Merchant] = {}
        #: debts by their service account id (debt operations only)
        self.debts: dict[int, Debt] = {}

    @classmethod
    async def load(cls, session: AsyncSession, txs: Iterable[Transaction]) -> Refs:
        r = cls()
        txs = list(txs)
        if txs:
            from app.services.ledger.common import user_tz

            r.tz = await user_tz(session, txs[0].user_id)
        acc_ids = {i for t in txs for i in (t.account_id, t.counter_account_id) if i}
        cat_ids = {t.category_id for t in txs if t.category_id}
        mer_ids = {t.merchant_id for t in txs if t.merchant_id}
        if acc_ids:
            for a in await session.scalars(select(Account).where(Account.id.in_(acc_ids))):
                r.accounts[a.id] = a
        if cat_ids:
            for c in await session.scalars(select(Category).where(Category.id.in_(cat_ids))):
                r.categories[c.id] = c
        if mer_ids:
            for m in await session.scalars(select(Merchant).where(Merchant.id.in_(mer_ids))):
                r.merchants[m.id] = m
        debt_acc = {
            i for i, a in r.accounts.items() if a.kind == AccountKind.debt
        } | {i for t in txs if (i := debt_account_id(t)) is not None}
        if debt_acc:
            for d in await session.scalars(select(Debt).where(Debt.account_id.in_(debt_acc))):
                r.debts[d.account_id] = d
        return r

    def debt_of(self, tx: Transaction) -> Debt | None:
        i = debt_account_id(tx)
        return self.debts.get(i) if i is not None else None

    def debt_name(self, tx: Transaction) -> str:
        d = self.debt_of(tx)
        return d.name if d is not None else (tx.counterparty or "?")

    def acc_name(self, i: int | None) -> str | None:
        a = self.accounts.get(i) if i else None
        return a.name if a else None

    def cat_name(self, i: int | None) -> str | None:
        c = self.categories.get(i) if i else None
        return c.name if c else None


TITLE_MAX = 80


def _clip(s: str, n: int = TITLE_MAX) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def tx_title(tx: Transaction, r: Refs) -> str:
    """Row title (DESIGN §9.4): description or merchant/recipient, else category.
    «обед Highlands», «аккаунт GPT Pro · @alice», «vast.ai», «Еда», «Возврат · наушники»,
    «Перевод Кошелёк → @myshop», «Обмен USDT → TON · Биржа». Clients show the category as the
    second line when it differs from the title."""
    if tx.debt_op is not None:
        head = f"{debt_label(tx)}: {r.debt_name(tx)}"
        desc = _clip(tx.description or "")
        return _clip(f"{head} · {desc}" if desc else head)
    acc = r.acc_name(tx.account_id)
    cacc = r.acc_name(tx.counter_account_id)
    if tx.kind == TxKind.transfer:
        return f"Перевод {acc or '?'} → {cacc or '?'}"
    if tx.kind == TxKind.exchange:
        if tx.account_id == tx.counter_account_id:
            return f"Обмен {tx.currency} → {tx.counter_asset} · {acc or '?'}"
        return f"Обмен {acc or '?'} → {cacc or '?'}"
    if tx.kind == TxKind.adjustment:
        return f"Корректировка · {acc or '?'}"
    cat = r.cat_name(tx.category_id) or "Без категории"
    m = r.merchants.get(tx.merchant_id) if tx.merchant_id else None
    who = (m.name if m else None) or tx.counterparty
    desc = _clip(tx.description or "")
    parts: list[str] = []
    if desc:
        parts.append(desc)
    if who and who.strip().lower() not in desc.lower():
        parts.append(who.strip())
    title = " · ".join(parts) if parts else cat
    return f"Возврат · {title}" if tx.kind == TxKind.refund else title


#: row / card titles of debt operations (docs/DEBTS.md §8), the name follows after «: »
DEBT_LABELS: dict[str, str] = {
    DebtOp.lend: "Дал в долг",
    DebtOp.repay_in: "Возврат долга",
    DebtOp.borrow: "Занял",
    DebtOp.repay_out: "Вернул долг",
}


def debt_label(tx: Transaction) -> str:
    if tx.debt_op == DebtOp.forgive:
        # forgiving what the person owed me is my expense; a forgiven own debt is income
        return "Долг прощён" if tx.kind == TxKind.income else "Списан долг"
    return DEBT_LABELS.get(str(tx.debt_op), "Долг")


def debt_summary(tx: Transaction, r: Refs) -> str:
    """Action summary of a debt operation (docs/DEBTS.md §4); the sign is the change of the debt
    balance («X должен мне», §3.1) — the same texts as the assistant (ai/debt_cards.op_summary):
    «Долг: Коля +500 000 ₫», «Возврат: Коля −200 000 ₫», «Занял: Петя −1 000 000 ₫»,
    «Вернул долг: Петя +500 000 ₫», «Списан долг: Коля −300 000 ₫»,
    «Долг прощён: Коля +100 000 ₫»."""
    name = r.debt_name(tx)
    op = str(tx.debt_op)
    if op == DebtOp.forgive:
        sign = 1 if tx.kind == TxKind.income else -1
        head = debt_label(tx)
    else:
        head, sign = {
            DebtOp.lend: ("Долг", 1),
            DebtOp.repay_in: ("Возврат", -1),
            DebtOp.borrow: ("Занял", -1),
            DebtOp.repay_out: ("Вернул долг", 1),
        }.get(op, ("Долг", 1))  # type: ignore[call-overload]
    return f"{head}: {name} {format_money(sign * tx.amount, tx.currency, sign='always')}"


def _money_label(tx: Transaction, r: Refs) -> tuple[str, str] | None:
    """(signed money, category) of a P&L operation; None for transfers/exchanges/adjustments."""
    if tx.kind not in (TxKind.expense, TxKind.income, TxKind.refund):
        return None
    money = format_money(tx.amount, tx.currency)
    if tx.kind == TxKind.income:
        money = f"+{money}"
    elif tx.kind == TxKind.refund:
        money = f"Возврат {money}"
    return money, r.cat_name(tx.category_id) or "Без категории"


def tx_summary(tx: Transaction, r: Refs) -> str:
    """Action summary, e.g. «150 000 ₫ · Еда» (undo → «Отменено: 150 000 ₫ · Еда»)."""
    if tx.debt_op is not None:
        return debt_summary(tx, r)
    money = format_money(tx.amount, tx.currency)
    if tx.kind == TxKind.transfer:
        return f"Перевод {money}: {r.acc_name(tx.account_id)} → {r.acc_name(tx.counter_account_id)}"
    if tx.kind == TxKind.exchange:
        cm = format_money(tx.counter_amount or 0, tx.counter_asset or tx.currency)
        return f"Обмен {money} → {cm}"
    if tx.kind == TxKind.adjustment:
        delta = format_money(tx.account_amount or 0, tx.account_asset or tx.currency,
                             sign="always")
        return f"Корректировка {r.acc_name(tx.account_id)}: {delta}"
    ml = _money_label(tx, r)
    assert ml is not None
    return f"{ml[0]} · {ml[1]}"


def change_text(old: Transaction, new: Transaction, r: Refs) -> str:
    """What an edit changed, in the direction old → new:
    «300 000 ₫ → 250 000 ₫ · Еда», «250 000 ₫ · Кофе → Еда», «250 000 ₫ · Еда»."""
    a, b = _money_label(old, r), _money_label(new, r)
    if a is None or b is None:
        sa, sb = tx_summary(old, r), tx_summary(new, r)
        return sb if sa == sb else f"{sa} → {sb}"
    money = b[0] if a[0] == b[0] else f"{a[0]} → {b[0]}"
    label = b[1] if a[1] == b[1] else f"{a[1]} → {b[1]}"
    return f"{money} · {label}"


def _account_ref(a: Account | None) -> AccountRef | None:
    return AccountRef(id=a.id, name=a.name, icon=a.icon) if a else None


def _category_ref(c: Category | None) -> CategoryRef | None:
    if c is None:
        return None
    return CategoryRef(
        id=c.id, name=c.name, kind=c.kind, icon=c.icon, color=c.color,  # type: ignore[arg-type]
        is_business=c.is_business, is_new=c.is_new,
    )


def build_out(tx: Transaction, r: Refs) -> TransactionOut:
    m = r.merchants.get(tx.merchant_id) if tx.merchant_id else None
    return TransactionOut(
        id=tx.id,
        kind=tx.kind,  # type: ignore[arg-type]
        status=tx.status,  # type: ignore[arg-type]
        occurred_at=tx.occurred_at.astimezone(tz(r.tz)) if r.tz else tx.occurred_at,
        occurred_on=tx.occurred_on,
        amount=tx.amount,
        currency=tx.currency,
        account=_account_ref(r.accounts.get(tx.account_id) if tx.account_id else None),
        account_amount=tx.account_amount,
        account_asset=tx.account_asset,
        account_rate=tx.account_rate,
        is_estimated=tx.is_estimated,
        counter_account=_account_ref(
            r.accounts.get(tx.counter_account_id) if tx.counter_account_id else None
        ),
        counter_amount=tx.counter_amount,
        counter_asset=tx.counter_asset,
        category=_category_ref(r.categories.get(tx.category_id) if tx.category_id else None),
        is_business=tx.is_business,
        merchant=MerchantRef(id=m.id, name=m.name) if m else None,
        counterparty=tx.counterparty,
        description=tx.description,
        amount_usd=tx.amount_usd,
        amount_vnd=tx.amount_vnd,
        usd_rate=tx.usd_rate,
        rate_date=tx.rate_date,
        rate_source=tx.rate_source,
        refund_of_id=tx.refund_of_id,
        recurring_id=tx.recurring_id,
        source=tx.source,  # type: ignore[arg-type]
        raw_text=tx.raw_text,
        created_action_id=tx.created_action_id,
        version=tx.version,
        created_at=tx.created_at,
        updated_at=tx.updated_at,
        title=tx_title(tx, r),
        debt=_debt_ref(tx, r),
    )


def _debt_ref(tx: Transaction, r: Refs) -> TxDebtRef | None:
    d = r.debt_of(tx)
    if d is None:
        return None
    return TxDebtRef(id=d.id, name=d.name, op=tx.debt_op)  # type: ignore[arg-type]


async def to_out(session: AsyncSession, tx: Transaction) -> TransactionOut:
    return build_out(tx, await Refs.load(session, [tx]))


async def to_out_many(session: AsyncSession, txs: list[Transaction]) -> list[TransactionOut]:
    r = await Refs.load(session, txs)
    return [build_out(t, r) for t in txs]


async def summary_for(session: AsyncSession, tx: Transaction) -> str:
    return tx_summary(tx, await Refs.load(session, [tx]))


async def action_summary(session: AsyncSession, ctx: Any) -> str:
    """Default Russian summary of an action from its first recorded entity."""
    tx_ids = [int(r["id"]) for r in ctx.entity_refs if r["type"] == "transaction"]
    kind = str(ctx.kind)
    if kind in (ActionKind.bulk_update, ActionKind.bulk_delete) and len(tx_ids) > 1:
        verb = "Изменено" if kind == ActionKind.bulk_update else "Удалено"
        return f"{verb} операций: {len(tx_ids)}"
    if tx_ids:
        tx = await session.get(Transaction, tx_ids[0])
        if tx is not None:
            s = await summary_for(session, tx)
            if kind == ActionKind.delete_transaction:
                return f"Удалено: {s}"
            if kind == ActionKind.update_transaction:
                return f"Изменено: {s}"
            if kind == ActionKind.restore_transaction:
                return f"Восстановлено: {s}"
            return s
    for ref in ctx.entity_refs:
        if ref["type"] == "account_asset":
            aa = await session.get(AccountAsset, int(ref["id"]))
            if aa is not None:
                acc = await session.get(Account, aa.account_id)
                val = (
                    format_money(aa.opening_balance, aa.asset)
                    if aa.opening_balance is not None
                    else "не задан"
                )
                return f"Остаток {acc.name if acc else '?'}: {val}"
        if ref["type"] == "category":
            c = await session.get(Category, int(ref["id"]))
            if c is not None:
                if kind == ActionKind.merge_categories:
                    continue
                return f"Категория «{c.name}»"
    if kind == ActionKind.merge_categories:
        return "Слияние категорий"
    return kind


async def briefs_for(session: AsyncSession, rows: list[ActionLog]) -> list[ActionBrief]:
    main_ids: dict[Any, int] = {}
    for a in rows:
        ids = [int(r["id"]) for r in (a.entity_refs or []) if r.get("type") == "transaction"]
        if ids:
            main_ids[a.id] = ids[0]
    txs: dict[int, Transaction] = {}
    if main_ids:
        for t in await session.scalars(
            select(Transaction).where(Transaction.id.in_(set(main_ids.values())))
        ):
            txs[t.id] = t
    r = await Refs.load(session, txs.values())
    out: list[ActionBrief] = []
    for a in rows:
        ids = [int(x["id"]) for x in (a.entity_refs or []) if x.get("type") == "transaction"]
        t = txs.get(main_ids.get(a.id, -1))
        out.append(
            ActionBrief(
                action_id=a.id,
                kind=a.kind,
                summary=a.summary,
                source=a.source,
                created_at=a.created_at,
                transaction_ids=ids,
                tx_kind=t.kind if t else None,
                amount=t.amount if t else None,
                currency=t.currency if t else None,
                category=r.cat_name(t.category_id) if t else None,
                account=r.acc_name(t.account_id) if t else None,
            )
        )
    return out
