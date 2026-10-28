"""HTTP rate providers. httpx; mocked with respx in tests.

Every fetcher returns a list of :class:`FetchedRate` rows ready for ``currency_rates``
(1 base = rate quote). Money/rates are parsed straight into ``Decimal`` — JSON floats are
read with ``parse_float=Decimal``, never through ``float``.

Sources :
* Vietcombank — ``https://www.vietcombank.com.vn/api/exchangerates?date=YYYY-MM-DD``:
  X/VND ``sell`` (bank sells X — used for card charges and ₫ summaries), ``buy`` (transfer)
  and ``mid``. Other bank sites sit behind anti-bot walls from a server — not used.
* ЦБ РФ — ``https://www.cbr.ru/scripts/XML_daily.asp?date_req=DD/MM/YYYY`` (handles weekends:
  returns the rate in force), fallback ``cbr-xml-daily.ru`` JSON.
* CoinGecko — ``/simple/price`` (+ ``/search`` to resolve a ticker; ticker collisions →
  the largest market cap, i.e. the smallest ``market_cap_rank``), ``/coins/{id}/history``.
* open.er-api.com — fallback for fiat (``USD`` base).
"""

from __future__ import annotations

import json
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.config import get_settings
from app.core.timeutil import local_today

log = logging.getLogger("pocket.rates")

VCB_URL = "https://www.vietcombank.com.vn/api/exchangerates"
CBR_XML_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
CBR_JSON_DAILY_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
CBR_JSON_ARCHIVE_URL = "https://www.cbr-xml-daily.ru/archive/{y:04d}/{m:02d}/{d:02d}/daily_json.js"
COINGECKO_URL = "https://api.coingecko.com/api/v3"
OPEN_ER_URL = "https://open.er-api.com/v6/latest/{base}"

# Browser-like prefix: some bank sites reject bare non-browser agents.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) PocketFinance/1.0 (+https://github.com/d3grando/pocket-finance)"
)

#: Well-known tickers → CoinGecko ids. Checked first: a bare symbol search is ambiguous
#: (in 2026 the CoinGecko symbol "TON" belongs to tokamak-network, while Toncoin was renamed
#: to "Gram" — «тон» in user input is Toncoin = ``the-open-network``).
COINGECKO_IDS: dict[str, str] = {
    "TON": "the-open-network",
    "TONCOIN": "the-open-network",
    "GRAM": "the-open-network",
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "USDT": "tether",
    "USDC": "usd-coin",
    "DAI": "dai",
    "BNB": "binancecoin",
    "SOL": "solana",
    "TRX": "tron",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "LTC": "litecoin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "ATOM": "cosmos",
    "XMR": "monero",
    "BCH": "bitcoin-cash",
    "SHIB": "shiba-inu",
    "NOT": "notcoin",
    "SUI": "sui",
    "NEAR": "near",
    "APT": "aptos",
    "ARB": "arbitrum",
    "OP": "optimism",
    "PEPE": "pepe",
}


class ProviderError(Exception):
    """Provider unreachable / bad payload. Callers fall back to the last known rate."""


@dataclass(frozen=True)
class FetchedRate:
    base: str
    quote: str
    rate: Decimal  # 1 base = rate quote
    rate_date: date
    kind: str  # mid | sell | buy
    source: str  # RateSource value


def _client() -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(
        timeout=httpx.Timeout(s.rates_http_timeout),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/xml, */*"},
        follow_redirects=True,
    )


def _dec(v: Any) -> Decimal | None:
    """Parse a provider number ("26020.00", "80,4909", Decimal) → positive Decimal or None."""
    if v is None:
        return None
    if isinstance(v, float):  # never expected: JSON is parsed with parse_float=Decimal
        v = repr(v)
    s = str(v).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not s or s in {"-", "0", "0.00"}:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if not d.is_finite() or d <= 0:
        return None
    return d


def _json(resp: httpx.Response) -> Any:
    try:
        return json.loads(resp.content, parse_float=Decimal)
    except (ValueError, UnicodeDecodeError) as e:
        raise ProviderError(f"bad JSON from {resp.request.url}: {resp.text[:120]!r}") from e


async def _get(client: httpx.AsyncClient, url: str, **params: Any) -> httpx.Response:
    try:
        resp = await client.get(url, params=params or None)
    except httpx.HTTPError as e:
        raise ProviderError(f"{url}: {type(e).__name__}: {e}") from e
    return resp


# ------------------------------------------------------------------------- Vietcombank


async def fetch_vietcombank(on: date) -> list[FetchedRate]:
    """X/VND rates of Vietcombank for ``on``: kinds sell, buy (transfer) and mid."""
    async with _client() as c:
        resp = await _get(c, VCB_URL, date=on.isoformat())
    if resp.status_code != 200:
        raise ProviderError(f"vietcombank HTTP {resp.status_code}")
    data = _json(resp)
    rows = data.get("Data") if isinstance(data, dict) else None
    if not rows:
        raise ProviderError("vietcombank: empty Data")
    try:
        rate_date = date.fromisoformat(str(data.get("Date", ""))[:10])
    except ValueError:
        rate_date = on
    out: list[FetchedRate] = []
    for row in rows:
        code = str(row.get("currencyCode", "")).strip().upper()
        if not code or code == "VND":
            continue
        sell = _dec(row.get("sell"))
        buy = _dec(row.get("transfer")) or _dec(row.get("cash"))
        if sell is not None:
            out.append(FetchedRate(code, "VND", sell, rate_date, "sell", "vietcombank"))
        if buy is not None:
            out.append(FetchedRate(code, "VND", buy, rate_date, "buy", "vietcombank"))
        if sell is not None and buy is not None:
            out.append(FetchedRate(code, "VND", (sell + buy) / 2, rate_date, "mid", "vietcombank"))
    if not any(r.base == "USD" and r.kind == "sell" for r in out):
        raise ProviderError("vietcombank: no USD sell rate")
    return out


# ------------------------------------------------------------------------------ CBR


def _parse_cbr_xml(content: bytes) -> list[FetchedRate]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ProviderError(f"cbr: bad XML: {e}") from e
    try:
        rate_date = datetime.strptime(root.attrib.get("Date", ""), "%d.%m.%Y").date()
    except ValueError as e:
        raise ProviderError("cbr: no ValCurs Date") from e
    out: list[FetchedRate] = []
    for v in root.findall("Valute"):
        code = (v.findtext("CharCode") or "").strip().upper()
        nominal = _dec(v.findtext("Nominal"))
        value = _dec(v.findtext("Value"))
        if not code or nominal is None or value is None:
            continue
        out.append(FetchedRate(code, "RUB", value / nominal, rate_date, "mid", "cbr"))
    return out


def _parse_cbr_json(data: Any) -> list[FetchedRate]:
    try:
        rate_date = date.fromisoformat(str(data["Date"])[:10])
        valutes = data["Valute"]
    except (KeyError, TypeError, ValueError) as e:
        raise ProviderError("cbr-json: bad payload") from e
    out: list[FetchedRate] = []
    for code, v in valutes.items():
        nominal = _dec(v.get("Nominal"))
        value = _dec(v.get("Value"))
        if nominal is None or value is None:
            continue
        out.append(FetchedRate(str(code).upper(), "RUB", value / nominal, rate_date, "mid", "cbr"))
    return out


async def fetch_cbr(on: date) -> list[FetchedRate]:
    """X/RUB official rates in force on ``on`` (RUB per 1 unit; weekends → last working day)."""
    errors: list[str] = []
    async with _client() as c:
        try:
            resp = await _get(c, CBR_XML_URL, date_req=on.strftime("%d/%m/%Y"))
            if resp.status_code == 200:
                out = _parse_cbr_xml(resp.content)
                if any(r.base == "USD" for r in out):
                    return out
            errors.append(f"xml HTTP {resp.status_code}")
        except ProviderError as e:
            errors.append(str(e))
        # Fallback: cbr-xml-daily.ru JSON (today → daily, past → archive walking back).
        today = local_today(None)
        if on >= today:
            urls = [CBR_JSON_DAILY_URL]
        else:
            urls = [
                CBR_JSON_ARCHIVE_URL.format(y=d.year, m=d.month, d=d.day)
                for d in (on - timedelta(days=i) for i in range(7))
            ]
        for url in urls:
            try:
                resp = await _get(c, url)
            except ProviderError as e:
                errors.append(str(e))
                break
            if resp.status_code == 404:
                continue
            if resp.status_code != 200:
                errors.append(f"json HTTP {resp.status_code}")
                break
            out = _parse_cbr_json(_json(resp))
            if any(r.base == "USD" for r in out):
                return out
    raise ProviderError("cbr: " + "; ".join(errors or ["no data"]))


# ------------------------------------------------------------------------ CoinGecko

_ID_CACHE: dict[str, tuple[str | None, float]] = {}
_ID_TTL = 24 * 3600.0
_ID_NEG_TTL = 600.0


def _cg_headers() -> dict[str, str]:
    key = get_settings().coingecko_api_key
    return {"x-cg-demo-api-key": key} if key else {}


def pick_coin(symbol: str, coins: list[dict[str, Any]]) -> str | None:
    """From /search results choose the coin whose symbol equals ``symbol``; on a collision —
    the largest market cap (smallest market_cap_rank; unranked last)."""
    sym = symbol.upper()
    matches = [c for c in coins if str(c.get("symbol", "")).upper() == sym and c.get("id")]
    if not matches:
        return None

    def rank(c: dict[str, Any]) -> int:
        r = c.get("market_cap_rank")
        try:
            return int(r) if r is not None else 10**9
        except (TypeError, ValueError):
            return 10**9

    return str(min(matches, key=rank)["id"])


async def resolve_coingecko_id(symbol: str, client: httpx.AsyncClient | None = None) -> str | None:
    """Ticker → CoinGecko id (curated map, then /search). Cached in-process."""
    sym = symbol.upper()
    if sym in COINGECKO_IDS:
        return COINGECKO_IDS[sym]
    cached = _ID_CACHE.get(sym)
    now = time.monotonic()
    if cached and now - cached[1] < (_ID_TTL if cached[0] else _ID_NEG_TTL):
        return cached[0]
    own = client is None
    c = client or _client()
    try:
        resp = await _get(c, f"{COINGECKO_URL}/search", query=sym)
        if resp.status_code != 200:
            raise ProviderError(f"coingecko search HTTP {resp.status_code}")
        data = _json(resp)
    finally:
        if own:
            await c.aclose()
    coin_id = pick_coin(sym, list(data.get("coins") or []))
    _ID_CACHE[sym] = (coin_id, now)
    return coin_id


async def fetch_coingecko(symbols: list[str], on: date | None = None) -> list[FetchedRate]:
    """Current USD prices: SYMBOL/USD (kind mid). Unknown tickers are skipped."""
    syms = sorted({s.upper() for s in symbols if s})
    if not syms:
        return []
    rate_date = on or local_today(None)
    async with _client() as c:
        c.headers.update(_cg_headers())
        ids: dict[str, str] = {}
        for s in syms:
            try:
                cid = await resolve_coingecko_id(s, c)
            except ProviderError as e:
                log.warning("coingecko id for %s: %s", s, e)
                continue
            if cid:
                ids[s] = cid
        if not ids:
            return []
        resp = await _get(
            c,
            f"{COINGECKO_URL}/simple/price",
            ids=",".join(sorted(set(ids.values()))),
            vs_currencies="usd",
        )
        if resp.status_code != 200:
            raise ProviderError(f"coingecko price HTTP {resp.status_code}: {resp.text[:80]!r}")
        data = _json(resp)
    out: list[FetchedRate] = []
    for s, cid in ids.items():
        price = _dec((data.get(cid) or {}).get("usd"))
        if price is not None:
            out.append(FetchedRate(s, "USD", price, rate_date, "mid", "coingecko"))
    return out


async def fetch_coingecko_history(symbol: str, on: date) -> list[FetchedRate]:
    """Historic daily USD price (CoinGecko /coins/{id}/history, 00:00 UTC of ``on``)."""
    async with _client() as c:
        c.headers.update(_cg_headers())
        cid = await resolve_coingecko_id(symbol, c)
        if not cid:
            return []
        resp = await _get(
            c,
            f"{COINGECKO_URL}/coins/{cid}/history",
            date=on.strftime("%d-%m-%Y"),
            localization="false",
        )
        if resp.status_code != 200:
            raise ProviderError(f"coingecko history HTTP {resp.status_code}")
        data = _json(resp)
    price = _dec(((data.get("market_data") or {}).get("current_price") or {}).get("usd"))
    if price is None:
        return []
    return [FetchedRate(symbol.upper(), "USD", price, on, "mid", "coingecko")]


# ----------------------------------------------------------------------- open.er-api


async def fetch_open_er_api(base: str = "USD") -> list[FetchedRate]:
    """Latest fiat rates: base/X (kind mid). Only the latest day is available (free API)."""
    async with _client() as c:
        resp = await _get(c, OPEN_ER_URL.format(base=base.upper()))
    if resp.status_code != 200:
        raise ProviderError(f"open.er-api HTTP {resp.status_code}")
    data = _json(resp)
    if not isinstance(data, dict) or data.get("result") != "success":
        raise ProviderError("open.er-api: result != success")
    try:
        ts = int(data.get("time_last_update_unix"))
        rate_date = datetime.fromtimestamp(ts, UTC).date()
    except (TypeError, ValueError):
        rate_date = datetime.now(UTC).date()
    b = base.upper()
    out: list[FetchedRate] = []
    for code, v in (data.get("rates") or {}).items():
        q = str(code).upper()
        r = _dec(v)
        if q == b or r is None:
            continue
        out.append(FetchedRate(b, q, r, rate_date, "mid", "open_er_api"))
    return out
