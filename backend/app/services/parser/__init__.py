"""Rule-based input parser. Pure functions: no DB, no network.

Public API (docs/ARCH.md §7):
* ``parse(text, ctx) -> ParseResult``
* ``amounts.parse_amount(raw, currency, *, small_thousands=True, allow_zero=False) -> Decimal``
* ``dates.resolve_date(token, today) -> date | None``
* ``currencies.detect_currency(token) -> str | None`` (+ ``coin_candidate``)
* ``periods.find_periods(text, today)`` — stats periods
"""

from app.services.parser.amounts import parse_amount
from app.services.parser.currencies import coin_candidate, detect_currency
from app.services.parser.dates import resolve_date
from app.services.parser.parser import parse

__all__ = ["coin_candidate", "detect_currency", "parse", "parse_amount", "resolve_date"]
