"""String enums stored as VARCHAR + CHECK constraints (docs/ARCH.md §3)."""

from __future__ import annotations

from enum import StrEnum


class Source(StrEnum):
    web = "web"
    desktop = "desktop"
    telegram = "telegram"
    system = "system"


class AccountKind(StrEnum):
    bank = "bank"
    cash = "cash"
    card = "card"
    crypto_wallet = "crypto_wallet"
    exchange = "exchange"
    other = "other"
    #: service account of a debt counterparty (docs/DEBTS.md): balance > 0 = owes me,
    #: < 0 = I owe. Hidden from /accounts, balances, account resolution and pickers.
    debt = "debt"


class CategoryKind(StrEnum):
    expense = "expense"
    income = "income"


class TxKind(StrEnum):
    expense = "expense"
    income = "income"
    refund = "refund"
    transfer = "transfer"
    exchange = "exchange"
    adjustment = "adjustment"


# Kinds that count in income/expense statistics.
PNL_KINDS = (TxKind.expense, TxKind.income, TxKind.refund)


class DebtOp(StrEnum):
    """transactions.debt_op (docs/DEBTS.md §3). NULL for every non-debt operation."""

    lend = "lend"  # дал в долг: real → debt account (transfer)
    repay_in = "repay_in"  # мне вернули: debt account → real (transfer)
    borrow = "borrow"  # занял: debt account → real (transfer)
    repay_out = "repay_out"  # я вернул: real → debt account (transfer)
    forgive = "forgive"  # списал остаток: expense «Переводы людям» / income «Прочее» on debt acc


#: debt ops where money comes INTO a real account (debt account is the transfer source)
DEBT_INFLOW = (DebtOp.repay_in, DebtOp.borrow)
#: debt ops where money goes OUT of a real account (debt account is the transfer target)
DEBT_OUTFLOW = (DebtOp.lend, DebtOp.repay_out)


class TxStatus(StrEnum):
    posted = "posted"
    needs_account = "needs_account"


class RateKind(StrEnum):
    mid = "mid"
    sell = "sell"
    buy = "buy"


class RateSource(StrEnum):
    vietcombank = "vietcombank"
    cbr = "cbr"
    coingecko = "coingecko"
    open_er_api = "open_er_api"
    manual = "manual"
    identity = "identity"


class AIRoute(StrEnum):
    parser = "parser"
    llm = "llm"
    parser_llm = "parser_llm"
    none = "none"


class AIStatus(StrEnum):
    ok = "ok"
    invalid = "invalid"
    error = "error"
    timeout = "timeout"
    unavailable = "unavailable"
    clarify = "clarify"


class AuditOp(StrEnum):
    create = "create"
    update = "update"
    delete = "delete"
    restore = "restore"
    merge = "merge"


class ActionKind(StrEnum):
    create_transaction = "create_transaction"
    update_transaction = "update_transaction"
    delete_transaction = "delete_transaction"
    restore_transaction = "restore_transaction"
    bulk_update = "bulk_update"
    bulk_delete = "bulk_delete"
    transfer = "transfer"
    exchange = "exchange"
    adjust_balance = "adjust_balance"
    assign_account = "assign_account"
    set_opening = "set_opening"
    create_category = "create_category"
    merge_categories = "merge_categories"
    debt = "debt"  # any debt operation (lend/repay/borrow/forgive), docs/DEBTS.md
    undo = "undo"


class PreviewKind(StrEnum):
    bulk_update = "bulk_update"
    bulk_delete = "bulk_delete"


class Frequency(StrEnum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    yearly = "yearly"


class OutboxKind(StrEnum):
    report_monthly = "report_monthly"
    report_daily = "report_daily"
    report_weekly = "report_weekly"
    recurring_reminder = "recurring_reminder"
    debt_reminder = "debt_reminder"
    info = "info"


class OutboxStatus(StrEnum):
    pending = "pending"
    sending = "sending"
    sent = "sent"
    failed = "failed"


class IdemState(StrEnum):
    in_progress = "in_progress"
    done = "done"


def check_in(column: str, enum: type[StrEnum]) -> str:
    """SQL CHECK expression: column IN (...)."""
    vals = ", ".join(f"'{m.value}'" for m in enum)
    return f"{column} IN ({vals})"
