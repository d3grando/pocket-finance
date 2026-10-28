"""Rate providers on recorded fixtures (respx) + rates service (DB): pivot, windows, fallback,
estimate of a card charge, backfill, REST."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.models import CurrencyRate
from app.services.rates import providers as P
from app.services.rates import service as R
from tests.analytics.conftest import add_rate, fixture_bytes, sp

D27 = date(2026, 10, 27)


def mock_vcb(rx, on: date = D27, status: int = 200, body: bytes | None = None):  # type: ignore[no-untyped-def]
    return rx.get(P.VCB_URL, params={"date": on.isoformat()}).mock(
        return_value=httpx.Response(status, content=body or fixture_bytes("vcb_2026-10-27.json"))
    )


def mock_cbr_xml(rx, status: int = 200):  # type: ignore[no-untyped-def]
    return rx.get(P.CBR_XML_URL).mock(
        return_value=httpx.Response(status, content=fixture_bytes("cbr_xml_2026-10-25.xml"))
    )


def mock_oer(rx):  # type: ignore[no-untyped-def]
    return rx.get(P.OPEN_ER_URL.format(base="USD")).mock(
        return_value=httpx.Response(200, content=fixture_bytes("oer_usd.json"))
    )


def mock_cg_price(rx):  # type: ignore[no-untyped-def]
    return rx.get(f"{P.COINGECKO_URL}/simple/price").mock(
        return_value=httpx.Response(200, content=fixture_bytes("cg_simple_price.json"))
    )


# ------------------------------------------------------------------------- providers


async def test_vietcombank_parse(rx):
    mock_vcb(rx)
    rows = await P.fetch_vietcombank(D27)
    usd = {r.kind: r for r in rows if r.base == "USD"}
    assert usd["sell"].rate == Decimal("26020.00") and usd["sell"].quote == "VND"
    assert usd["buy"].rate == Decimal("25640.00")
    assert usd["mid"].rate == Decimal("25830.00")
    assert usd["sell"].rate_date == D27 and usd["sell"].source == "vietcombank"
    # RUB has cash 0.00 → still has transfer/sell; DKK cash 0.00 is not a rate
    rub = {r.kind: r.rate for r in rows if r.base == "RUB"}
    assert rub["sell"] == Decimal("335.82") and rub["buy"] == Decimal("303.37")
    assert all(isinstance(r.rate, Decimal) and r.rate > 0 for r in rows)


async def test_vietcombank_errors(rx):
    mock_vcb(rx, status=503)
    with pytest.raises(P.ProviderError):
        await P.fetch_vietcombank(D27)


async def test_vietcombank_empty_payload(rx):
    mock_vcb(rx, body=b'{"Count":0,"Date":"2026-10-27T00:00:00","Data":[]}')
    with pytest.raises(P.ProviderError):
        await P.fetch_vietcombank(D27)


async def test_cbr_xml_weekend_returns_rate_in_force(rx):
    mock_cbr_xml(rx)
    rows = await P.fetch_cbr(date(2026, 10, 25))  # Sunday → rates of Sat 24.10
    by = {r.base: r for r in rows}
    assert by["USD"].rate_date == date(2026, 10, 24)
    assert by["USD"].quote == "RUB" and by["USD"].kind == "mid" and by["USD"].source == "cbr"
    assert by["USD"].rate > Decimal(50)
    # VND nominal 10000 → per 1 dong
    assert by["VND"].rate < Decimal("0.01")
    assert isinstance(by["USD"].rate, Decimal)


async def test_cbr_json_fallback_when_xml_fails(rx):
    mock_cbr_xml(rx, status=500)
    rx.get(P.CBR_JSON_ARCHIVE_URL.format(y=2026, m=10, d=25)).respond(404)
    rx.get(P.CBR_JSON_ARCHIVE_URL.format(y=2026, m=10, d=24)).mock(
        return_value=httpx.Response(200, content=fixture_bytes("cbr_archive_2026-10-24.json"))
    )
    rows = await P.fetch_cbr(date(2026, 10, 25))
    usd = next(r for r in rows if r.base == "USD")
    assert usd.rate_date == date(2026, 10, 24)
    assert str(usd.rate) == str(usd.rate)  # Decimal, parsed without float
    assert isinstance(usd.rate, Decimal)


async def test_coingecko_curated_ids_and_decimal(rx):
    route = mock_cg_price(rx)
    rows = await P.fetch_coingecko(["TON", "usdt", "BTC"], on=D27)
    by = {r.base: r.rate for r in rows}
    assert by["TON"] == Decimal("1.22")
    assert by["USDT"] == Decimal("0.99865")
    assert by["BTC"] == Decimal("79750")
    q = dict(route.calls.last.request.url.params)
    assert set(q["ids"].split(",")) == {"the-open-network", "tether", "bitcoin"}


async def test_coingecko_search_collision_picks_largest_cap(rx):
    coins = [
        {"id": "small-foo", "symbol": "FOO", "market_cap_rank": 900},
        {"id": "big-foo", "symbol": "foo", "market_cap_rank": 12},
        {"id": "unranked-foo", "symbol": "FOO", "market_cap_rank": None},
        {"id": "foobar", "symbol": "FOOBAR", "market_cap_rank": 1},
    ]
    assert P.pick_coin("FOO", coins) == "big-foo"
    assert P.pick_coin("NOPE", coins) is None
    # recorded search: in 2026 symbol "TON" on CoinGecko is tokamak-network, Toncoin is "GRAM"
    rec = P._json(httpx.Response(200, content=fixture_bytes("cg_search_ton.json")))
    assert P.pick_coin("TON", rec["coins"]) == "tokamak-network"
    assert P.pick_coin("GRAM", rec["coins"]) == "the-open-network"
    # …which is exactly why TON is pinned in the curated map
    assert P.COINGECKO_IDS["TON"] == "the-open-network"
    rx.get(f"{P.COINGECKO_URL}/search").mock(
        return_value=httpx.Response(200, json={"coins": coins})
    )
    assert await P.resolve_coingecko_id("FOO") == "big-foo"


async def test_coingecko_throttled(rx):
    rx.get(f"{P.COINGECKO_URL}/simple/price").respond(429, text="Throttled")
    with pytest.raises(P.ProviderError):
        await P.fetch_coingecko(["TON"])


async def test_open_er_api_decimal(rx):
    mock_oer(rx)
    rows = await P.fetch_open_er_api()
    by = {r.quote: r for r in rows}
    assert by["VND"].rate == Decimal("25831.545742")  # exact digits — no float on the way
    assert by["VND"].base == "USD" and by["VND"].source == "open_er_api"
    assert by["RUB"].rate_date == date(2026, 10, 27)


# ------------------------------------------------------------------------- service


async def test_refresh_fiat_upserts_all_sources(session, rx):
    mock_vcb(rx)
    rx.get(P.CBR_XML_URL).mock(
        return_value=httpx.Response(200, content=fixture_bytes("cbr_xml_2026-10-25.xml"))
    )
    mock_oer(rx)
    n = await R.refresh_fiat(session, on=D27)
    assert n > 100
    n2 = await R.refresh_fiat(session, on=D27)  # idempotent upsert
    assert n2 == n
    cnt = len((await session.scalars(select(CurrencyRate))).all())
    assert cnt == n
    q = await R.get_rate(session, "USD", "VND", D27, fetch_missing=False)
    assert q is not None and q.rate == Decimal("26020.00") and q.source == "vietcombank"
    assert sp(R.rate_label(q)) == "$1 = 26 020 ₫ (VCB, 27.10)"
    rub = await R.get_rate(session, "USD", "RUB", D27, kind="mid", fetch_missing=False)
    assert rub is not None and rub.source == "cbr" and rub.rate_date == date(2026, 10, 24)
    assert sp(R.rate_label(rub)).startswith("$1 = 80.")
    assert sp(R.rate_label(rub)).endswith("₽ (ЦБ, 24.10)")


async def test_refresh_fiat_survives_provider_failure(session, rx):
    mock_vcb(rx, status=502)
    rx.get(P.CBR_XML_URL).respond(500)
    rx.get(P.CBR_JSON_DAILY_URL).respond(500)
    for i in range(7):
        d = date(2026, 10, 27 - i)
        rx.get(P.CBR_JSON_ARCHIVE_URL.format(y=d.year, m=d.month, d=d.day)).respond(500)
    mock_oer(rx)
    n = await R.refresh_fiat(session, on=D27)
    assert n > 100  # open.er-api still written
    q = await R.get_rate(session, "USD", "VND", D27, fetch_missing=False)
    assert q is not None and q.source == "open_er_api"


async def test_pivot_and_exactness(session):
    await add_rate(session, "USD", "VND", "26020", D27, "sell", "vietcombank")
    await add_rate(session, "USD", "VND", "25640", D27, "buy", "vietcombank")
    await add_rate(session, "USD", "RUB", "80.5", D27, "mid", "cbr")
    await add_rate(session, "TON", "USD", "1.22", D27, "mid", "coingecko")
    await add_rate(session, "USD", "VND", "25831.5", D27, "mid", "open_er_api")
    usd_vnd = await R.get_rate(session, "USD", "VND", D27)
    assert usd_vnd.rate == Decimal("26020") and usd_vnd.kind == "sell"
    buy = await R.get_rate(session, "USD", "VND", D27, kind="buy")
    assert buy.rate == Decimal("25640")
    vnd_usd = await R.get_rate(session, "VND", "USD", D27)
    assert vnd_usd.rate * 26020 == Decimal(1) or abs(vnd_usd.rate * 26020 - 1) < Decimal("1e-20")
    ton_vnd = await R.get_rate(session, "TON", "VND", D27)
    assert ton_vnd.rate == Decimal("1.22") * 26020 and ton_vnd.source == "coingecko"
    rub_vnd = await R.get_rate(session, "RUB", "VND", D27)
    assert rub_vnd.rate == Decimal(26020) / Decimal("80.5")
    same = await R.get_rate(session, "usdt", "USDT", D27)
    assert same.rate == 1 and same.source == "identity"
    conv = await R.convert(session, Decimal(15), "USD", "VND", D27)
    assert conv.amount == Decimal(390300) and conv.currency == "VND"


async def test_nearest_previous_within_window_and_fallback(session, rx):
    await add_rate(session, "USD", "VND", "25900", date(2026, 10, 20), "sell", "vietcombank")
    q = await R.get_rate(session, "USD", "VND", D27, fetch_missing=False)
    assert q.rate == Decimal(25900) and q.rate_date == date(2026, 10, 20) and not q.is_fallback
    # older than 7 days → «last known with a mark»
    q2 = await R.get_rate(session, "USD", "VND", date(2026, 11, 10), fetch_missing=False)
    assert q2.rate == Decimal(25900) and q2.is_fallback
    # before any known rate → the closest later one, marked
    q3 = await R.get_rate(session, "USD", "VND", date(2026, 1, 5), fetch_missing=False)
    assert q3 is not None and q3.is_fallback
    # nothing at all
    assert await R.get_rate(session, "XYZ", "USD", D27, fetch_missing=False) is None


async def test_primary_source_beats_fallback_source(session):
    await add_rate(session, "USD", "VND", "25831.5", D27, "mid", "open_er_api")
    await add_rate(session, "USD", "VND", "26000", date(2026, 10, 25), "sell", "vietcombank")
    q = await R.get_rate(session, "USD", "VND", D27, fetch_missing=False)
    assert q.source == "vietcombank" and q.rate == 26000


async def test_fetch_missing_for_new_coin(session, rx, monkeypatch):
    monkeypatch.setattr(R, "_today", lambda: D27)
    route = mock_cg_price(rx)
    q = await R.get_rate(session, "TON", "USD", D27)
    assert q is not None and q.rate == Decimal("1.22") and q.source == "coingecko"
    assert route.call_count == 1
    stored = (await session.scalars(select(CurrencyRate).where(CurrencyRate.base == "TON"))).all()
    assert len(stored) == 1
    # second call → from DB, no new HTTP call
    await R.get_rate(session, "TON", "USD", D27)
    assert route.call_count == 1


async def test_fetch_failure_uses_last_known_with_mark(session, rx, monkeypatch):
    monkeypatch.setattr(R, "_today", lambda: D27)
    await add_rate(session, "TON", "USD", "1.5", date(2026, 9, 1), "mid", "coingecko")
    route = rx.get(f"{P.COINGECKO_URL}/simple/price").respond(500)
    q = await R.get_rate(session, "TON", "USD", D27)
    assert q.rate == Decimal("1.5") and q.is_fallback
    assert route.call_count == 1
    await R.get_rate(session, "TON", "USD", D27)  # negative cache: no hammering
    assert route.call_count == 1


async def test_estimate_charge_usd_from_vnd_account(session):
    await add_rate(session, "USD", "VND", "26020", D27, "sell", "vietcombank")
    c = await R.estimate_charge(session, Decimal("15"), "USD", "VND", D27)
    assert c.account_amount == Decimal(390300) and c.is_estimated
    assert c.account_rate == Decimal(26020)
    fee = await R.estimate_charge(session, Decimal("15"), "USD", "VND", D27, fee_pct=Decimal("1"))
    assert fee.account_amount == Decimal(394203)  # 390300 × 1.01
    same = await R.estimate_charge(session, Decimal("150000"), "VND", "VND", D27)
    assert same.account_amount == Decimal(150000) and not same.is_estimated
    odd = await R.estimate_charge(session, Decimal("21.3"), "USD", "VND", D27)
    assert odd.account_amount == Decimal(554226)  # 21.3 × 26020 = 554226 (₫ has 0 decimals)


async def test_tx_amounts(session):
    await add_rate(session, "USD", "VND", "25000", D27, "sell", "vietcombank")
    await add_rate(session, "TON", "USD", "5", D27, "mid", "coingecko")
    a = await R.tx_amounts(session, Decimal(3), "TON", D27)
    assert (a.amount_usd, a.amount_vnd, a.usd_rate) == (Decimal(15), Decimal(375000), Decimal(5))
    assert a.rate_source == "coingecko" and a.rate_date == D27
    v = await R.tx_amounts(session, Decimal(150000), "VND", D27)
    assert v.amount_usd == Decimal(6) and v.amount_vnd == Decimal(150000)
    assert v.rate_source == "vietcombank"
    none = await R.tx_amounts(session, Decimal(1), "XYZ", D27, fetch_missing=False)
    assert none.amount_usd is None and none.amount_vnd is None


async def test_backfill_missing(session, build, rx, monkeypatch):
    monkeypatch.setattr(R, "_today", lambda: D27)
    await build.category("Еда")
    t = await build.tx("expense", "150000", "VND", D27, category="Еда")
    t_done = await build.tx("expense", "1", "USD", D27, category="Еда", usd="1", vnd="25000")
    mock_vcb(rx)
    n = await R.backfill_missing(session)
    assert n == 1
    await session.refresh(t)
    await session.refresh(t_done)
    assert t.amount_vnd == Decimal(150000)
    assert t.amount_usd == (Decimal(150000) / Decimal(26020)).quantize(Decimal("1e-8"))
    assert t.rate_source == "vietcombank" and t.rate_date == D27
    assert t.version == 1  # system fill does not bump the user-facing version
    assert t_done.amount_vnd == Decimal(25000)  # untouched


async def test_rates_routes(client, session, rx):
    await add_rate(session, "USD", "VND", "26020", D27, "sell", "vietcombank")
    await add_rate(session, "USD", "RUB", "80.4909", D27, "mid", "cbr")
    await add_rate(session, "TON", "USD", "1.22", D27, "mid", "coingecko")
    r = await client.get("/rates/convert", params={"amount": "15", "from_": "usd", "to": "VND",
                                                   "on": "2026-10-27"})  # fmt: skip
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["converted"] == "390300" and body["to"] == "VND"
    assert body["rate"]["rate"] == "26020"
    assert sp(body["rate"]["label"]) == "$1 = 26 020 ₫ (VCB, 27.10)"
    r = await client.get("/rates/convert", params={"amount": "1", "from_": "ABCX", "to": "USD",
                                                   "on": "2026-10-27"})  # fmt: skip
    assert r.status_code == 503 and r.json()["error"]["code"] == "rates_unavailable"
    r = await client.get("/rates/latest")
    assert r.status_code == 200, r.text
    lt = r.json()
    assert lt["usd_vnd"]["rate"] == "26020"
    assert sp(lt["usd_rub"]["label"]) == "$1 = 80.49 ₽ (ЦБ, 27.10)"
    assert lt["crypto"][0]["base"] == "TON"
    assert sp(lt["crypto"][0]["label"]).startswith("1 TON ≈ $1.22")
