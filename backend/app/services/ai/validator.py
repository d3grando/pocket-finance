"""Validation of intents before any write. Nothing is written if this fails.

Every intent (from the parser or the LLM) passes here: amounts are recomputed from ``*_raw`` by
the parser's ``parse_amount`` (parser wins), currencies/dates/targets are checked, references are
resolved to ids, categories are de-duplicated. LLM-originated intents get extra safety checks
(amount must come from the text, ids must come from the context, sanity caps): a failure there is
``reason="unsafe"`` → the pipeline answers honestly «AI недоступен» and writes nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import FIAT, format_money, is_fiat
from app.core.timeutil import tz, utcnow
from app.models import Transaction
from app.schemas.intents import (
    AdjustBalanceIntent,
    AnswerIntent,
    ClarifyIntent,
    CreateCategoryIntent,
    CreateTransactionIntent,
    CtxAccount,
    DebtIntent,
    DebtsQueryIntent,
    DeleteTransactionIntent,
    Intent,
    NewCategory,
    ParseContext,
    ParseResult,
    SearchFilter,
    SearchIntent,
    StatsIntent,
    TargetSpec,
    TransactionChanges,
    TransferIntent,
    UndoIntent,
    UpdateTransactionIntent,
)
from app.services.ai import compat, guards
from app.services.ai import resolve as rs
from app.services.ai import textnorm as tn

MAX_INTENTS = 10
BULK_LIMIT = 100
#: an actual charge («васт списали 1.3м») further than this from the reference rate → clarify
CHARGE_RATE_TOLERANCE = Decimal("0.15")
CHARGE_LOOKBACK_DAYS = 31
Origin = Literal["parser", "llm"]
_MUTATING = (CreateTransactionIntent, TransferIntent, AdjustBalanceIntent, UpdateTransactionIntent,
             DeleteTransactionIntent, UndoIntent, CreateCategoryIntent, DebtIntent)
_EDITS = (UpdateTransactionIntent, DeleteTransactionIntent)
_PNL = ("expense", "income", "refund")
#: parser reasons after which the LLM's date must not be trusted (the parser saw a future /
#: weekend date — «в эту пятницу», «на выходных»)
_DATE_REASONS = ("future_date", "ambiguous_date")
_CHARGE_STOP = {"по факту", "по", "нам", "мне", "у меня", "с меня", "с нас", "сегодня", "вчера",
                "там", "а", "и", "уже", "фактически"}
_ICON_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,31}")
_CHARGE_WHO_RE = re.compile(
    r"^\s*(?P<who>[^\d\s,.:;!?]+(?:\s+[^\d\s,.:;!?]+)?)\s+(?:по\s+факту\s+)?"
    r"(?:списал\w*|сняли|снял\w*|charged)",
    re.I | re.U,
)


@dataclass
class ValidatedIntent:
    """Intent with refs resolved to ids and amounts recomputed by the parser.

    ``intent`` — the (possibly converted) intent, e.g. transfer to a foreign @handle becomes
    an expense CreateTransactionIntent with counterparty; ``resolved`` — ids:
    {"account_id", "counter_account_id", "category_id", "new_category", "merchant_id",
     "transaction_ids", "search_filter", ...}.
    """

    intent: Intent
    resolved: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ValidationFailure:
    message: str  # Russian, shown to the user
    # validation_failed | ambiguous_currency | ambiguous_target | missing_amount | not_found
    # | unknown | other | unsafe (LLM output rejected by a safety check; the card shows the real
    # reason — see ``card_reason``)
    reason: str = "validation_failed"
    options: list[str] = field(default_factory=list)
    #: for reason="unsafe": how to show it (missing_amount → «Не хватает суммы», else an error
    #: card with ``message``). Never a fake «AI недоступен».
    card_reason: str | None = None


class _Fail(Exception):
    def __init__(self, failure: ValidationFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


async def validate(
    session: AsyncSession,
    intents: list[Intent],
    ctx: ParseContext,
    *,
    text: str | None = None,
    origin: Origin = "parser",
    parse_result: ParseResult | None = None,
) -> list[ValidatedIntent] | ValidationFailure:
    """Checks: amounts recomputed from *_raw via parser.amounts.parse_amount (parser wins);
    currency known & unambiguous; date not > 1 day in future; refs resolve (account, category —
    unknown category → new_category with similarity check first); target exists; at most
    10 intents; transfer to non-own handle → expense («Переводы людям» or by meaning).

    Whole-message rules (nothing is written if any fails): a clarify next to writes → only the
    question; one undo per message; the LLM may not override what the parser flagged (future /
    weekend date, per-unit price, ambiguous «1.500»); edits touching > 1 record, a record found
    by search or a record that is not the last one → preview (``resolved["preview"]``)."""
    if not intents:
        return ValidationFailure("Не понял, что записать", reason="unknown")
    if len(intents) > MAX_INTENTS:
        return ValidationFailure(f"Слишком много действий в одном сообщении (больше {MAX_INTENTS})")
    pre = _message_rules(intents, ctx, text or "", origin, parse_result)
    if pre is not None:
        return pre
    v = _Validator(session, ctx, text or "", origin)
    out: list[ValidatedIntent] = []
    try:
        for it in intents:
            out.append(await v.one(it))
        if origin == "llm":
            v.check_per_unit(out)
    except _Fail as f:
        return f.failure
    _confirmation_policy(out)
    return out


def _message_rules(
    intents: list[Intent], ctx: ParseContext, text: str, origin: Origin,
    pr: ParseResult | None,
) -> ValidationFailure | None:
    clarify = next((i for i in intents if isinstance(i, ClarifyIntent)), None)
    if clarify is not None and any(isinstance(i, _MUTATING) for i in intents):
        # «еда 100к, кофе 0» → create + clarify: «если проверка не прошла, ничего не
        # записывается» — ask first, write nothing.
        q = clarify.question.strip() or "Уточните, что записать"
        return ValidationFailure(f"Ничего не записал. {q}", reason=clarify.reason,
                                 options=list(clarify.options))
    if sum(isinstance(i, UndoIntent) for i in intents) > 1:
        return ValidationFailure(
            "Отменяю по одному действию. Напишите «отмени» ещё раз или нажмите «Отменить» "
            "под нужной карточкой", reason="ambiguous_target", options=["отмени"])
    if origin != "llm" or pr is None or not pr.reason:
        return None
    writes = [i for i in intents
              if isinstance(i, (CreateTransactionIntent, TransferIntent, DebtIntent))]
    if pr.reason in _DATE_REASONS and writes:
        own = next((i for i in pr.intents if isinstance(i, ClarifyIntent)), None)
        if own is not None:
            return ValidationFailure(own.question, reason=own.reason, options=list(own.options))
        return ValidationFailure(
            "Дата в будущем — будущие операции не записываю. Укажите прошедший день, например "
            "«вчера» или дату «24.10»", reason="other")
    if pr.reason == "ambiguous_amount" and writes:
        return _ambiguous_amount_failure(text)
    return None


def _ambiguous_amount_failure(text: str) -> ValidationFailure:
    """«1.500 usdt» — 1500 или 1.5? Never guessed (×1000 difference)."""
    m = re.search(r"(?<![\d.,])(\d{1,3})[.,](\d{3})(?![\d.,])", text)
    options: list[str] = []
    question = "Сумма неоднозначна — уточните"
    if m:
        tok, whole, frac = m.group(0), m.group(1) + m.group(2), f"{m.group(1)}.{m.group(2)}"
        frac = frac.rstrip("0").rstrip(".") or "0"
        question = f"«{tok}» — это {whole} или {frac}?"
        options = [text.replace(tok, whole, 1), text.replace(tok, frac, 1)]
    return ValidationFailure(question, reason="other", options=options)


def _confirmation_policy(out: list[ValidatedIntent]) -> None:
    """«всё, что задевает больше одной существующей записи … — превью»: several edit
    intents (each with one target) are one change of several records → all go to preview."""
    edits = [v for v in out if isinstance(v.intent, _EDITS)]
    ids = {i for v in edits for i in v.resolved.get("transaction_ids", [])}
    if len(ids) > 1:
        for v in edits:
            v.resolved["preview"] = True


class _Validator:
    def __init__(self, session: AsyncSession, ctx: ParseContext, text: str, origin: Origin):
        self.s = session
        self.ctx = ctx
        self.text = text
        self.origin = origin
        self.uid = getattr(ctx, "user_id", None)
        self.my_ids: dict[str, int] = dict(getattr(ctx, "my_account_ids", {}) or {})
        for h in ctx.my_handles:
            self.my_ids.setdefault(h.lower().lstrip("@"), 0)
        self._text_currencies: set[str] | None = None

    # ------------------------------------------------------------------ helpers
    def fail(self, message: str, reason: str = "validation_failed", options=None) -> _Fail:
        return _Fail(ValidationFailure(message, reason, list(options or [])))

    def unsafe(self, message: str, card_reason: str | None = None) -> _Fail:
        """Safety failure: unsafe for LLM output, a plain validation failure for the parser.
        The card shows ``message`` (the real reason), never «AI недоступен»."""
        if self.origin != "llm":
            return self.fail(message, card_reason or "validation_failed")
        return _Fail(ValidationFailure(message, "unsafe", [], card_reason))

    @property
    def text_currencies(self) -> set[str]:
        if self._text_currencies is None:
            self._text_currencies = compat.currencies_in_text(self.text)
        return self._text_currencies

    def parse(self, raw: str, currency: str | None, *, allow_zero: bool = False) -> Decimal:
        return compat.parse_amount(raw, currency, small_thousands=self.ctx.small_amount_thousands,
                                   allow_zero=allow_zero)

    def account(self, ref: str | None) -> CtxAccount | None:
        if not ref:
            return None
        try:
            acc = rs.resolve_account(self.ctx, ref)
        except rs.Ambiguous as e:
            raise self.fail(
                f"Счёт «{ref}» неоднозначен: {', '.join(e.candidates)}",
                "ambiguous_target",
                [f"{self.text} — {c}" for c in e.candidates][:4] if self.text else [],
            ) from e
        if acc is None:
            names = ", ".join(a.name for a in self.ctx.accounts[:9])
            raise self.fail(f"Не знаю счёт «{ref}». Есть: {names}" if names else
                            f"Не знаю счёт «{ref}»")
        return acc

    def currency_justified(self, c: str, account: CtxAccount | None = None,
                           extra: tuple[str | None, ...] = ()) -> bool:
        """An LLM currency must come from the text, the default currency, the account or the
        record being edited — the model may not «decide» that «600к» was in USD."""
        allowed = {(self.ctx.default_currency or "VND").upper()}
        allowed |= {x.upper() for x in extra if x}
        if account is not None and account.currency and not account.is_multi_asset:
            allowed.add(account.currency.upper())
        return c in allowed or c in self.text_currencies or (
            len(c) >= 3 and re.search(rf"(?<![^\W\d_]){re.escape(c.lower())}(?![^\W\d_])",
                                      self.text.lower()) is not None)

    def currency(self, cur: str | None, account: CtxAccount | None = None, *,
                 extra: tuple[str | None, ...] = ()) -> str:
        if cur:
            c = cur.upper()
            if self.origin == "llm" and not self.currency_justified(c, account, extra):
                if c in guards.COMMON_ASSETS or c in {a.upper() for a in self.ctx.known_assets}:
                    raise self.fail(
                        f"В тексте нет валюты «{c}». Напишите, например, «15 $» или «300 USDT»",
                        "ambiguous_currency",
                    )
                raise self.fail(
                    f"Не знаю валюту «{c}». Напишите, например, «15 $» или «300 USDT»",
                    "ambiguous_currency",
                )
            return c
        if account is not None and not account.is_multi_asset and account.currency:
            return account.currency.upper()
        return (self.ctx.default_currency or "VND").upper()

    def soft_currency(self, cur: str | None) -> str | None:
        """Read side (search/stats): an unjustified LLM currency is dropped, not an error."""
        if not cur:
            return None
        c = cur.upper()
        if self.origin == "llm" and not self.currency_justified(c):
            return None
        return c

    def amount(
        self, amount: Decimal | None, raw: str | None, currency: str, *, allow_zero: bool = False
    ) -> Decimal:
        value = amount
        if raw:
            try:
                parsed = self.parse(raw, currency, allow_zero=allow_zero)
            except (ValueError, ArithmeticError):
                parsed = None
            if parsed is not None:
                value = parsed  # parser wins over the model's arithmetic
        if value is None:
            raise self.fail("Не хватает суммы", "missing_amount")
        if value < 0 or (value == 0 and not allow_zero):
            raise self.fail("Сумма должна быть больше нуля")
        if guards.too_precise(value):
            raise self.fail(f"Слишком мелкая сумма {value:f} {currency} — не больше 8 знаков "
                            "после запятой")
        llm = self.origin == "llm"
        if llm and self.text and not guards.amount_explained(
            value, raw, self.text, currency=currency,
            parse=lambda r, c: self.parse(r, c, allow_zero=allow_zero),
        ):
            raise self.unsafe(
                "Не нашёл эту сумму в тексте — напишите её цифрами, например «ужин 600к»",
                "missing_amount",
            )
        if guards.amount_too_large(value, currency):
            raise self.unsafe(f"Слишком большая сумма: {format_money(value, currency)} — проверьте")
        return value

    def day(self, d: date | None) -> date | None:
        if d is None:
            return None
        today = self.ctx.today
        if d > today + timedelta(days=1):
            raise self.fail(f"Дата {d.strftime('%d.%m.%Y')} в будущем — проверьте")
        if d < today - timedelta(days=3660):
            raise self.fail(f"Дата {d.strftime('%d.%m.%Y')} слишком старая — проверьте")
        return d

    def category(
        self,
        kind: str,
        *,
        ref: str | None = None,
        new: NewCategory | None = None,
        merchant_category_id: int | None = None,
        for_resale: bool = False,
        to_person: bool = False,
    ) -> dict[str, Any]:
        """→ {"category_id": id} or {"new_category": NewCategory} (de-duplicated)."""
        ck: rs.CatKind = "income" if kind == "income" else "expense"
        if for_resale and ck == "expense":
            c = rs.category_by_name(self.ctx, "Закупка товара", ck)
            if c:
                return {"category_id": c.id}
        if ref:
            r = rs.find_category(self.ctx, ref, ck)
            if r.category and r.score >= rs.DEDUP_THRESHOLD:
                return {"category_id": r.category.id}
        if merchant_category_id and (m := rs.category_by_id(self.ctx, merchant_category_id)):
            if m.kind == ck:
                return {"category_id": m.id}
        proposal = new.name if new else ref
        if proposal:
            r = rs.find_category(self.ctx, proposal, ck)
            if r.category and r.score >= rs.DEDUP_THRESHOLD:
                return {"category_id": r.category.id}
            name = tn.clean_category_name(proposal)
            if name and not guards.bad_name(name):
                return {
                    "new_category": NewCategory(
                        name=name,
                        kind=ck,
                        is_business=bool(new.is_business) if new else False,
                        icon=_icon(new.icon) if new else None,
                    )
                }
        if to_person and ck == "expense":
            c = rs.category_by_name(self.ctx, "Переводы людям", ck)
            if c:
                return {"category_id": c.id}
        c = rs.category_by_name(self.ctx, "Прочее", ck) or next(
            (x for x in self.ctx.categories if x.kind == ck), None
        )
        if c is None:
            raise self.fail("Нет ни одной категории — добавьте категории в настройках")
        return {"category_id": c.id}

    # ------------------------------------------------------------------ dispatch by type
    async def one(self, it: Intent) -> ValidatedIntent:
        if isinstance(it, CreateTransactionIntent):
            return self.create(it)
        if isinstance(it, TransferIntent):
            return self.transfer(it)
        if isinstance(it, AdjustBalanceIntent):
            return self.adjust(it)
        if isinstance(it, UpdateTransactionIntent):
            return await self.update(it)
        if isinstance(it, DeleteTransactionIntent):
            return await self.delete(it)
        if isinstance(it, UndoIntent):
            return self.undo(it)
        if isinstance(it, SearchIntent):
            return ValidatedIntent(it, {"search_filter": self.search_filter(it.filter)})
        if isinstance(it, StatsIntent):
            return self.stats(it)
        if isinstance(it, CreateCategoryIntent):
            return self.create_category(it)
        if isinstance(it, AnswerIntent):
            return self.answer(it)
        if isinstance(it, ClarifyIntent):
            return ValidatedIntent(it, {})
        if isinstance(it, (DebtIntent, DebtsQueryIntent)):
            from app.services.ai import debt_validate  # debts (docs/DEBTS.md §5.4)

            if isinstance(it, DebtIntent):
                return await debt_validate.validate_debt(self, it)
            return await debt_validate.validate_query(self, it)
        raise self.fail("Неизвестное действие")  # pragma: no cover

    # ------------------------------------------------------------------ create
    def create(self, it: CreateTransactionIntent) -> ValidatedIntent:
        handle = _handle(it.counterparty)
        if handle and handle in self.my_ids:
            # Payment to an own @handle is an internal transfer, not an expense.
            if it.kind != "expense":
                raise self.fail("Доход от своего аккаунта — это перевод. Напишите «перевёл …»")
            tr = TransferIntent(
                amount=it.amount, amount_raw=it.amount_raw, currency=it.currency,
                from_account_ref=it.account_ref, to_handle=handle,
                description=it.description, occurred_on=it.occurred_on,
            )
            return self.transfer(tr)
        acc = None if it.account_none else self.account(it.account_ref)
        currency = self.currency(it.currency, acc)
        amount = self.amount(it.amount, it.amount_raw, currency)
        if acc is not None and not acc.is_multi_asset and acc.currency and acc.currency != currency:
            if not is_fiat(currency):
                raise self.fail(f"На счёте {acc.name} нет {currency} — выберите другой счёт")
        merchant = rs.resolve_merchant(self.ctx, name=it.merchant_ref, handle=handle)
        res: dict[str, Any] = {
            "kind": it.kind,
            "amount": amount,
            "currency": currency,
            "account_id": acc.id if acc else None,
            "account_none": bool(it.account_none),
            "merchant_id": merchant.id if merchant else None,
            "counterparty": (f"@{handle}" if handle else (it.counterparty or None)),
            "description": (it.description or None),
            "occurred_on": self.day(it.occurred_on),
            "occurred_time": it.occurred_time,
        }
        res.update(
            self.category(
                it.kind,
                ref=it.category_ref,
                new=it.new_category,
                merchant_category_id=merchant.category_id if merchant else None,
                for_resale=it.for_resale,
                to_person=bool(handle),
            )
        )
        if merchant is None and (it.merchant_ref or handle):
            name = (it.merchant_ref or "").strip() or f"@{handle}"
            if 1 <= len(name) <= 64 and not guards.bad_name(name):
                res["merchant_new"] = {
                    "name": name,
                    "handle": handle,
                    "alias": tn.norm(it.merchant_ref) if it.merchant_ref else None,
                }
        if it.occurred_time and res["occurred_on"] is None:
            res["occurred_on"] = self.ctx.today
        if res["occurred_on"] is not None and it.occurred_time is not None:
            zone = tz(self.ctx.timezone)
            at = datetime.combine(res["occurred_on"], it.occurred_time, tzinfo=zone)
            if at > self.ctx.now + timedelta(days=1):
                raise self.fail("Время операции в будущем — проверьте")
            res["occurred_at"] = at
        fixed = it.model_copy(update={"amount": amount, "currency": currency})
        return ValidatedIntent(fixed, res)

    # ------------------------------------------------------------------ transfer / exchange
    def transfer(self, it: TransferIntent) -> ValidatedIntent:
        handle = _handle(f"@{it.to_handle.lstrip('@')}") if it.to_handle else None
        if handle and handle not in self.my_ids:
            # Transfer to someone else's @handle = expense with a recipient.
            exp = CreateTransactionIntent(
                amount=it.amount, amount_raw=it.amount_raw, currency=it.currency, kind="expense",
                account_ref=it.from_account_ref, counterparty=f"@{handle}",
                description=it.description, occurred_on=it.occurred_on,
            )
            return self.create(exp)
        src = self.account(it.from_account_ref)
        dst: CtxAccount | None
        if handle:
            dst_id = self.my_ids.get(handle) or 0
            dst = rs.account_by_id(self.ctx, dst_id)
            if dst is None:
                raise self.fail(f"У аккаунта @{handle} нет кошелька — проверьте «Мои аккаунты»")
        else:
            dst = self.account(it.to_account_ref)
        currency = self.currency(it.currency, src)
        if src is None:
            src = rs.default_account(self.ctx, currency)
        counter_raw_cur = it.counter_currency.upper() if it.counter_currency else None
        if (dst is None and src is not None and src.is_multi_asset and counter_raw_cur
                and counter_raw_cur != currency):
            # «купил 0.1 eth за 300 usdt на бирже»: both legs in one multi-asset account
            dst = src
        if src is None:
            raise self.fail(
                "С какого счёта перевод?",
                "ambiguous_target",
                [f"{self.text} с {a.name}" for a in self._accounts_for(currency)][:4]
                if self.text else [],
            )
        if dst is None:
            raise self.fail(
                "На какой счёт перевод?",
                "ambiguous_target",
                [f"{self.text} на {a.name}" for a in self.ctx.accounts if a.id != src.id][:4]
                if self.text else [],
            )
        amount = self.amount(it.amount, it.amount_raw, currency)
        counter_cur = it.counter_currency.upper() if it.counter_currency else None
        dst_single = not dst.is_multi_asset and dst.currency
        if counter_cur is None and dst_single and dst.currency != currency:
            counter_cur = dst.currency
        res: dict[str, Any] = {
            "from_account_id": src.id,
            "to_account_id": dst.id,
            "amount": amount,
            "currency": currency,
            "description": it.description,
            "occurred_on": self.day(it.occurred_on),
            "to_handle": handle,
        }
        if not src.is_multi_asset and src.currency and src.currency != currency:
            raise self.fail(f"Счёт {src.name} в {src.currency}, а сумма в {currency}")
        if src.id == dst.id and not (counter_cur and counter_cur != currency
                                     and src.is_multi_asset):
            raise self.fail("Счета перевода должны различаться")
        if counter_cur and counter_cur != currency:
            counter_cur = self.currency(counter_cur)
            if it.counter_amount is None and not it.counter_amount_raw:
                raise self.fail(f"Сколько {counter_cur} получено при обмене?", "missing_amount")
            counter = self.amount(it.counter_amount, it.counter_amount_raw, counter_cur)
            if not dst.is_multi_asset and dst.currency and dst.currency != counter_cur:
                raise self.fail(f"Счёт {dst.name} в {dst.currency}, а получено {counter_cur}")
            res.update(is_exchange=True, counter_amount=counter, counter_currency=counter_cur)
        else:
            if not dst.is_multi_asset and dst.currency and dst.currency != currency:
                raise self.fail(f"Счёт {dst.name} в {dst.currency}, а перевод в {currency}")
            res["is_exchange"] = False
        fixed = it.model_copy(update={"amount": amount, "currency": currency})
        return ValidatedIntent(fixed, res)

    def _accounts_for(self, currency: str) -> list[CtxAccount]:
        c = currency.upper()
        first = [a for a in self.ctx.accounts if c in a.assets]
        multi = [a for a in self.ctx.accounts if a.is_multi_asset and a not in first]
        fiat = [a for a in self.ctx.accounts
                if c in FIAT and not a.is_multi_asset and a not in first and (a.currency in FIAT)]
        return first + multi + fiat

    # ------------------------------------------------------------------ reconciliation
    def adjust(self, it: AdjustBalanceIntent) -> ValidatedIntent:
        acc = self.account(it.account_ref)
        assert acc is not None
        if it.currency:
            currency = self.currency(it.currency, acc)
        elif acc.is_multi_asset:
            raise self.fail(
                f"Какая монета на счёте {acc.name}? Например: «в {acc.name} 120 usdt»",
                "ambiguous_currency",
            )
        else:
            currency = self.currency(None, acc)
        if not acc.is_multi_asset and acc.currency and acc.currency != currency:
            raise self.fail(f"Счёт {acc.name} ведётся в {acc.currency}, а не в {currency}")
        amount = self.amount(it.amount, it.amount_raw, currency, allow_zero=True)
        fixed = it.model_copy(update={"amount": amount, "currency": currency})
        return ValidatedIntent(fixed, {"account_id": acc.id, "asset": currency, "amount": amount})

    # ------------------------------------------------------------------ edit / delete
    async def targets(self, target: TargetSpec) -> tuple[list[Transaction], bool]:
        """→ (transactions, needs_preview). Immediate only for one record that is the last one
        (``last`` / ``last_expense`` / ``last_income`` with position ≤ 2, the «Изменить» button
        context, or an id that IS the last operation). Search → preview, limited to the asked
        number («удали кофе» → the newest one, «последние 5» → five, «все …» → up to 100).
        Ids must come from the dialogue context (never invented by the model)."""
        ctx = self.ctx
        kind = target.kind
        if kind == "last" and target.position == 1 and ctx.edit_transaction_id:
            kind = "context"
        if kind == "context" and not ctx.edit_transaction_id:
            kind = "last"
        if kind == "search":
            assert target.search is not None
            limit = self.search_limit(target.search)
            sf = self.search_filter(target.search, strict=True).model_copy(
                update={"limit": limit})
            from app.services.ledger import query as ledger_query

            txs = await ledger_query.resolve_target(
                self.s, self.uid, TargetSpec(kind="search", search=sf), ctx
            )
            txs = [t for t in txs if t.deleted_at is None][:limit]
            if limit >= BULK_LIMIT and len(txs) >= BULK_LIMIT:
                raise self.fail(f"Нашлось {BULK_LIMIT}+ операций — уточните период или условие")
            if not txs:
                raise self.fail("Не нашёл подходящих операций", "not_found")
            return txs, True
        immediate = True
        if kind == "context":
            ids = [ctx.edit_transaction_id]
        elif kind == "id":
            if target.transaction_id not in rs.allowed_transaction_ids(ctx):
                raise self.unsafe(
                    "Не нашёл эту операцию среди последних действий — уточните, какую изменить")
            ids = [target.transaction_id]
            if target.transaction_id != ctx.edit_transaction_id:
                last = await self._last_ids("last", 1)
                immediate = last == ids  # the model picked an older record → confirm first
        else:
            ids = await self._last_ids(kind, target.position)
            immediate = target.position <= 2
        rows = [await self.s.get(Transaction, i) for i in ids if i is not None]
        rows = [r for r in rows if r is not None and r.user_id == self.uid and r.deleted_at is None]
        if not rows:
            raise self.fail("Не нашёл операцию, которую нужно изменить", "not_found")
        return rows, not immediate

    def search_limit(self, sf: SearchFilter) -> int:
        """How many records a search target may touch: the explicit limit; «все …» or a period
        → up to BULK_LIMIT; otherwise the newest one («удали кофе»)."""
        if "limit" in sf.model_fields_set:
            return max(1, min(sf.limit, BULK_LIMIT))
        if guards.has_bulk_word(self.text) or sf.period is not None:
            return BULK_LIMIT
        return 1

    async def _actual_charge_target(self, it: UpdateTransactionIntent) -> _ChargeTarget | None:
        """«васт списали 21.3м» (fixes the charge at once). Target = the NEWEST live
        operation of that merchant / word (by merchant rule or by the word in the text, even
        when «васт» is only a lexicon word without a merchant row):
        * newest is is_estimated → fixed at once;
        * newest still waits for an account (needs_account) → gets the default account of the
          charge currency (₫ → Карта) with this charge, at once;
        * otherwise (already fixed / no account) → preview of the newest estimated or newest."""
        t, ch = it.target, it.changes
        if t.kind != "search" or t.search is None:
            return None
        sf = t.search
        if "limit" in sf.model_fields_set and sf.limit != 1:
            return None
        if ch.account_amount is None and not ch.account_amount_raw:
            return None
        other = ch.model_dump(exclude_none=True,
                              exclude={"account_amount", "account_amount_raw", "account_ref"})
        if any(v not in (False, [], "") for v in other.values()):
            return None
        terms: set[str] = set()
        merchant = None
        if sf.merchant_ref:
            merchant = rs.resolve_merchant(self.ctx, name=sf.merchant_ref,
                                           handle=_handle(sf.merchant_ref))
            terms.add(sf.merchant_ref)
        if merchant is not None:
            terms |= {merchant.name, *merchant.aliases}
        if sf.text:
            terms.add(sf.text)
        if sf.counterparty:
            terms.add(sf.counterparty)
        m = _CHARGE_WHO_RE.match(self.text)
        if m and m.group("who").strip().lower() not in _CHARGE_STOP:
            terms.add(m.group("who"))
        terms = {x.strip().lower() for x in terms if x and len(x.strip()) >= 3}
        cat_ids = self._category_ids(sf.category_refs) + [
            i for i in sf.category_ids if rs.category_by_id(self.ctx, i)]
        T = Transaction
        conds: list[Any] = []
        if merchant is not None:
            conds.append(T.merchant_id == merchant.id)
        for term in sorted(terms):
            like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conds += [T.raw_text.ilike(like, escape="\\"), T.description.ilike(like, escape="\\"),
                      T.counterparty.ilike(like, escape="\\")]
        if not conds and cat_ids:
            conds.append(T.category_id.in_(cat_ids))
        if not conds:
            return None
        since = utcnow() - timedelta(days=CHARGE_LOOKBACK_DAYS)
        rows = list((await self.s.scalars(
            select(T).where(T.user_id == self.uid, T.deleted_at.is_(None), T.kind.in_(_PNL),
                            T.created_at >= since, or_(*conds))
            .order_by(T.created_at.desc(), T.id.desc()).limit(20)
        )).all())
        if not rows:
            return None
        newest = rows[0]
        if newest.status == "needs_account":
            return _ChargeTarget([newest], False, self._charge_account(ch))
        if newest.is_estimated:
            return _ChargeTarget([newest], False, {})
        older = next((r for r in rows[1:] if r.is_estimated and r.account_id is not None), None)
        if older is not None:
            return _ChargeTarget([older], True, {})
        extra = {} if newest.account_id is not None else self._charge_account(ch)
        return _ChargeTarget([newest], True, extra)

    def _charge_account(self, ch: TransactionChanges) -> dict[str, Any]:
        """Account of an actual charge for an operation without one: written («с карты») or the
        default account of the charge currency (₫ → Карта)."""
        if ch.account_ref:
            acc = self.account(ch.account_ref)
        else:
            curs = compat.currencies_in_text(ch.account_amount_raw or "")
            cur = next(iter(curs)) if len(curs) == 1 else (self.ctx.default_currency or "VND")
            acc = rs.default_account(self.ctx, cur.upper())
        if acc is None:
            raise self.fail(
                "С какого счёта списали?", "ambiguous_target",
                [f"{self.text} с {a.name}" for a in self.ctx.accounts
                 if not a.is_multi_asset][:4] if self.text else [],
            )
        return {"account_id": acc.id}

    def _category_ids(self, refs: list[str]) -> list[int]:
        out: list[int] = []
        for ref in refs:
            for k in ("expense", "income"):
                r = rs.find_category(self.ctx, ref, k)  # type: ignore[arg-type]
                if r.category and r.score >= rs.DEDUP_THRESHOLD:
                    out.append(r.category.id)
                    break
        return out

    async def _check_charge_rate(self, tx: Transaction, account_amount: Decimal,
                                 asset: str) -> None:
        """Sanity of an actual charge: the implied rate must be within ±15 % of the reference
        (VCB / the rate stored in the operation). «$100 списали 1.3м» → clarify, not write."""
        if tx.amount is None or tx.amount <= 0:
            return
        cur = (tx.currency or "").upper()
        asset = asset.upper()
        ref: Decimal | None = None
        if asset == cur:
            ref = Decimal(1)
        elif tx.is_estimated and tx.account_rate and (tx.account_asset or "").upper() == asset:
            ref = Decimal(tx.account_rate)
        elif asset == "VND" and tx.amount_vnd:
            ref = Decimal(tx.amount_vnd) / Decimal(tx.amount)
        elif asset == "USD" and tx.amount_usd:
            ref = Decimal(tx.amount_usd) / Decimal(tx.amount)
        else:
            try:
                from app.services.rates import service as rates

                kind = "sell" if "VND" in (cur, asset) else "mid"
                q = await rates.get_rate(self.s, cur, asset, tx.occurred_on, kind=kind,
                                         fetch_missing=False)
                ref = Decimal(q.rate) if q is not None else None
            except Exception:  # never block a correction because rates are unavailable
                ref = None
        if ref is None or ref <= 0:
            return
        implied = account_amount / Decimal(tx.amount)
        if abs(implied / ref - 1) <= CHARGE_RATE_TOLERANCE:
            return
        raise self.fail(
            f"Списание {format_money(account_amount, asset)} за "
            f"{format_money(tx.amount, cur)} — это курс {format_money(implied, asset)} "
            f"за 1 {cur} вместо ≈ {format_money(ref, asset)}. Проверьте сумму списания",
            "ambiguous_target",
        )

    async def _last_ids(self, kind: str, position: int) -> list[int]:
        want = {"last_expense": "expense", "last_income": "income"}.get(kind)
        picked: list[int] = []
        ctx_ids = rs.recent_transaction_ids(self.ctx)
        if ctx_ids:
            rows = (
                await self.s.scalars(
                    select(Transaction).where(
                        Transaction.id.in_(ctx_ids),
                        Transaction.user_id == self.uid,
                        Transaction.deleted_at.is_(None),
                    )
                )
            ).all()
            by_id = {r.id: r for r in rows}
            picked = [i for i in ctx_ids if i in by_id and (want is None or by_id[i].kind == want)]
        if len(picked) < position:
            q = select(Transaction.id).where(
                Transaction.user_id == self.uid, Transaction.deleted_at.is_(None)
            )
            if want:
                q = q.where(Transaction.kind == want)
            more = (
                await self.s.scalars(
                    q.order_by(Transaction.created_at.desc(), Transaction.id.desc())
                    .limit(position + 10)
                )
            ).all()
            picked += [i for i in more if i not in picked]
        if len(picked) < position:
            return []
        return [picked[position - 1]]


    async def update(self, it: UpdateTransactionIntent) -> ValidatedIntent:
        charge = await self._actual_charge_target(it)
        if charge is not None:
            txs, preview, extra = charge.txs, charge.preview, dict(charge.patch)
        else:
            (txs, preview), extra = await self.targets(it.target), {}
        ch = it.changes
        base = txs[0]
        patch: dict[str, Any] = {}
        clean: dict[str, Any] = {}
        if ch.kind is not None:
            patch["kind"] = clean["kind"] = ch.kind
        if ch.currency is not None:
            patch["currency"] = clean["currency"] = self.currency(
                ch.currency, extra=tuple(t.currency for t in txs))
        if ch.amount is not None or ch.amount_raw:
            cur = patch.get("currency") or base.currency
            patch["amount"] = clean["amount"] = self.amount(ch.amount, ch.amount_raw, cur)
        if ch.account_none:
            patch["account_none"] = clean["account_none"] = True
        elif ch.account_ref or extra.get("account_id"):
            acc = self.account(ch.account_ref) if ch.account_ref else rs.account_by_id(
                self.ctx, extra["account_id"])
            assert acc is not None
            patch["account_id"] = acc.id
            clean["account_ref"] = acc.name
        if ch.account_amount is not None or ch.account_amount_raw:
            if any(t.account_id is None for t in txs) and "account_id" not in patch:
                raise self.fail("У операции нет счёта — сначала выберите счёт")
            new_acc = rs.account_by_id(self.ctx, patch.get("account_id"))
            if new_acc is not None:
                asset = (base.currency if new_acc.is_multi_asset else new_acc.currency) or (
                    base.currency)
            else:
                asset = base.account_asset or base.currency
            patch["account_amount"] = clean["account_amount"] = self.amount(
                ch.account_amount, ch.account_amount_raw, asset
            )
            if len(txs) == 1 and "amount" not in patch and "currency" not in patch:
                await self._check_charge_rate(base, patch["account_amount"], asset)
        if ch.category_ref or ch.new_category:
            kinds = {(patch.get("kind") or t.kind) for t in txs}
            if not kinds <= {"expense", "income", "refund"}:
                raise self.fail("У перевода/обмена нет категории")
            if len({"income" if k == "income" else "expense" for k in kinds}) > 1:
                raise self.fail("Среди операций есть и доходы, и расходы — уточните")
            cres = self.category(next(iter(kinds)), ref=ch.category_ref, new=ch.new_category)
            if "category_id" in cres:
                patch["category_id"] = cres["category_id"]
                cat = rs.category_by_id(self.ctx, cres["category_id"])
                clean["category_ref"] = cat.name if cat else ch.category_ref
            else:
                patch["new_category"] = clean["new_category"] = cres["new_category"]
        if ch.occurred_on is not None:
            patch["occurred_on"] = clean["occurred_on"] = self.day(ch.occurred_on)
        if ch.description is not None:
            patch["description"] = clean["description"] = ch.description
        if ch.counterparty is not None:
            patch["counterparty"] = clean["counterparty"] = ch.counterparty
        if not patch:
            raise self.fail("Не понял, что изменить", "unknown")
        changes = TransactionChanges.model_validate(clean)
        target = it.target
        if target.kind == "search" and target.search is not None:
            target = target.model_copy(update={"search": self.search_filter(target.search)})
        fixed = UpdateTransactionIntent(target=target, changes=changes)
        return ValidatedIntent(
            fixed,
            {"transaction_ids": [t.id for t in txs], "transactions": txs, "preview": preview,
             "patch": patch},
        )

    async def delete(self, it: DeleteTransactionIntent) -> ValidatedIntent:
        txs, preview = await self.targets(it.target)
        target = it.target
        if target.kind == "search" and target.search is not None:
            target = target.model_copy(update={"search": self.search_filter(target.search)})
        return ValidatedIntent(
            DeleteTransactionIntent(target=target),
            {"transaction_ids": [t.id for t in txs], "transactions": txs, "preview": preview},
        )

    def undo(self, it: UndoIntent) -> ValidatedIntent:
        if it.action_id is not None:
            if it.action_id not in {a.action_id for a in self.ctx.recent_actions}:
                raise self.unsafe("Такого действия нет среди последних — нажмите «Отменить» "
                                  "под нужной карточкой")
        return ValidatedIntent(it, {"action_id": it.action_id})

    # ------------------------------------------------------------------ read side
    def search_filter(self, sf: SearchFilter, *, strict: bool = False) -> SearchFilter:
        """Refs → ids (category/account), merchant → canonical name, sanity of amounts.
        ``strict`` (edit/delete targets): unknown category/account ids are an error, never a
        silently wider filter («удали в категории #9999» must not hit every expense)."""
        upd: dict[str, Any] = {}
        cat_ids = list(sf.category_ids or [])
        extra_text: list[str] = []
        kinds = set(sf.kinds or [])
        cat_kinds: list[rs.CatKind] = (
            ["income"] if kinds == {"income"} else ["expense"] if kinds and "income" not in kinds
            else ["expense", "income"]
        )
        known_cat_ids = {c.id for c in self.ctx.categories}
        if strict and any(i not in known_cat_ids for i in cat_ids):
            raise self.fail("Не нашёл такую категорию — уточните", "not_found")
        cat_ids = [i for i in cat_ids if i in known_cat_ids]
        for ref in sf.category_refs:
            hit = None
            for k in cat_kinds:
                r = rs.find_category(self.ctx, ref, k)
                if r.category and r.score >= rs.DEDUP_THRESHOLD:
                    hit = r.category
                    break
            if hit:
                cat_ids.append(hit.id)
            else:
                extra_text.append(ref)
        upd["category_refs"] = []
        upd["category_ids"] = sorted(set(cat_ids))
        if strict and any(not rs.account_by_id(self.ctx, i) for i in (sf.account_ids or [])):
            raise self.fail("Не нашёл такой счёт — уточните", "not_found")
        acc_ids = [i for i in (sf.account_ids or []) if rs.account_by_id(self.ctx, i)]
        for ref in sf.account_refs:
            acc = self.account(ref)
            assert acc is not None
            acc_ids.append(acc.id)
        upd["account_refs"] = []
        upd["account_ids"] = sorted(set(acc_ids))
        if sf.merchant_ref:
            m = rs.resolve_merchant(self.ctx, name=sf.merchant_ref, handle=_handle(sf.merchant_ref))
            upd["merchant_ref"] = m.name if m else sf.merchant_ref
        if extra_text and not sf.text:
            upd["text"] = " ".join(extra_text)[:200]
        for f in ("min_amount", "max_amount"):
            v = getattr(sf, f)
            if v is not None and v < 0:
                raise self.fail("Сумма в фильтре должна быть положительной")
        lo, hi = sf.min_amount, sf.max_amount
        if lo is not None and hi is not None and lo > hi:
            raise self.fail("Минимальная сумма больше максимальной")
        if sf.currency:
            upd["currency"] = self.soft_currency(sf.currency)
        if sf.amount_currency:
            upd["amount_currency"] = self.soft_currency(sf.amount_currency)
        if sf.period and sf.period.preset == "custom":
            if not (sf.period.date_from and sf.period.date_to):
                raise self.fail("Для своего периода нужны обе даты")
        return sf.model_copy(update=upd)

    def stats(self, it: StatsIntent) -> ValidatedIntent:
        cat_ids: list[int] = []
        for ref in it.category_refs:
            r = rs.find_category(self.ctx, ref, "income" if it.metric == "earned" else "expense")
            if not (r.category and r.score >= rs.DEDUP_THRESHOLD):
                other = rs.find_category(
                    self.ctx, ref, "expense" if it.metric == "earned" else "income"
                )
                r = other if other.category and other.score >= rs.DEDUP_THRESHOLD else r
            if not (r.category and r.score >= rs.DEDUP_THRESHOLD):
                raise self.fail(f"Не знаю категорию «{ref}»", "not_found")
            cat_ids.append(r.category.id)
        acc_ids: list[int] = []
        for ref in it.account_refs:
            acc = self.account(ref)
            assert acc is not None
            acc_ids.append(acc.id)
        for p in (it.period, it.compare_period):
            if p is not None and p.preset == "custom" and not (p.date_from and p.date_to):
                raise self.fail("Для своего периода нужны обе даты")
        if it.display_currency:
            dc = self.soft_currency(it.display_currency)
            if dc != it.display_currency:
                it = it.model_copy(update={"display_currency": dc})
        return ValidatedIntent(it, {"category_ids": cat_ids, "account_ids": acc_ids})

    def create_category(self, it: CreateCategoryIntent) -> ValidatedIntent:
        name = tn.clean_category_name(it.name)
        if not name or guards.bad_name(name):
            raise self.fail(f"Странное название категории «{it.name[:40]}»")
        icon = _icon(it.icon)
        r = rs.find_category(self.ctx, name, it.kind)
        if r.category and r.score >= rs.DEDUP_THRESHOLD:
            return ValidatedIntent(it.model_copy(update={"icon": icon}),
                                   {"existing_category_id": r.category.id})
        return ValidatedIntent(it.model_copy(update={"name": name, "icon": icon}), {"name": name})

    def answer(self, it: AnswerIntent) -> ValidatedIntent:
        t = (it.text or "").strip()
        if not t:
            raise self.fail("Пустой ответ", "unknown")
        low = t.lower()
        if "<<<user>>>" in low or "ты — модуль разбора" in low or guards.looks_like_injection(t):
            raise self.unsafe("Не могу так ответить. Спросите про свои траты, например "
                              "«сколько я потратил за месяц?»")
        names = [n for a in self.ctx.accounts for n in (a.name, *a.aliases)]
        if guards.answer_leaks(t, names):
            # the model does not know balances/amounts: any such «answer» is invented
            raise self.fail("Про ваши счета и суммы отвечаю только по данным. Спросите, например, "
                            "«сколько на счетах?» или «сколько я потратил за месяц?»", "other",
                            ["сколько на счетах?", "сколько я потратил за месяц?"])
        return ValidatedIntent(it.model_copy(update={"text": t[:1500]}), {})

    # ------------------------------------------------------------------ whole-message checks
    def check_per_unit(self, out: list[ValidatedIntent]) -> None:
        """«2 кофе по 40к», «кофе 50к x2»: the model must not record the unit price as the total
        (it never multiplies) → ask «Итого 80 000 ₫?» unless the operations already add up."""
        pu = guards.per_unit(self.text)
        if pu is None:
            return
        creates = [v for v in out if isinstance(v.intent, CreateTransactionIntent)]
        if not creates:
            return
        qty, price_raw, what, fragment = pu
        cur = creates[0].resolved.get("currency") or (self.ctx.default_currency or "VND")
        try:
            price = self.parse(price_raw, cur)
        except (ValueError, ArithmeticError):
            return
        total = price * qty
        same = [v for v in creates if v.resolved.get("currency") == cur]
        if qty <= 1 or sum((v.resolved["amount"] for v in same), Decimal(0)) == total:
            return
        t_short, p_short = guards.short_amount(total, cur), guards.short_amount(price, cur)
        options = [self.text.replace(fragment, f"{what} {t_short}", 1),
                   self.text.replace(fragment, f"{what} {p_short}", 1)]
        raise self.fail(
            f"«{fragment.strip()}» — записать итог {format_money(total, cur)}? Сам не умножаю",
            "other", options,
        )


@dataclass
class _ChargeTarget:
    txs: list[Transaction]
    preview: bool
    patch: dict[str, Any]


def _icon(icon: str | None) -> str | None:
    """Model-provided lucide icon name: short identifier only (DB column is VARCHAR(32))."""
    if not icon:
        return None
    i = icon.strip()
    return i if _ICON_RE.fullmatch(i) else None


class _NoAccType:
    currency = None


_NoAcc = _NoAccType()


def _handle(value: str | None) -> str | None:
    """«@Alice» → «alice»; plain names are not handles."""
    if not value:
        return None
    v = value.strip()
    if v.startswith("@") and len(v) > 1:
        h = v[1:].lower()
        return h if all(ch.isalnum() or ch == "_" for ch in h) else None
    return None
