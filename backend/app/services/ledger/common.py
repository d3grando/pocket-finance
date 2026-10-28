"""Shared ledger helpers: user settings, dates, rates/valuation, Decimal math,
text normalisation, change events. Money is Decimal only (docs/ARCH.md §4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import LimitReached, ServiceUnavailable, ValidationFailed
from app.core.money import AMOUNT_MAX, quantize_asset
from app.core.timeutil import DEFAULT_TZ, local_date_of, local_today, occurred_at_for, utcnow
from app.models import UserSettings
from app.services.events import bus as events_bus
from app.services.rates import service as rates_service

MONEY_Q = Decimal("1e-8")
RATE_Q = Decimal("1e-12")
ONE = Decimal(1)


# ------------------------------------------------------------------------------- decimal math


def mul(a: Decimal, b: Decimal) -> Decimal:
    with localcontext() as c:
        c.prec = 60
        return a * b


def div(a: Decimal, b: Decimal) -> Decimal:
    with localcontext() as c:
        c.prec = 60
        return a / b


def q_money(d: Decimal) -> Decimal:
    """Storage precision of NUMERIC(28,8), banker's rounding."""
    with localcontext() as c:
        c.prec = 60
        return d.quantize(MONEY_Q, rounding=ROUND_HALF_EVEN)


def q_rate(d: Decimal) -> Decimal:
    """Storage precision of NUMERIC(30,12); never rounds a positive rate down to zero."""
    with localcontext() as c:
        c.prec = 60
        r = d.quantize(RATE_Q, rounding=ROUND_HALF_EVEN)
    if r == 0 and d > 0:
        return RATE_Q
    return r


def q_asset(d: Decimal, asset: str) -> Decimal:
    with localcontext() as c:
        c.prec = 60
        return quantize_asset(d, asset)


# ------------------------------------------------------------------------------- input guards


def check_amount(value: Decimal | None, field: str = "amount") -> None:
    """Amounts are stored as NUMERIC(28,8): more than 8 fractional digits would be rounded
    silently (or to zero → CHECK violation). Reject instead (422)."""
    if value is None:
        return
    with localcontext() as c:
        c.prec = 60
        rounded = value.quantize(MONEY_Q, rounding=ROUND_HALF_EVEN)
    if rounded != value:
        raise ValidationFailed(
            "Не больше 8 знаков после точки", details={"field": field, "value": str(value)}
        )


def check_query_text(**params: str | None) -> None:
    """Query strings with U+0000 → 422 (PostgreSQL text cannot hold it; was a 500)."""
    for name, v in params.items():
        if v is not None and "\x00" in v:
            raise ValidationFailed("Недопустимый символ в запросе", details={"field": name})


_DERIVED = ("amount_usd", "amount_vnd", "account_amount", "counter_amount")


def check_derived(tx: Any) -> None:
    """Values derived by rate (≈ USD / ≈ VND / charge in the account asset) must still fit
    NUMERIC(28,8): «1 000 000 000 000 BTC» is a typo, not a 500."""
    for name in _DERIVED:
        v = getattr(tx, name, None)
        if v is not None and abs(v) >= AMOUNT_MAX:
            raise ValidationFailed("Слишком большая сумма", details={"field": name})


# Per-user writer lock (advisory, transaction-scoped). Ledger writers take it SHARED before
# their first row lock (they never block each other); wiping all data takes it EXCLUSIVE, so
# no operation can be committed half-way through the wipe (FK violation → 500).
_WRITE_LOCK_NS = 0x5046 << 48


async def writer_lock(session: AsyncSession, user_id: int, *, exclusive: bool = False) -> None:
    fn = func.pg_advisory_xact_lock if exclusive else func.pg_advisory_xact_lock_shared
    await session.execute(select(fn(_WRITE_LOCK_NS | int(user_id))))


async def check_limit(session: AsyncSession, count_stmt: Any, limit: int, what: str) -> None:
    """422 ``limit_reached`` when ``count_stmt`` (``SELECT count(*) …``) is already ``limit``."""
    n = int(await session.scalar(count_stmt) or 0)
    if n >= limit:
        raise LimitReached(f"Слишком много {what}: не больше {limit}",
                           details={"limit": limit})


# ------------------------------------------------------------------------------- text helpers


def norm(s: str | None) -> str:
    """Case/ё-insensitive key: «Налом » → «налом», «Жильё» → «жилье»."""
    if not s:
        return ""
    return " ".join(s.strip().lower().replace("ё", "е").split())


#: Same limits as the API schemas (``schemas.common.MAX_ALIASES`` / ``ALIAS_MAX_LEN``): learned
#: aliases (merchant memory, debt name forms) never grow a list past them.
MAX_ALIASES = 50
ALIAS_MAX_LEN = 64


def norm_aliases(aliases: list[str] | None, *extra: str | None,
                 limit: int = MAX_ALIASES) -> list[str]:
    """Lowercased, whitespace-collapsed, cut to ``ALIAS_MAX_LEN``, de-duplicated (after the cut),
    order kept, at most ``limit`` items (the first ones win: a new learned alias is dropped
    rather than an old one). Linear: a set, not a list scan per item."""
    out: list[str] = []
    seen: set[str] = set()
    for a in [*(aliases or []), *extra]:
        if len(out) >= limit:
            break
        n = " ".join((a or "").strip().lower().split())[:ALIAS_MAX_LEN].rstrip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def like_contains(q: str) -> str:
    """``%q%`` for LIKE with ``\\``, ``%`` and ``_`` escaped (backslash = the escape char),
    stripped and lowercased: compare it with ``lower(column)``."""
    esc = q.strip().lower().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{esc}%"


def strip_handle(h: str | None) -> str | None:
    if h is None:
        return None
    h = h.strip().lstrip("@").strip()
    return h or None


# ------------------------------------------------------------------------------- settings/dates


async def user_settings(session: AsyncSession, user_id: int) -> UserSettings | None:
    return await session.get(UserSettings, user_id)


async def user_tz(session: AsyncSession, user_id: int) -> str:
    tz = await session.scalar(select(UserSettings.timezone).where(UserSettings.user_id == user_id))
    return tz or DEFAULT_TZ


async def resolve_when(
    session: AsyncSession,
    user_id: int,
    occurred_on: date | None,
    occurred_at: datetime | None,
) -> tuple[datetime, date]:
    """(occurred_at, occurred_on) in the user's timezone. Dates more than 1 day in the
    future are rejected (a typo must not silently land next year)."""
    tz = await user_tz(session, user_id)
    try:
        if occurred_at is not None:
            if occurred_at.tzinfo is None:
                raise ValidationFailed(
                    "Время операции должно содержать часовой пояс",
                    details={"field": "occurred_at"},
                )
            on = local_date_of(occurred_at, tz)
            at = occurred_at
            if occurred_on is not None and occurred_on != on:
                # both given and they disagree: one of them is a mistake, do not pick silently
                raise ValidationFailed(
                    "Дата и время операции не совпадают",
                    details={"field": "occurred_on", "value": occurred_on.isoformat(),
                             "occurred_at_date": on.isoformat()},
                )
        else:
            on = occurred_on or local_today(tz)
            at = occurred_at_for(on, tz)
    except OverflowError as e:  # year 1 / 9999 shifted to the user's timezone
        raise ValidationFailed(
            "Дата вне допустимого диапазона", details={"field": "occurred_on"}
        ) from e
    today = local_today(tz)
    if on > today + timedelta(days=1):
        raise ValidationFailed(
            "Дата операции в будущем", details={"field": "occurred_on", "value": on.isoformat()}
        )
    if on.year < 2000:
        raise ValidationFailed("Слишком старая дата", details={"field": "occurred_on"})
    return at, on


def now() -> datetime:
    return utcnow()


# ------------------------------------------------------------------------------- rates


@dataclass(frozen=True)
class _Q:
    rate: Decimal
    rate_date: date
    source: str
    identity: bool = False
    is_fallback: bool = False


def _pref_kind(base: str, quote: str, vnd_kind: str = "sell") -> str:
    # VND conversions use the Vietcombank sell rate (charges, summaries) — or buy for money
    # credited to a ₫ account; others (CBR, CoinGecko) are mid.
    return vnd_kind if "VND" in (base, quote) else "mid"


async def _raw_rate(
    session: AsyncSession, base: str, quote: str, on: date, *, fetch: bool,
    vnd_kind: str = "sell",
) -> _Q | None:
    kind = _pref_kind(base, quote, vnd_kind)
    other = "mid" if kind == "sell" else "sell"
    q = await rates_service.get_rate(
        session, base, quote, on, kind=kind, fetch_missing=fetch  # type: ignore[arg-type]
    )
    if q is None:
        q = await rates_service.get_rate(
            session, base, quote, on, kind=other, fetch_missing=False  # type: ignore[arg-type]
        )
    if q is None or q.rate is None or q.rate <= 0:
        return None
    return _Q(
        Decimal(q.rate), q.rate_date, q.source, q.source == "identity", bool(q.is_fallback)
    )


class RateCache:
    """Per-operation memo of rate lookups. USD is the pivot (ARCH §3.5): only X↔USD legs may
    trigger a provider fetch; inverse and direct non-USD pairs are looked up without fetching."""

    def __init__(self, session: AsyncSession, on: date, *, vnd_kind: str = "sell") -> None:
        self.session = session
        self.on = on
        self.vnd_kind = vnd_kind
        self._memo: dict[tuple[str, str], _Q | None] = {}

    async def direct(self, base: str, quote: str) -> _Q | None:
        key = (base, quote)
        if key not in self._memo:
            if base == quote:
                self._memo[key] = _Q(ONE, self.on, "identity", True)
            else:
                fetch = "USD" in (base, quote)
                q = await _raw_rate(self.session, base, quote, self.on, fetch=fetch,
                                    vnd_kind=self.vnd_kind)
                if q is None:
                    inv = await _raw_rate(self.session, quote, base, self.on, fetch=False,
                                          vnd_kind=self.vnd_kind)
                    if inv is not None:
                        q = _Q(div(ONE, inv.rate), inv.rate_date, inv.source, inv.identity,
                               inv.is_fallback)
                self._memo[key] = q
        return self._memo[key]

    async def chain(self, base: str, quote: str) -> list[_Q] | None:
        """Quotes whose product converts base→quote (via USD first, else a direct pair)."""
        if base == quote or "USD" in (base, quote):
            d = await self.direct(base, quote)
            return [d] if d is not None else None
        a = await self.direct(base, "USD")
        b = await self.direct("USD", quote) if a is not None else None
        if a is not None and b is not None:
            if a.identity and b.identity:
                return [a]
            return [a, b]
        d = await self.direct(base, quote)
        return [d] if d is not None else None


def _product(chain: list[_Q]) -> Decimal:
    r = ONE
    for q in chain:
        r = mul(r, q.rate)
    return r


@dataclass
class Valuation:
    amount_usd: Decimal | None
    amount_vnd: Decimal | None
    usd_rate: Decimal | None
    rate_date: date | None
    rate_source: str | None


async def valuate(session: AsyncSession, amount: Decimal, currency: str, on: date) -> Valuation:
    """amount_usd / amount_vnd / usd_rate at ``on`` (ARCH §4.5). NULLs when no rate yet —
    the worker's backfill fills them later."""
    cur = currency.upper()
    rc = RateCache(session, on)
    usd_chain = await rc.chain(cur, "USD")
    vnd_chain = await rc.chain(cur, "VND")
    usd_rate = q_rate(_product(usd_chain)) if usd_chain else None
    amount_usd = q_money(mul(amount, _product(usd_chain))) if usd_chain else None
    amount_vnd = q_money(mul(amount, _product(vnd_chain))) if vnd_chain else None
    used = [q for q in (usd_chain or []) + (vnd_chain or []) if not q.identity]
    if used:
        rate_date, rate_source = used[0].rate_date, used[0].source
    elif usd_chain or vnd_chain:
        rate_date, rate_source = on, "identity"
    else:
        rate_date, rate_source = None, None
    return Valuation(amount_usd, amount_vnd, usd_rate, rate_date, rate_source)


async def convert_to_asset(
    session: AsyncSession, amount: Decimal, currency: str, asset: str, on: date,
    *, inflow: bool = False,
) -> tuple[Decimal, Decimal]:
    """Charge in the account's asset ≈ by rate (ARCH §4.8): (account_amount, rate).
    ``inflow`` (income/refund credited to a ₫ account): the bank buys the currency → VCB buy
    rate, otherwise sell. 503 rates_unavailable when no rate is known at all."""
    cur, asset = currency.upper(), asset.upper()
    if cur == asset:
        return amount, ONE
    vnd_kind = "buy" if inflow and asset == "VND" else "sell"
    chain = await RateCache(session, on, vnd_kind=vnd_kind).chain(cur, asset)
    if not chain:
        raise ServiceUnavailable(
            f"Нет курса {cur}→{asset}: укажите сумму списания со счёта вручную",
            code="rates_unavailable",
            details={"from": cur, "to": asset},
        )
    rate = _product(chain)
    acc_amount = q_asset(mul(amount, rate), asset)
    if acc_amount <= 0:
        acc_amount = Decimal(1).scaleb(-8)
    return acc_amount, q_rate(rate)


async def approx(session: AsyncSession, amount: Decimal, asset: str, to: str, on: date
                 ) -> Decimal | None:
    """Display-only conversion (balances ≈ $ / ≈ ₫)."""
    chain = await RateCache(session, on).chain(asset.upper(), to.upper())
    if not chain:
        return None
    return q_money(mul(amount, _product(chain)))


# ------------------------------------------------------------------------------- events


async def publish(
    session: AsyncSession,
    user_id: int,
    entities: list[str] | set[str],
    *,
    action_id: Any = None,
    source: str | None = None,
) -> None:
    ents = sorted(set(entities))
    if "transactions" in ents and "stats" not in ents:
        ents.append("stats")
    await events_bus.publish(session, user_id, ents, action_id=action_id, source=source)
