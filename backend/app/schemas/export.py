"""Export schemas. CSV/XLSX are file responses; full export is JSON."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.schemas.common import Schema


class FullExport(Schema):
    exported_at: datetime
    version: int = 1
    settings: dict[str, Any]
    accounts: list[dict[str, Any]]
    categories: list[dict[str, Any]]
    merchants: list[dict[str, Any]]
    my_accounts: list[dict[str, Any]]
    transactions: list[dict[str, Any]]
    recurring: list[dict[str, Any]]
    #: docs/DEBTS.md §12 — people with debts (additive; also embedded as ``accounts[].debt``)
    debts: list[dict[str, Any]] = []


# CSV/XLSX columns (fixed order, RU headers):
EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("id", "ID"),
    ("occurred_on", "Дата"),
    ("occurred_time", "Время"),
    ("kind", "Тип"),
    ("amount", "Сумма"),
    ("currency", "Валюта"),
    ("account", "Счёт"),
    ("account_amount", "Списано со счёта"),
    ("account_asset", "Валюта счёта"),
    ("is_estimated", "Оценка"),
    ("counter_account", "Счёт-получатель"),
    ("counter_amount", "Зачислено"),
    ("counter_asset", "Валюта зачисления"),
    ("category", "Категория"),
    ("is_business", "Бизнес"),
    ("merchant", "Продавец"),
    ("counterparty", "Получатель"),
    ("description", "Назначение"),
    ("amount_usd", "≈ USD"),
    ("amount_vnd", "≈ VND"),
    ("usd_rate", "Курс к USD"),
    ("source", "Источник"),
    ("raw_text", "Исходный текст"),
    ("status", "Статус"),
]
