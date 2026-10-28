"""CSV/XLSX/JSON export. Columns: app.schemas.export.EXPORT_COLUMNS."""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator
from datetime import date, time
from decimal import Decimal
from typing import Any, Literal

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import normalize
from app.core.timeutil import local_today, tz
from app.models import (
    Account,
    AccountAsset,
    Category,
    Debt,
    Merchant,
    MyAccount,
    RecurringTransaction,
    Transaction,
    UserSettings,
)
from app.schemas.export import EXPORT_COLUMNS, FullExport
from app.schemas.transactions import TransactionFilter
from app.services.ledger.audit import snapshot
from app.services.ledger.common import now, user_tz
from app.services.ledger.query import iter_transactions
from app.services.ledger.summaries import Refs

KIND_RU = {
    "expense": "Расход", "income": "Доход", "refund": "Возврат", "transfer": "Перевод",
    "exchange": "Обмен", "adjustment": "Корректировка",
}
STATUS_RU = {"posted": "проведена", "needs_account": "разобрать"}
_MONEY_COLS = {"amount", "account_amount", "counter_amount", "amount_usd", "amount_vnd",
               "usd_rate"}


def _row(t: Transaction, r: Refs, tz_name: str) -> dict[str, Any]:
    m = r.merchants.get(t.merchant_id) if t.merchant_id else None
    local = t.occurred_at.astimezone(tz(tz_name))
    return {
        "id": t.id,
        "occurred_on": t.occurred_on,
        "occurred_time": time(local.hour, local.minute),
        "kind": KIND_RU.get(t.kind, t.kind),
        "amount": t.amount,
        "currency": t.currency,
        "account": r.acc_name(t.account_id) or "",
        "account_amount": t.account_amount,
        "account_asset": t.account_asset or "",
        "is_estimated": "да" if t.is_estimated else "нет",
        "counter_account": r.acc_name(t.counter_account_id) or "",
        "counter_amount": t.counter_amount,
        "counter_asset": t.counter_asset or "",
        "category": r.cat_name(t.category_id) or "",
        "is_business": "да" if t.is_business else "нет",
        "merchant": m.name if m else "",
        "counterparty": t.counterparty or "",
        "description": t.description or "",
        "amount_usd": t.amount_usd,
        "amount_vnd": t.amount_vnd,
        "usd_rate": t.usd_rate,
        "source": t.source,
        "raw_text": t.raw_text or "",
        "status": STATUS_RU.get(t.status, t.status),
    }


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return normalize(v)
    if isinstance(v, time):
        return v.strftime("%H:%M")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _safe_cell(s: str) -> str:
    """CSV formula injection guard for user-written text (OWASP: prefix with ')."""
    if s and s[0] in "=+-@\t\r" and not _is_number(s):
        return "'" + s
    return s


def _is_number(s: str) -> bool:
    try:
        Decimal(s)
        return True
    except Exception:
        return False


# Excel keeps 15 significant digits in a number cell: longer amounts go as exact text
_XLSX_DIGITS = 15


def _xlsx_exact(v: Decimal) -> bool:
    return len(v.normalize().as_tuple().digits) <= _XLSX_DIGITS


async def _row_batches(
    session: AsyncSession, user_id: int, f: TransactionFilter
) -> AsyncIterator[list[dict[str, Any]]]:
    tz_name = await user_tz(session, user_id)
    async for batch in iter_transactions(session, user_id, f):
        refs = await Refs.load(session, batch)
        yield [_row(t, refs, tz_name) for t in batch]


class _CsvSink:
    def __init__(self, keys: list[str], headers: list[str]) -> None:
        self.keys = keys
        self.buf = io.StringIO()
        self.w = csv.writer(self.buf, delimiter=";", lineterminator="\r\n",
                            quoting=csv.QUOTE_MINIMAL)
        self.w.writerow(headers)

    def write(self, rows: list[dict[str, Any]]) -> None:
        keys = self.keys
        for row in rows:
            self.w.writerow(
                [_text(row[k]) if k in _MONEY_COLS or k in _PLAIN_COLS
                 else _safe_cell(_text(row[k])) for k in keys]
            )

    def finish(self) -> bytes:
        return ("\ufeff" + self.buf.getvalue()).encode("utf-8")


class _XlsxSink:
    """openpyxl write-only workbook. Text cells are never formulas here (data_type 's'), so —
    unlike CSV — no apostrophe guard: «@bob» stays «@bob». Dates/times are real
    date cells; amounts are numbers unless they exceed Excel's 15 significant digits."""

    def __init__(self, keys: list[str], headers: list[str]) -> None:
        from openpyxl import Workbook

        self.keys = keys
        self.wb = Workbook(write_only=True)
        self.ws = self.wb.create_sheet("Операции")
        self.ws.append(headers)

    def _text_cell(self, s: str) -> Any:
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

        # control characters (\x07, \x1b…) are not allowed in XLSX XML: openpyxl raises
        # IllegalCharacterError (was a 500) — drop them, the text stays readable
        s = ILLEGAL_CHARACTERS_RE.sub("", s)
        if not s.startswith("="):
            return s
        c = WriteOnlyCell(self.ws, value=s)
        c.data_type = "s"  # literal text, never evaluated as a formula
        return c

    def _cell(self, k: str, v: Any) -> Any:
        from openpyxl.cell import WriteOnlyCell

        if k == "id":
            return v
        if k in _MONEY_COLS:
            if v is None:
                return None
            return v if _xlsx_exact(v) else normalize(v)
        if k == "occurred_on":
            c = WriteOnlyCell(self.ws, value=v)
            c.number_format = "yyyy-mm-dd"
            return c
        if k == "occurred_time":
            c = WriteOnlyCell(self.ws, value=v)
            c.number_format = "hh:mm"
            return c
        return self._text_cell(_text(v))

    def write(self, rows: list[dict[str, Any]]) -> None:
        keys = self.keys
        for row in rows:
            self.ws.append([self._cell(k, row[k]) for k in keys])

    def finish(self) -> bytes:
        out = io.BytesIO()
        self.wb.save(out)
        return out.getvalue()


_PLAIN_COLS = {"id", "occurred_on", "occurred_time"}


async def export_transactions(
    session: AsyncSession, user_id: int, f: TransactionFilter, fmt: Literal["csv", "xlsx"]
) -> tuple[bytes, str, str]:
    """Return (content, media_type, filename). CSV: UTF-8 with BOM, ';' separator (Excel RU),
    decimal point '.', dates ISO. XLSX via openpyxl with numeric and date cells.
    Rows are read from the DB in batches on the event loop; building the file (CPU) runs in a
    worker thread so other requests of this process are not blocked by a large export."""
    day = local_today(await user_tz(session, user_id)).isoformat()
    keys = [k for k, _ in EXPORT_COLUMNS]
    headers = [h for _, h in EXPORT_COLUMNS]
    sink: _CsvSink | _XlsxSink = (
        _CsvSink(keys, headers) if fmt == "csv"
        else await anyio.to_thread.run_sync(_XlsxSink, keys, headers)
    )
    async for rows in _row_batches(session, user_id, f):
        await anyio.to_thread.run_sync(sink.write, rows)
    data = await anyio.to_thread.run_sync(sink.finish)
    if fmt == "csv":
        return data, "text/csv; charset=utf-8", f"pocket-transactions-{day}.csv"
    return (
        data,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"pocket-transactions-{day}.xlsx",
    )


async def export_all_json(session: AsyncSession, user_id: int) -> FullExport:
    """Everything of the user (transactions include soft-deleted ones)."""
    st = await session.get(UserSettings, user_id)
    accounts = list(
        await session.scalars(select(Account).where(Account.user_id == user_id)
                              .order_by(Account.id))
    )
    assets = list(
        await session.scalars(
            select(AccountAsset).where(AccountAsset.user_id == user_id).order_by(AccountAsset.id)
        )
    )
    by_acc: dict[int, list[dict[str, Any]]] = {}
    for a in assets:
        by_acc.setdefault(a.account_id, []).append(snapshot(a))
    # debts (docs/DEBTS.md §12): each person rides on its service account (kind=debt) as
    # ``debt`` and as the top-level ``debts[]`` (both additive)
    debt_rows = [
        snapshot(d)
        for d in await session.scalars(
            select(Debt).where(Debt.user_id == user_id).order_by(Debt.id)
        )
    ]
    debt_by_acc = {d["account_id"]: d for d in debt_rows}

    async def all_of(model: Any) -> list[dict[str, Any]]:
        rows = await session.scalars(
            select(model).where(model.user_id == user_id).order_by(model.id)
        )
        return [snapshot(x) for x in rows]

    return FullExport(
        exported_at=now(),
        settings=snapshot(st) if st else {},
        accounts=[
            {**snapshot(a), "assets": by_acc.get(a.id, []),
             **({"debt": debt_by_acc[a.id]} if a.id in debt_by_acc else {})}
            for a in accounts
        ],
        categories=await all_of(Category),
        merchants=await all_of(Merchant),
        my_accounts=await all_of(MyAccount),
        transactions=await all_of(Transaction),
        recurring=await all_of(RecurringTransaction),
        debts=debt_rows,
    )
