"""Executes validated intents via service functions only. One DB transaction per
message; all-or-nothing (the pipeline wraps this in a SAVEPOINT). Builds result cards.

The model never reaches this module: only ``ValidatedIntent`` objects produced by the validator.
Every write goes through a ledger/analytics service function with Pydantic-checked
arguments (``TransactionCreate``, ``TransactionPatch``, ``TransferCreate`` …). No SQL from text.

Confirmation rules (ARCH §5.1): new operations and edit/delete of a single last/context/id
target run immediately (+ action id for undo); search targets or > 1 record → preview token.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.money import format_money, is_fiat
from app.core.principal import Principal
from app.models import ActionLog, Category, Transaction
from app.models.enums import ActionKind
from app.schemas.assistant import (
    AccountOption,
    AssistantResponse,
    BalanceCard,
    PreviewItem,
)
from app.schemas.categories import CategoryCreate, CategoryOut
from app.schemas.intents import (
    AdjustBalanceIntent,
    AnswerIntent,
    ClarifyIntent,
    CreateCategoryIntent,
    CreateTransactionIntent,
    DebtIntent,
    DebtsQueryIntent,
    DeleteTransactionIntent,
    NewCategory,
    ParseContext,
    SearchIntent,
    StatsIntent,
    TransferIntent,
    UndoIntent,
    UpdateTransactionIntent,
)
from app.schemas.transactions import (
    AdjustmentCreate,
    ExchangeCreate,
    TransactionCreate,
    TransactionOut,
    TransactionPatch,
    TransferCreate,
)
from app.services.accounts import balances as acc_balances
from app.services.ai import cards, debt_dispatch
from app.services.ai import resolve as rs
from app.services.ai import textnorm as tn
from app.services.ai.validator import ValidatedIntent
from app.services.categories import service as cat_svc
from app.services.events import bus
from app.services.ledger import actions as ledger_actions
from app.services.ledger import audit as ledger_audit
from app.services.ledger import bulk as ledger_bulk
from app.services.ledger import query as ledger_query
from app.services.ledger import transactions as ledger_tx
from app.services.ledger import transfers as ledger_transfers
from app.services.merchants import service as merchant_svc
from app.services.stats import service as stats_svc

ENTITY_ORDER = ["transactions", "accounts", "categories", "merchants", "debts", "stats"]
_KIND_RU = {"expense": "расход", "income": "доход", "refund": "возврат"}


async def dispatch(
    session: AsyncSession,
    principal: Principal,
    items: list[ValidatedIntent],
    ctx: ParseContext,
    *,
    raw_text: str,
    ai_request_id: int | None,
    route: str,
) -> AssistantResponse:
    """create_transaction → ledger.create_transaction (needs_account card if no account);
    transfer → ledger.create_transfer/create_exchange; adjust_balance → ledger.adjust_balance;
    update/delete → single target executes now, multi/search → ledger.bulk.create_preview;
    undo → ledger.actions.undo_action; search → ledger.query.list_transactions;
    stats → stats.service.answer_stats_intent; create_category → categories.create_category;
    answer/clarify → cards only."""
    d = _Dispatcher(session, principal, ctx, raw_text, ai_request_id)
    for vi in items:
        await d.run(vi)
    return await d.finish(route)


def account_options(ctx: ParseContext, currency: str) -> list[AccountOption]:
    """«С какого?» (max 8 + «Без счёта» (0)). Fiat ($, €…): accounts in that currency, then the
    default ₫ account («$ с Карта»), other fiat accounts, then multi-asset wallets.
    Crypto: wallets holding the coin, then other multi-asset wallets. Wallets of «Мои аккаунты»
    always go last — they must not push Карта/Наличные out of the list."""
    c = currency.upper()
    mine_ids = set((getattr(ctx, "my_account_ids", None) or {}).values())
    own = [a for a in ctx.accounts if a.id not in mine_ids]
    mine = [a for a in ctx.accounts if a.id in mine_ids]

    def wallets(accs: list) -> list:
        multi = [a for a in accs if a.is_multi_asset]
        return [a for a in multi if c in a.assets] + [a for a in multi if c not in a.assets]

    if is_fiat(c):
        single = [a for a in own if not a.is_multi_asset and a.currency and is_fiat(a.currency)]
        same = [a for a in single if a.currency.upper() == c]
        vnd_default = [a for a in single if "VND" in a.default_for_currencies and a not in same]
        rest = [a for a in single if a not in same and a not in vnd_default]
        ordered = same + vnd_default + rest + wallets(own) + wallets(mine)
    else:
        ordered = wallets(own) + wallets(mine)
    ordered = ordered[:8]
    opts = [
        AccountOption(account_id=a.id, label=a.name,
                      hint=f"есть {c}" if (c in a.assets and a.is_multi_asset) else None,
                      hotkey=str(i))
        for i, a in enumerate(ordered, 1)
    ]
    opts.append(AccountOption(account_id=None, label="Без счёта", hint=None, hotkey="0"))
    return opts


def describe_changes(b: TransactionOut, a: TransactionOut) -> list[str]:
    out: list[str] = []
    if (b.amount, b.currency) != (a.amount, a.currency):
        out.append(f"{format_money(b.amount, b.currency)} → {format_money(a.amount, a.currency)}")
    bc, ac = (b.category.name if b.category else None), (a.category.name if a.category else None)
    if bc != ac:
        out.append(f"{bc or 'без категории'} → {ac or 'без категории'}")
    ba, aa = (b.account.name if b.account else None), (a.account.name if a.account else None)
    if ba != aa:
        out.append(f"{ba or 'без счёта'} → {aa or 'без счёта'}")
    same_asset_follow = (  # account amount simply follows the amount in the same currency
        a.account_asset == a.currency and b.account_asset == b.currency
        and a.account_amount == a.amount and b.account_amount == b.amount
    )
    if (b.account_amount != a.account_amount and a.account_amount is not None
            and a.account_asset and not same_asset_follow):
        prev = format_money(b.account_amount, b.account_asset or a.account_asset,
                            approx=b.is_estimated) if b.account_amount is not None else "—"
        new = format_money(a.account_amount, a.account_asset, approx=a.is_estimated)
        out.append(f"списание {prev} → {new}")
    if b.occurred_on != a.occurred_on:
        out.append(f"{b.occurred_on:%d.%m} → {a.occurred_on:%d.%m}")
    if b.kind != a.kind:
        out.append(f"{_KIND_RU.get(b.kind, b.kind)} → {_KIND_RU.get(a.kind, a.kind)}")
    if (b.description or None) != (a.description or None):
        out.append(f"«{b.description or '—'}» → «{a.description or '—'}»")
    if (b.counterparty or None) != (a.counterparty or None):
        out.append(f"{b.counterparty or '—'} → {a.counterparty or '—'}")
    return out


class _Dispatcher:
    def __init__(
        self,
        session: AsyncSession,
        principal: Principal,
        ctx: ParseContext,
        raw_text: str,
        ai_request_id: int | None,
    ) -> None:
        self.s = session
        self.p = principal
        self.ctx = ctx
        self.raw = raw_text
        self.ai_id = ai_request_id
        self.slots: list[Any] = []  # cards or the "created" group marker
        self.created: list[tuple[TransactionOut, uuid.UUID, bool, str | None]] = []
        self.action_ids: list[uuid.UUID] = []
        self.changed: set[str] = set()
        self._new_cats: dict[tuple[str, str], Category] = {}
        #: edits that need confirmation, merged per kind of change → one preview card each
        self._previews: dict[str, dict[str, Any]] = {}

    @property
    def uid(self) -> int:
        return self.p.user_id

    async def run(self, vi: ValidatedIntent) -> None:
        it = vi.intent
        if isinstance(it, CreateTransactionIntent):
            await self.create(vi)
        elif isinstance(it, TransferIntent):
            await self.transfer(vi)
        elif isinstance(it, AdjustBalanceIntent):
            await self.adjust(vi)
        elif isinstance(it, UpdateTransactionIntent):
            await self.update(vi)
        elif isinstance(it, DeleteTransactionIntent):
            await self.delete(vi)
        elif isinstance(it, UndoIntent):
            await self.undo(vi)
        elif isinstance(it, SearchIntent):
            await self.search(vi)
        elif isinstance(it, StatsIntent):
            card = await stats_svc.answer_stats_intent(self.s, self.uid, it, self.ctx, vi.resolved)
            self.slots.append(card)
        elif isinstance(it, CreateCategoryIntent):
            await self.create_category(vi)
        elif isinstance(it, AnswerIntent):
            self.slots.append(cards.text_answer_card(it.text))
        elif isinstance(it, ClarifyIntent):
            self.slots.append(cards.unclear_card(it.reason, it.question, it.options,
                                                 input_text=self.raw))
        elif isinstance(it, DebtIntent):
            await debt_dispatch.run_debt(self, vi)
        elif isinstance(it, DebtsQueryIntent):
            await debt_dispatch.run_query(self, vi)
        else:  # pragma: no cover
            raise AppError("Неизвестное действие", code="validation_error", status=422)

    # ------------------------------------------------------------------ helpers
    async def _begin(self, kind: ActionKind) -> ledger_actions.ActionContext:
        return await ledger_actions.begin_action(
            self.s, self.p, kind, input_text=self.raw, ai_request_id=self.ai_id
        )

    async def _finish(self, act: ledger_actions.ActionContext, summary: str) -> uuid.UUID:
        await ledger_actions.finish_action(self.s, act, summary)
        self.action_ids.append(act.id)
        return act.id

    async def ensure_category(
        self, nc: NewCategory, act: ledger_actions.ActionContext | None = None
    ) -> Category:
        """Create a model-proposed category once per message (🆕, undoable with ``act``); reuse
        on a race/duplicate. An ARCHIVED category with that name is brought back (visible, 🆕)
        instead of silently filing the operation into a hidden category; undo re-archives it."""
        key = (nc.kind, tn.norm(nc.name))
        if key in self._new_cats:
            return self._new_cats[key]
        data = CategoryCreate(name=nc.name, kind=nc.kind, is_business=nc.is_business,
                              icon=nc.icon)
        try:
            cat = await cat_svc.create_category(self.s, self.p, data, is_new=True, action=act)
            self.changed.add("categories")
        except AppError as e:
            if e.code != "already_exists":
                raise
            cat = await self.s.scalar(
                select(Category).where(
                    Category.user_id == self.uid,
                    Category.kind == nc.kind,
                    func.lower(Category.name) == nc.name.lower(),
                    Category.merged_into_id.is_(None),
                ).with_for_update()
            )
            if cat is None:
                raise
            if cat.is_archived:
                before = ledger_audit.snapshot(cat)
                cat.is_archived = False
                cat.is_new = True
                cat.version = (cat.version or 1) + 1
                await self.s.flush()
                await ledger_audit.write_audit(
                    self.s, user_id=self.uid, entity_type="category", entity_id=cat.id,
                    operation="restore", before=before, after=ledger_audit.snapshot(cat),
                    source=self.p.source, action_id=act.id if act else None,
                )
                if act is not None:
                    act.record("category", cat.id, before, cat.version)
                self.changed.add("categories")
        self._new_cats[key] = cat
        return cat

    async def learn_merchant(self, mn: dict[str, Any], category_id: int | None,
                             act: ledger_actions.ActionContext) -> Any:
        """Remember a new merchant/@handle rule. Undo of the operation reverts the rule: a rule
        created here is neutralised (no category/account), an existing one gets its old state.
        The hit itself is counted once, by the ledger, when the operation is created."""
        existing = await merchant_svc.match_merchant(
            self.s, self.uid, name=mn["name"], handle=mn.get("handle"))
        before = ledger_audit.snapshot(existing) if existing is not None else None
        m = await merchant_svc.learn(
            self.s, self.uid, name=mn["name"], handle=mn.get("handle"), category_id=category_id,
            alias=mn.get("alias"), count_hit=False,
        )
        if before is None:
            before = ledger_audit.snapshot(m)
            before.update(category_id=None, default_account_id=None, hits=0)
        act.record("merchant", m.id, before, None)
        return m

    def _cat_name(self, cat_id: int | None) -> str | None:
        c = rs.category_by_id(self.ctx, cat_id)
        if c:
            return c.name
        return next((c.name for c in self._new_cats.values() if c.id == cat_id), None)

    # ------------------------------------------------------------------ create
    async def create(self, vi: ValidatedIntent) -> None:
        r = vi.resolved
        act = await self._begin(ActionKind.create_transaction)
        cat_id: int | None = r.get("category_id")
        if r.get("new_category") is not None:
            cat = await self.ensure_category(r["new_category"], act)
            cat_id = cat.id
        merchant_id = r.get("merchant_id")
        note = r.get("note")  # «Долга у Коли нет — записал как доход» (debts п.5)
        if r.get("merchant_new"):
            m = await self.learn_merchant(r["merchant_new"], cat_id, act)
            merchant_id = m.id
            self.changed.add("merchants")
            cname = self._cat_name(m.category_id) if m.category_id else None
            if cname:
                note = (f"{note}. " if note else "") + f"Запомню: {m.name} → {cname}"
        data = TransactionCreate(
            kind=r["kind"],
            amount=r["amount"],
            currency=r["currency"],
            account_id=r.get("account_id"),
            account_none=bool(r.get("account_none")),
            category_id=cat_id,
            merchant_id=merchant_id,
            counterparty=r.get("counterparty"),
            description=r.get("description"),
            occurred_on=r.get("occurred_on"),
            occurred_at=r.get("occurred_at"),
        )
        tx = await ledger_tx.create_transaction(
            self.s, self.p, data, raw_text=self.raw, ai_request_id=self.ai_id, action=act
        )
        out = await ledger_tx.to_out(self.s, tx)
        aid = await self._finish(act, cards.tx_summary(out))
        self.changed.update({"transactions", "stats"})
        if out.account:
            self.changed.add("accounts")
        # 🆕 + «В другую» while the category is not confirmed — also for the 2nd operation in it
        is_new = bool(out.category and out.category.is_new)
        if out.status == "needs_account":
            opts = account_options(self.ctx, out.currency)
            self.slots.append(cards.needs_account_card(out, aid, opts))
        else:
            self._add_created(out, aid, is_new, note)

    def _add_created(self, out: TransactionOut, aid: uuid.UUID, is_new: bool, note: str | None):
        if not self.created:
            self.slots.append("__created__")
        self.created.append((out, aid, is_new, note))

    # ------------------------------------------------------------------ transfer / exchange
    async def transfer(self, vi: ValidatedIntent) -> None:
        r = vi.resolved
        if r.get("is_exchange"):
            act = await self._begin(ActionKind.exchange)
            data_x = ExchangeCreate(
                from_account_id=r["from_account_id"], to_account_id=r["to_account_id"],
                amount=r["amount"], currency=r["currency"], counter_amount=r["counter_amount"],
                counter_currency=r["counter_currency"], description=r.get("description"),
                occurred_on=r.get("occurred_on"),
            )
            tx = await ledger_transfers.create_exchange(
                self.s, self.p, data_x, raw_text=self.raw, ai_request_id=self.ai_id, action=act
            )
        else:
            act = await self._begin(ActionKind.transfer)
            data_t = TransferCreate(
                from_account_id=r["from_account_id"], to_account_id=r["to_account_id"],
                amount=r["amount"], currency=r["currency"], description=r.get("description"),
                occurred_on=r.get("occurred_on"),
            )
            tx = await ledger_transfers.create_transfer(
                self.s, self.p, data_t, raw_text=self.raw, ai_request_id=self.ai_id, action=act
            )
        out = await ledger_tx.to_out(self.s, tx)
        aid = await self._finish(act, cards.tx_summary(out))
        self.changed.update({"transactions", "accounts", "stats"})
        self._add_created(out, aid, False, None)

    # ------------------------------------------------------------------ reconciliation
    async def adjust(self, vi: ValidatedIntent) -> None:
        r = vi.resolved
        act = await self._begin(ActionKind.adjust_balance)
        res = await ledger_transfers.adjust_balance(
            self.s, self.p,
            AdjustmentCreate(account_id=r["account_id"], asset=r["asset"],
                             actual_balance=r["amount"]),
            raw_text=self.raw, action=act,
        )
        acc = rs.account_by_id(self.ctx, r["account_id"])
        name = acc.name if acc else "Счёт"
        bal = format_money(res.balance, res.asset)
        aid: uuid.UUID | None = None
        if res.kind == "matches":
            subtitle = "Сходится — ничего не менял"
            discard = getattr(ledger_actions, "discard_action", None)
            if discard is not None:  # begin_action already inserted the row
                await discard(self.s, act)
        else:
            summary = f"Сверка {name}: {bal}"
            aid = await self._finish(act, summary)
            self.changed.update({"accounts", "transactions", "stats"})
            if res.kind == "adjusted" and res.delta is not None:
                subtitle = f"корректировка {format_money(res.delta, res.asset, sign='always')}"
            else:
                subtitle = "Остаток задан"
        account = await acc_balances.account_out(self.s, self.uid, r["account_id"])
        self.slots.append(
            BalanceCard(
                title=f"{name}: {bal}",
                subtitle=subtitle,
                action_id=res.action_id or aid,
                account=account,
                result=res.kind,
                transaction=res.transaction,
                buttons=[cards.undo_btn(res.action_id or aid)] if (res.action_id or aid) else [],
            )
        )

    # ------------------------------------------------------------------ edit / delete
    async def update(self, vi: ValidatedIntent) -> None:
        r = vi.resolved
        if r["preview"]:
            self._queue_preview("bulk_update", vi)
            return
        txs: list[Transaction] = r["transactions"]
        patch = dict(r["patch"])
        nc = patch.pop("new_category", None)
        tx = txs[0]
        before = await ledger_tx.to_out(self.s, tx)
        act = await self._begin(ActionKind.update_transaction)
        if nc is not None:
            patch["category_id"] = (await self.ensure_category(nc, act)).id
        tx2 = await ledger_tx.update_transaction(
            self.s, self.p, tx.id, TransactionPatch(version=before.version, **patch), action=act
        )
        after = await ledger_tx.to_out(self.s, tx2)
        brief = await debt_dispatch.brief_for(self.s, tx2, after)
        summary = cards.tx_summary(after)
        if brief is not None:
            summary = debt_dispatch.dc.op_summary(brief.op, brief.name, after.amount,
                                                  after.currency, after.kind)
        aid = await self._finish(act, summary)
        self.changed.update({"transactions", "accounts", "stats"})
        card = cards.updated_card(after, aid, describe_changes(before, after))
        if brief is not None:
            debt_dispatch.relabel(card, after, brief)
            self.changed.add("debts")
        if after.category and after.category.is_new:
            card.buttons.insert(0, cards.btn("change-category", "В другую", "change_category",
                                             style="outline", transaction_id=after.id))
        self.slots.append(card)

    async def delete(self, vi: ValidatedIntent) -> None:
        r = vi.resolved
        if r["preview"]:
            self._queue_preview("bulk_delete", vi)
            return
        txs: list[Transaction] = r["transactions"]
        tx = txs[0]
        before = await ledger_tx.to_out(self.s, tx)
        brief = await debt_dispatch.brief_for(self.s, tx, before)
        act = await self._begin(ActionKind.delete_transaction)
        await ledger_tx.delete_transaction(self.s, self.p, tx.id, version=before.version,
                                           action=act)
        summary = cards.tx_summary(before)
        if brief is not None:
            summary = debt_dispatch.dc.op_summary(brief.op, brief.name, before.amount,
                                                  before.currency, before.kind)
        aid = await self._finish(act, summary)
        self.changed.update({"transactions", "accounts", "stats"})
        card = cards.deleted_card(before, aid)
        if brief is not None:
            debt_dispatch.relabel(card, before, brief)
            self.changed.add("debts")
        self.slots.append(card)

    def _queue_preview(self, op: str, vi: ValidatedIntent) -> None:
        """All edits of one message that need confirmation become ONE preview per kind of
        change (deletes together; updates with the same patch together) — «удали кофе 55к и кофе
        85к» is one «Удалю 2 операции», never two immediate deletes."""
        key = op if op == "bulk_delete" else "bulk_update:" + _patch_key(vi.resolved["patch"])
        g = self._previews.get(key)
        if g is None:
            g = {"op": op, "vi": vi, "txs": []}
            self._previews[key] = g
            self.slots.append(("__preview__", key))
        for t in vi.resolved["transactions"]:
            if all(t.id != x.id for x in g["txs"]):
                g["txs"].append(t)

    async def _preview(self, op: str, vi: ValidatedIntent, txs: list[Transaction]) -> Any:
        intent = vi.intent
        assert isinstance(intent, (UpdateTransactionIntent, DeleteTransactionIntent))
        patch: dict | None = None
        if isinstance(intent, UpdateTransactionIntent):
            # ledger wants refs already resolved to ids (TransactionPatch fields).
            patch = dict(vi.resolved["patch"])
            nc = patch.pop("new_category", None)
            if nc is not None:
                patch["category_id"] = (await self.ensure_category(nc)).id
        pt = await ledger_bulk.create_preview(self.s, self.p, intent, txs, patch=patch)
        outs = [await ledger_tx.to_out(self.s, t) for t in txs]
        items: list[PreviewItem] = []
        summary_parts: list[str] = []
        if isinstance(intent, UpdateTransactionIntent):
            ch = intent.changes
            for o in outs:
                before, after = self._preview_pair(o, ch, vi.resolved["patch"])
                items.append(PreviewItem(transaction=o, before=before, after=after))
            summary_parts = self._change_summary(outs, ch, vi.resolved["patch"])
        else:
            items = [PreviewItem(transaction=o, before=format_money(o.amount, o.currency),
                                 after=None) for o in outs]
            summary_parts = ["удаление"]
        currencies = {o.currency for o in outs}
        total = None
        if len(currencies) == 1:
            cur = next(iter(currencies))
            total = "Итого " + format_money(sum((o.amount for o in outs), Decimal(0)), cur)
        return cards.preview_card(
            token=pt.id, operation=op, change_summary="; ".join(summary_parts),
            items=items, total_count=len(outs), total=total, expires_at=pt.expires_at,
        )

    def _preview_pair(self, o: TransactionOut, ch, patch: dict) -> tuple[str | None, str | None]:
        if ch.amount is not None:
            return (format_money(o.amount, o.currency),
                    format_money(ch.amount, ch.currency or o.currency))
        if ch.account_amount is not None and o.account_asset:
            return (format_money(o.account_amount or 0, o.account_asset, approx=o.is_estimated),
                    format_money(ch.account_amount, o.account_asset))
        if "category_id" in patch or ch.new_category is not None:
            new = ch.new_category.name if ch.new_category else self._cat_name(patch["category_id"])
            return (o.category.name if o.category else "—", new)
        if ch.occurred_on is not None:
            return (f"{o.occurred_on:%d.%m}", f"{ch.occurred_on:%d.%m}")
        if "account_id" in patch or ch.account_none:
            return (o.account.name if o.account else "без счёта", ch.account_ref or "без счёта")
        return (format_money(o.amount, o.currency), None)

    def _change_summary(self, outs: list[TransactionOut], ch, patch: dict) -> list[str]:
        parts: list[str] = []
        if "category_id" in patch or ch.new_category is not None:
            new = ch.new_category.name if ch.new_category else self._cat_name(patch["category_id"])
            olds = {o.category.name if o.category else "—" for o in outs}
            parts.append(f"категория: {olds.pop() + ' ' if len(olds) == 1 else ''}→ {new}")
        if ch.amount is not None:
            parts.append(f"сумма → {format_money(ch.amount, ch.currency or outs[0].currency)}")
        if ch.account_amount is not None:
            asset = outs[0].account_asset or outs[0].currency
            parts.append(f"списание → {format_money(ch.account_amount, asset)}")
        if "account_id" in patch or ch.account_none:
            parts.append(f"счёт → {ch.account_ref or 'без счёта'}")
        if ch.occurred_on is not None:
            parts.append(f"дата → {ch.occurred_on:%d.%m.%Y}")
        if ch.description is not None:
            parts.append(f"описание → «{ch.description}»")
        if ch.counterparty is not None:
            parts.append(f"получатель → {ch.counterparty}")
        if ch.kind is not None:
            parts.append(f"тип → {_KIND_RU.get(ch.kind, ch.kind)}")
        return parts or ["изменение"]

    # ------------------------------------------------------------------ undo
    async def _last_undoable(self) -> ActionLog | None:
        """«отмени» = the newest action that is not undone yet, skipping undo actions themselves
        («Отменено: …»): «отмени» twice undoes two actions instead of undoing the undo. A redo
        («Возвращено: …», from the «Вернуть» button) is a real change and can be undone."""
        return await self.s.scalar(
            select(ActionLog)
            .where(
                ActionLog.user_id == self.uid,
                ActionLog.undone_at.is_(None),
                ActionLog.is_undoable.is_(True),
                ActionLog.summary != "",
                ~(
                    (ActionLog.kind == str(ActionKind.undo))
                    & ActionLog.summary.startswith("Отменено")
                ),
            )
            .order_by(ActionLog.created_at.desc())
            .limit(1)
        )

    async def undo(self, vi: ValidatedIntent) -> None:
        aid = vi.resolved.get("action_id")
        row: ActionLog | None
        if aid is None:
            row = await self._last_undoable()
            if row is None:
                self.slots.append(cards.error_card("nothing_to_undo", "Нечего отменять"))
                return
            aid = row.id
        else:
            row = await self.s.get(ActionLog, aid)
        before_kind, before_summary = (row.kind, row.summary) if row is not None else (None, "")
        res = await ledger_actions.undo_action(self.s, self.p, aid)
        self.changed.update(res.changed)
        if res.undo_action_id:
            self.action_ids.append(res.undo_action_id)
        title = res.summary
        if res.status != "already_undone" and before_summary:
            title = _undo_title(before_kind, before_summary)
        self.slots.append(
            cards.undone_card(title, res.undone_action_id, res.undo_action_id,
                              already=res.status == "already_undone")
        )

    # ------------------------------------------------------------------ read side
    async def search(self, vi: ValidatedIntent) -> None:
        sf = vi.resolved["search_filter"]
        f = ledger_query.search_filter_to_filter(sf, self.ctx)
        f = f.model_copy(update={"limit": min(sf.limit, 20), "offset": 0})
        page = await ledger_query.list_transactions(self.s, self.uid, f)
        self.slots.append(
            cards.search_card(
                list(page.items), page.total, sf, tz_name=self.ctx.timezone, today=self.ctx.today,
                expense_vnd=page.totals.expense_vnd if page.totals else None,
            )
        )

    async def create_category(self, vi: ValidatedIntent) -> None:
        it = vi.intent
        assert isinstance(it, CreateCategoryIntent)
        existing = vi.resolved.get("existing_category_id")
        if existing:
            name = self._cat_name(existing) or it.name
            self.slots.append(cards.info_card(f"Категория «{name}» уже есть",
                                              "Новую не создаю, чтобы не было дублей"))
            return
        act = await self._begin(ActionKind.create_category)
        cat = await cat_svc.create_category(
            self.s, self.p,
            CategoryCreate(name=it.name, kind=it.kind, is_business=it.is_business, icon=it.icon),
            is_new=False,
        )
        act.record("category", cat.id, None, cat.version)
        aid = await self._finish(act, f"Категория «{cat.name}»")
        self.changed.add("categories")
        self.slots.append(cards.category_card(CategoryOut.model_validate(cat), aid))

    # ------------------------------------------------------------------ response
    async def finish(self, route: str) -> AssistantResponse:
        out_cards: list[Any] = []
        for slot in self.slots:
            if isinstance(slot, tuple) and slot[0] == "__preview__":
                g = self._previews[slot[1]]
                out_cards.append(await self._preview(g["op"], g["vi"], g["txs"]))
            elif slot == "__created__":
                if len(self.created) == 1:
                    tx, aid, is_new, note = self.created[0]
                    out_cards.append(cards.created_card(
                        tx, aid, category_is_new=is_new, tz_name=self.ctx.timezone,
                        today=self.ctx.today, note=note,
                    ))
                else:
                    out_cards.append(cards.created_many_card(
                        [c[0] for c in self.created], [c[1] for c in self.created]
                    ))
            else:
                out_cards.append(slot)
        changed = [e for e in ENTITY_ORDER if e in self.changed]
        if changed:
            await bus.publish(
                self.s, self.uid, changed,
                action_id=self.action_ids[0] if self.action_ids else None,
                source=self.p.source,
            )
        return AssistantResponse(
            request_id=None,
            route=route,  # type: ignore[arg-type]
            cards=out_cards,
            action_ids=self.action_ids,
            changed=changed,
            ai_available=True,
        )


def _patch_key(patch: dict[str, Any]) -> str:
    def val(v: Any) -> Any:
        return v.model_dump(mode="json") if hasattr(v, "model_dump") else str(v)

    return json.dumps({k: val(v) for k, v in patch.items()}, sort_keys=True, ensure_ascii=False)


def _undo_title(kind: str | None, summary: str) -> str:
    """What the user sees after undoing an action: «Отменено: X» for a normal action; undoing an
    undo is a redo («Возвращено: X»); undoing a redo removes X again («Отменено: X»)."""
    if kind == str(ActionKind.undo):
        if summary.startswith("Отменено: "):
            return "Возвращено: " + summary.removeprefix("Отменено: ")
        if summary.startswith("Возвращено: "):
            return "Отменено: " + summary.removeprefix("Возвращено: ")
    return f"Отменено: {summary}"
