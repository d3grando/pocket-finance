"""Rates. Sources: Vietcombank (USD/VND sell), CBR (USD/RUB), CoinGecko
(crypto/USD), open.er-api fallback. Pivot through USD. docs/ARCH.md §3.5.

Storage (``currency_rates``, 1 base = rate quote):
* vietcombank — ``X/VND`` kinds sell|buy|mid (USD/VND sell = card charge & ₫ summaries);
* cbr — ``X/RUB`` mid; open_er_api — ``USD/X`` mid; coingecko — ``SYM/USD`` mid.

Lookup: every asset has a *leg* to USD; ``get_rate(a, b)`` = leg(a) ∘ leg(b). A leg uses the
nearest rate on or before the date (≤ 7 days, primary source first). Nothing in the window →
(optionally) fetch the provider for that date → otherwise the NEAREST known rate (either side
of the date) with ``is_fallback=True`` («последний известный с пометкой»). No rate → ``None``.
A fiat X other than VND/RUB has history through Vietcombank (X/VND ÷ USD/VND) and CBR
(X/RUB ÷ USD/RUB) of the same day; open.er-api (latest day only) is the reserve source.
Crypto: today → CoinGecko current price, any past day (yesterday too) → CoinGecko history.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.money import FIAT, format_money, quantize_asset
from app.core.timeutil import local_today
from app.models import AccountAsset, CurrencyRate, Transaction
from app.services.rates import providers
from app.services.rates.providers import FetchedRate, ProviderError

log = logging.getLogger("pocket.rates")

RateKindT = Literal["sell", "mid", "buy"]

#: ISO-4217 fiat codes we treat as fiat (everything else is a crypto ticker → CoinGecko).
FIAT_CODES: frozenset[str] = frozenset(FIAT) | frozenset(
    "AED ARS AUD BDT BRL CAD CHF CLP COP CZK DKK EGP GBP GEL HKD HUF IDR ILS INR JPY KHR KRW "
    "KWD KZT LAK MMK MXN MYR NGN NOK NZD PHP PKR PLN RON SAR SEK SGD TRY TWD UAH UZS ZAR AMD "
    "AZN BYN KGS TJS".split()
)
#: Assets whose CoinGecko price is always refreshed.
DEFAULT_CRYPTO = ("USDT", "TON", "BTC", "ETH")

WINDOW_DAYS = 7
SOURCE_LABEL = {
    "vietcombank": "VCB",
    "cbr": "ЦБ",
    "coingecko": "CoinGecko",
    "open_er_api": "open.er-api",
    "manual": "вручную",
    "identity": "",
}
_KIND_PREF: dict[str, tuple[str, ...]] = {
    "sell": ("sell", "mid", "buy"),
    "mid": ("mid", "sell", "buy"),
    "buy": ("buy", "mid", "sell"),
}


def is_fiat_code(code: str) -> bool:
    return code.upper() in FIAT_CODES


@dataclass(frozen=True)
class RateQuote:
    base: str
    quote: str
    rate: Decimal  # 1 base = rate quote
    rate_date: date
    source: str  # RateSource value
    kind: str = "sell"
    is_fallback: bool = False  # older than requested date / last known
    fetched_at: datetime | None = None


@dataclass(frozen=True)
class Converted:
    amount: Decimal
    currency: str
    quote: RateQuote


@dataclass(frozen=True)
class _Leg:
    """Link asset ↔ USD. ``inverted=False``: 1 asset = value USD; True: 1 USD = value asset."""

    value: Decimal
    inverted: bool
    rate_date: date
    source: str
    kind: str
    fetched_at: datetime | None = None
    is_fallback: bool = False

    @property
    def identity(self) -> bool:
        return self.source == "identity"


@dataclass(frozen=True)
class _LegSpec:
    """How an asset links to USD. ``sources`` store the pair base/quote directly; ``via`` are
    composite legs of a fiat X from one official source quoting X and USD against a cross
    currency on the same day: 1 USD = (USD/cross) / (X/cross) X — Vietcombank (X/VND) and
    CBR (X/RUB) have history for any date, open.er-api only the latest day."""

    base: str
    quote: str
    inverted: bool
    sources: tuple[str, ...]
    via: tuple[tuple[str, str], ...] = ()

    @property
    def order(self) -> tuple[str, ...]:
        """Source priority: official composites first, open.er-api is the reserve source."""
        return tuple(s for s, _ in self.via) + self.sources


#: Currencies Vietcombank quotes against VND (its /exchangerates list).
VCB_CODES: frozenset[str] = frozenset(
    "AUD CAD CHF CNY DKK EUR GBP HKD INR JPY KRW KWD MYR NOK RUB SAR SEK SGD THB USD".split()
)
_COMPOSITE_KINDS = ("mid", "sell", "buy")  # same kind on both sides; mid ≈ market


def _leg_spec(asset: str) -> _LegSpec:
    a = asset.upper()
    if a == "VND":
        return _LegSpec("USD", "VND", True, ("vietcombank", "open_er_api"))
    if a == "RUB":
        return _LegSpec("USD", "RUB", True, ("cbr", "open_er_api"))
    if is_fiat_code(a):
        return _LegSpec(
            "USD", a, True, ("open_er_api",), via=(("vietcombank", "VND"), ("cbr", "RUB"))
        )
    return _LegSpec(a, "USD", False, ("coingecko",))


def _today() -> date:
    return local_today(get_settings().default_timezone)


# ------------------------------------------------------------------------- storage


async def upsert_rates(session: AsyncSession, rows: list[FetchedRate]) -> int:
    """INSERT … ON CONFLICT (base, quote, rate_date, kind, source) DO UPDATE rate."""
    uniq: dict[tuple[str, str, date, str, str], FetchedRate] = {}
    for r in rows:
        uniq[(r.base, r.quote, r.rate_date, r.kind, r.source)] = r
    if not uniq:
        return 0
    values = [
        {
            "base": r.base,
            "quote": r.quote,
            "rate": r.rate,
            "rate_date": r.rate_date,
            "kind": r.kind,
            "source": r.source,
        }
        for r in uniq.values()
    ]
    total = 0
    for i in range(0, len(values), 500):
        stmt = pg_insert(CurrencyRate).values(values[i : i + 500])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_currency_rates_key",
            set_={"rate": stmt.excluded.rate, "fetched_at": func.now()},
        )
        with session.no_autoflush:
            await session.execute(stmt)
        total += len(values[i : i + 500])
    return total


@dataclass(frozen=True)
class _Cand:
    """A candidate leg value (already oriented like ``spec.inverted``)."""

    value: Decimal
    rate_date: date
    source: str
    kind: str
    fetched_at: datetime | None = None


def _pick(
    cands: list[_Cand], spec: _LegSpec, kind: str, on: date, *, fallback: bool
) -> _Leg | None:
    """In the window: primary source first, then the newest date, then the kind preference.
    Fallback («last known with a mark»): the NEAREST date wins (a price from tomorrow beats one
    from two weeks ago); before-the-date only breaks a tie, then the source priority."""
    if not cands:
        return None
    kinds = _KIND_PREF.get(kind, _KIND_PREF["mid"])
    order = spec.order

    def key(c: _Cand) -> tuple:  # type: ignore[type-arg]
        k = (
            order.index(c.source) if c.source in order else 99,
            kinds.index(c.kind) if c.kind in kinds else 99,
        )
        if fallback:
            return (abs((c.rate_date - on).days), c.rate_date > on, *k)
        return (k[0], -c.rate_date.toordinal(), k[1])

    best = min(cands, key=key)
    return _Leg(
        value=best.value,
        inverted=spec.inverted,
        rate_date=best.rate_date,
        source=best.source,
        kind=best.kind,
        fetched_at=best.fetched_at,
        is_fallback=fallback,
    )


def _direct_cands(rows: list[CurrencyRate]) -> list[_Cand]:
    return [_Cand(Decimal(r.rate), r.rate_date, r.source, r.kind, r.fetched_at) for r in rows]


def _composite(
    x_rows: list[CurrencyRate], usd_rows: list[CurrencyRate], source: str
) -> list[_Cand]:
    """One candidate per date where the source has both X/cross and USD/cross of one kind."""
    xs = {(r.rate_date, r.kind): r for r in x_rows}
    us = {(r.rate_date, r.kind): r for r in usd_rows}
    out: list[_Cand] = []
    for d in sorted({r.rate_date for r in x_rows}):
        for k in _COMPOSITE_KINDS:
            xr, ur = xs.get((d, k)), us.get((d, k))
            if xr is None or ur is None or not xr.rate or not ur.rate:
                continue
            fetched = [t for t in (xr.fetched_at, ur.fetched_at) if t is not None]
            out.append(
                _Cand(
                    Decimal(ur.rate) / Decimal(xr.rate),  # 1 USD = value X
                    d,
                    source,
                    k,
                    min(fetched) if fetched else None,
                )
            )
            break
    return out


async def _rows(session: AsyncSession, q, on: date, *, fallback: bool) -> list[CurrencyRate]:  # type: ignore[no-untyped-def]
    q = q.execution_options(populate_existing=True)
    if not fallback:
        res = await session.scalars(
            q.where(CurrencyRate.rate_date.between(on - timedelta(days=WINDOW_DAYS), on))
        )
        return list(res.all())
    before = await session.scalars(
        q.where(CurrencyRate.rate_date <= on).order_by(CurrencyRate.rate_date.desc()).limit(12)
    )
    after = await session.scalars(
        q.where(CurrencyRate.rate_date > on).order_by(CurrencyRate.rate_date.asc()).limit(12)
    )
    return [*before.all(), *after.all()]


async def _composite_cands(
    session: AsyncSession, asset: str, source: str, cross: str, on: date, *, fallback: bool
) -> list[_Cand]:
    base_q = select(CurrencyRate).where(CurrencyRate.source == source, CurrencyRate.quote == cross)
    x_rows = await _rows(session, base_q.where(CurrencyRate.base == asset), on, fallback=fallback)
    if not x_rows:
        return []
    dates = sorted({r.rate_date for r in x_rows})
    usd_rows = list(
        (
            await session.scalars(
                base_q.where(
                    CurrencyRate.base == "USD", CurrencyRate.rate_date.in_(dates)
                ).execution_options(populate_existing=True)
            )
        ).all()
    )
    return _composite(x_rows, usd_rows, source)


async def _leg_from_db(
    session: AsyncSession, spec: _LegSpec, kind: str, on: date, *, fallback: bool
) -> _Leg | None:
    q = select(CurrencyRate).where(
        CurrencyRate.base == spec.base,
        CurrencyRate.quote == spec.quote,
        CurrencyRate.source.in_(spec.sources),
    )
    # Called from the ledger's write path: never autoflush the caller's half-built objects.
    with session.no_autoflush:
        cands = _direct_cands(await _rows(session, q, on, fallback=fallback))
        asset = spec.quote if spec.inverted else spec.base
        for source, cross in spec.via:
            cands += await _composite_cands(session, asset, source, cross, on, fallback=fallback)
    return _pick(cands, spec, kind, on, fallback=fallback)


# Negative cache for synchronous fetches: (asset, date) → monotonic time of the failed attempt.
_FETCH_FAILED: dict[tuple[str, date], float] = {}
_FETCH_RETRY_SECONDS = 600.0


def reset_fetch_cache() -> None:
    _FETCH_FAILED.clear()


async def _fetch_fiat(a: str, on: date, today: date) -> list[FetchedRate]:
    """A fiat X other than VND/RUB: Vietcombank and CBR for the date itself (history works),
    open.er-api only for recent dates (its free API has the latest day only)."""
    day = min(on, today)
    rows: list[FetchedRate] = []
    for name, enabled, call in (
        ("vietcombank", a in VCB_CODES, lambda: providers.fetch_vietcombank(day)),
        ("cbr", True, lambda: providers.fetch_cbr(day)),
    ):
        if not enabled or any(r.base == a for r in rows):
            continue
        try:
            rows += await call()
        except ProviderError as e:
            log.warning("%s %s for %s: %s", name, day, a, e)
    if on >= today - timedelta(days=WINDOW_DAYS) and not any(r.base == a for r in rows):
        rows += await providers.fetch_open_er_api("USD")
    return rows


async def _fetch_for(asset: str, on: date) -> list[FetchedRate]:
    """Ask the provider(s) of ``asset`` for ``on``. Errors → [] (logged)."""
    a = asset.upper()
    today = _today()
    rows: list[FetchedRate] = []
    try:
        if a == "VND":
            try:
                rows = await providers.fetch_vietcombank(min(on, today))
            except ProviderError as e:
                log.warning("vietcombank %s: %s", on, e)
                if on >= today - timedelta(days=WINDOW_DAYS):
                    rows = await providers.fetch_open_er_api("USD")
        elif a == "RUB":
            try:
                rows = await providers.fetch_cbr(min(on, today))
            except ProviderError as e:
                log.warning("cbr %s: %s", on, e)
                if on >= today - timedelta(days=WINDOW_DAYS):
                    rows = await providers.fetch_open_er_api("USD")
        elif is_fiat_code(a):
            rows = await _fetch_fiat(a, on, today)
        elif on >= today:
            # today (or a future-dated op): the current price, stored under today
            rows = await providers.fetch_coingecko([a], on=today)
        else:
            # any past day — yesterday too: the current price stored under «today» is outside
            # the ≤ on window, so the op would fall back to a stale rate
            rows = await providers.fetch_coingecko_history(a, on)
    except ProviderError as e:
        log.warning("rate fetch %s %s failed: %s", a, on, e)
        return []
    except Exception:  # never break a write path because of a provider
        log.exception("rate fetch %s %s crashed", a, on)
        return []
    return rows


async def _leg(
    session: AsyncSession, asset: str, on: date, kind: str, fetch_missing: bool
) -> _Leg | None:
    a = asset.upper()
    if a == "USD":
        return _Leg(Decimal(1), False, on, "identity", kind)
    spec = _leg_spec(a)
    leg = await _leg_from_db(session, spec, kind, on, fallback=False)
    if leg is not None:
        return leg
    if fetch_missing:
        key = (a, on)
        failed_at = _FETCH_FAILED.get(key)
        if failed_at is None or time.monotonic() - failed_at > _FETCH_RETRY_SECONDS:
            rows = await _fetch_for(a, on)
            if rows:
                await upsert_rates(session, rows)
                leg = await _leg_from_db(session, spec, kind, on, fallback=False)
                if leg is not None:
                    _FETCH_FAILED.pop(key, None)
                    return leg
            _FETCH_FAILED[key] = time.monotonic()
    return await _leg_from_db(session, spec, kind, on, fallback=True)


def _combine(b: _Leg, q: _Leg) -> Decimal:
    """quote-units per 1 base, avoiding needless inversions (USD→VND stays exactly 26020)."""
    if not b.inverted and q.inverted:
        return b.value * q.value
    if b.inverted and q.inverted:
        return q.value / b.value
    if not b.inverted and not q.inverted:
        return b.value / q.value
    return Decimal(1) / (b.value * q.value)


async def get_rate(
    session: AsyncSession,
    base: str,
    quote: str,
    on: date,
    *,
    kind: Literal["sell", "mid", "buy"] = "sell",
    fetch_missing: bool = True,
) -> RateQuote | None:
    """Rate at ``on`` (nearest previous ≤ 7 days, else last known with is_fallback=True).
    May fetch from a provider synchronously (timeout RATES_HTTP_TIMEOUT) for a new asset.
    Returns None when no rate is available at all."""
    b_code, q_code = base.upper(), quote.upper()
    if b_code == q_code:
        return RateQuote(b_code, q_code, Decimal(1), on, "identity", kind)
    b = await _leg(session, b_code, on, kind, fetch_missing)
    if b is None:
        return None
    q = await _leg(session, q_code, on, kind, fetch_missing)
    if q is None:
        return None
    real = [x for x in (b, q) if not x.identity]
    main = real[0]
    fetched = [x.fetched_at for x in real if x.fetched_at is not None]
    return RateQuote(
        base=b_code,
        quote=q_code,
        rate=_combine(b, q),
        rate_date=min(x.rate_date for x in real),
        source=main.source,
        kind=main.kind,
        is_fallback=any(x.is_fallback for x in real),
        fetched_at=min(fetched) if fetched else None,
    )


async def convert(
    session: AsyncSession, amount: Decimal, from_: str, to: str, on: date, **kw: object
) -> Converted | None:
    """amount × rate (no rounding here; callers quantize). None if no rate."""
    q = await get_rate(session, from_, to, on, **kw)  # type: ignore[arg-type]
    if q is None:
        return None
    return Converted(amount * q.rate, to.upper(), q)


# ------------------------------------------------------------ helpers for the ledger


@dataclass(frozen=True)
class TxAmounts:
    """Summary amounts of an operation at its date (ARCH §4.5). Fields are None without a rate."""

    amount_usd: Decimal | None
    amount_vnd: Decimal | None
    usd_rate: Decimal | None  # currency → USD
    rate_date: date | None
    rate_source: str | None
    is_fallback: bool = False


def _q8(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.00000001"))


async def tx_amounts(
    session: AsyncSession, amount: Decimal, currency: str, on: date, *, fetch_missing: bool = True
) -> TxAmounts:
    """amount_usd / amount_vnd / usd_rate / rate_date / rate_source for an operation.
    VND legs use the Vietcombank *sell* rate (ARCH §3.5) so ₫ and $ summaries agree."""
    cur = currency.upper()
    usd = await get_rate(session, cur, "USD", on, kind="sell", fetch_missing=fetch_missing)
    vnd = await get_rate(session, cur, "VND", on, kind="sell", fetch_missing=fetch_missing)
    ref = usd if usd is not None and usd.source != "identity" else (vnd or usd)
    return TxAmounts(
        amount_usd=_q8(amount * usd.rate) if usd else None,
        amount_vnd=_q8(amount * vnd.rate) if vnd else None,
        usd_rate=usd.rate if usd else None,
        rate_date=(ref.rate_date if ref else None),
        rate_source=(ref.source if ref else None),
        is_fallback=bool((usd and usd.is_fallback) or (vnd and vnd.is_fallback)),
    )


@dataclass(frozen=True)
class Charge:
    """Estimated charge of an operation in the account asset (ARCH §4.8)."""

    account_amount: Decimal  # quantized to the account asset precision
    account_rate: Decimal  # currency → account asset (incl. fee)
    is_estimated: bool
    quote: RateQuote


async def estimate_charge(
    session: AsyncSession,
    amount: Decimal,
    currency: str,
    account_asset: str,
    on: date,
    *,
    fee_pct: Decimal = Decimal(0),
    fetch_missing: bool = True,
) -> Charge | None:
    """«$ с карты в ₫»: USD spend from a ₫ account = amount × VCB sell × (1 + fee%/100).
    fee% is the account's card fee (0 by default). Same asset → 1:1, not estimated.
    None if there is no rate (caller writes the operation without account_amount)."""
    cur, asset = currency.upper(), account_asset.upper()
    if cur == asset:
        q = RateQuote(cur, asset, Decimal(1), on, "identity", "sell")
        return Charge(amount, Decimal(1), False, q)
    q = await get_rate(session, cur, asset, on, kind="sell", fetch_missing=fetch_missing)
    if q is None:
        return None
    factor = Decimal(1) + (fee_pct or Decimal(0)) / Decimal(100)
    rate = q.rate * factor
    return Charge(quantize_asset(amount * rate, asset), rate, True, q)


# --------------------------------------------------------------------- refreshers


async def refresh_fiat(session: AsyncSession, on: date | None = None) -> int:
    """Fetch & upsert VCB/CBR/open.er-api rates for today. Returns rows written.
    Each provider is independent: one failing does not stop the others."""
    day = on or _today()
    written = 0
    for name, call in (
        ("vietcombank", lambda: providers.fetch_vietcombank(day)),
        ("cbr", lambda: providers.fetch_cbr(day)),
        ("open_er_api", lambda: providers.fetch_open_er_api("USD")),
    ):
        try:
            rows = await call()
        except ProviderError as e:
            log.warning("refresh %s failed: %s", name, e)
            continue
        except Exception:
            log.exception("refresh %s crashed", name)
            continue
        written += await upsert_rates(session, rows)
    return written


async def crypto_assets_in_use(session: AsyncSession, days: int = 30) -> list[str]:
    """Crypto assets on accounts and in operations of the last ``days`` days (+ defaults)."""
    since = _today() - timedelta(days=days)
    found: set[str] = set(DEFAULT_CRYPTO)
    found.update(
        a.upper() for a in (await session.scalars(select(AccountAsset.asset).distinct())).all()
    )
    for col in (Transaction.currency, Transaction.account_asset, Transaction.counter_asset):
        res = await session.scalars(
            select(col).where(col.is_not(None), Transaction.occurred_on >= since).distinct()
        )
        found.update(a.upper() for a in res.all() if a)
    return sorted(a for a in found if a != "USD" and not is_fiat_code(a))


async def refresh_crypto(session: AsyncSession, assets: list[str] | None = None) -> int:
    """CoinGecko USD prices for the assets in use. Returns rows written."""
    syms = assets if assets is not None else await crypto_assets_in_use(session)
    syms = [s.upper() for s in syms if s and not is_fiat_code(s) and s.upper() != "USD"]
    if not syms:
        return 0
    try:
        rows = await providers.fetch_coingecko(syms, on=_today())
    except ProviderError as e:
        log.warning("refresh coingecko failed: %s", e)
        return 0
    except Exception:
        log.exception("refresh coingecko crashed")
        return 0
    return await upsert_rates(session, rows)


async def backfill_missing(session: AsyncSession, limit: int = 500) -> int:
    """Fill amount_usd/amount_vnd/usd_rate for transactions where they are NULL; also improve
    rows written with a fallback rate (rate_date more than 7 days before the operation, or
    AFTER it — a later price used for a past day) once a proper rate exists. No version bump
    (system fields). Returns rows updated."""
    stale = and_(
        Transaction.rate_date.is_not(None),
        or_(
            Transaction.rate_date < Transaction.occurred_on - WINDOW_DAYS,
            Transaction.rate_date > Transaction.occurred_on,
        ),
        Transaction.occurred_on <= _today(),
    )
    rows = (
        await session.execute(
            select(
                Transaction.id,
                Transaction.user_id,
                Transaction.amount,
                Transaction.currency,
                Transaction.occurred_on,
                Transaction.amount_usd,
                Transaction.amount_vnd,
                Transaction.kind,
                Transaction.account_asset,
                Transaction.account_amount,
                Transaction.is_estimated,
            )
            .where(
                Transaction.deleted_at.is_(None),
                or_(Transaction.amount_usd.is_(None), Transaction.amount_vnd.is_(None), stale),
            )
            .order_by(Transaction.occurred_on.desc(), Transaction.id.desc())
            .limit(limit)
        )
    ).all()
    updated = 0
    users: set[int] = set()
    for r in rows:
        ta = await tx_amounts(session, Decimal(r.amount), r.currency, r.occurred_on)
        if ta.amount_usd is None and ta.amount_vnd is None:
            continue
        was_missing = r.amount_usd is None or r.amount_vnd is None
        if not was_missing and ta.is_fallback:
            continue  # nothing better than what is stored
        new_vnd = ta.amount_vnd if ta.amount_vnd is not None else r.amount_vnd
        if (
            r.kind in ("expense", "income", "refund")
            and r.account_asset == "VND"
            and r.currency != "VND"
            and r.is_estimated is False
            and r.account_amount is not None
        ):
            # a confirmed ₫ charge: amount_vnd is the actual debit, not a rate estimate
            # (same rule as ledger.transactions.use_actual_charge)
            new_vnd = Decimal(r.account_amount)
        await session.execute(
            update(Transaction)
            .where(Transaction.id == r.id)
            .values(
                amount_usd=ta.amount_usd if ta.amount_usd is not None else r.amount_usd,
                amount_vnd=new_vnd,
                usd_rate=ta.usd_rate,
                rate_date=ta.rate_date,
                rate_source=ta.rate_source,
            )
            .execution_options(synchronize_session=False)
        )
        updated += 1
        users.add(r.user_id)
    if users:
        from app.services.events.bus import publish

        for uid in users:
            await publish(session, uid, ["transactions", "stats"], source="system")
    return updated


# ------------------------------------------------------------------------- display


def fmt_rate_value(v: Decimal, currency: str) -> str:
    """Rate amount for labels: fiat as money, tiny crypto prices with enough digits."""
    cur = currency.upper()
    if cur == "USD" and abs(v) < 1:
        s = f"{v.normalize():f}"
        ip, _, fp = s.partition(".")
        nz = len(fp) - len(fp.lstrip("0"))
        keep = min(len(fp), max(2, nz + 4))
        return "$" + ip + ("." + fp[:keep].rstrip("0").ljust(2, "0") if fp else ".00")
    if cur == "RUB":
        return format_money(v.quantize(Decimal("0.01")), "RUB")
    return format_money(v, cur)


def rate_label(q: RateQuote) -> str:
    """«$1 = 26 020 ₫ (VCB, 27.10)», «1 TON ≈ $1.22», «$1 = 80.49 ₽ (ЦБ, 27.10)»."""
    src = SOURCE_LABEL.get(q.source, q.source)
    when = q.rate_date.strftime("%d.%m")
    tail = f" ({src}, {when})" if src else ""
    if q.base == "USD":
        return f"$1 = {fmt_rate_value(q.rate, q.quote)}{tail}"
    if q.quote == "USD" and not is_fiat_code(q.base):
        return f"1 {q.base} ≈ {fmt_rate_value(q.rate, 'USD')}{tail}"
    return f"1 {q.base} = {fmt_rate_value(q.rate, q.quote)}{tail}"


async def latest_crypto(session: AsyncSession, limit: int = 30) -> list[RateQuote]:
    """Latest CoinGecko price per asset (assets in use first)."""
    in_use = await crypto_assets_in_use(session)
    sub = (
        select(CurrencyRate)
        .where(CurrencyRate.source == "coingecko", CurrencyRate.quote == "USD")
        .ext(distinct_on(CurrencyRate.base))
        .order_by(CurrencyRate.base, CurrencyRate.rate_date.desc(), CurrencyRate.fetched_at.desc())
    )
    rows = list((await session.scalars(sub)).all())
    rows.sort(key=lambda r: (r.base not in in_use, r.base))
    today = _today()
    return [
        RateQuote(
            r.base,
            "USD",
            Decimal(r.rate),
            r.rate_date,
            r.source,
            r.kind,
            is_fallback=(today - r.rate_date).days > 1,
            fetched_at=r.fetched_at,
        )
        for r in rows[:limit]
    ]
