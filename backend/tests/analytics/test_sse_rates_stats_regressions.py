"""Regressions: SSE stops after the session/device is revoked; rates for past days
(crypto yesterday → history, nearest fallback; fiat X → VCB/CBR of that day); stats: a
future-dated op is in the series, refund-only categories are not negative slices, an
income-only day says «Расходов нет»."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import uvicorn
from sqlalchemy import delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.events import principal_alive
from app.core.principal import Principal
from app.models import DeviceToken, Session, User
from app.schemas.common import PeriodSpec
from app.schemas.intents import ParseContext, StatsIntent
from app.services.events import bus
from app.services.rates import providers as P
from app.services.rates import service as R
from app.services.stats import service as S
from tests.analytics.conftest import NOW, add_rate, sp

D27 = date(2026, 10, 27)
CTX = ParseContext(now=NOW, today=D27)


# --------------------------------------------------------------------------------- SSE


@pytest.fixture
async def auth_rows(engine):  # type: ignore[no-untyped-def]
    """A committed user with a live cookie session and a live device token."""
    now = datetime.now(UTC)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        u = User(username=f"sse-{uuid.uuid4().hex[:10]}")
        s.add(u)
        await s.flush()
        sess = Session(
            user_id=u.id, token_hash=uuid.uuid4().bytes, csrf_hash=b"x", last_seen_at=now,
            expires_at=now + timedelta(days=90),
        )  # fmt: skip
        dev = DeviceToken(
            user_id=u.id, name="PC", platform="windows", token_hash=uuid.uuid4().bytes
        )
        s.add_all([sess, dev])
        await s.commit()
        ids = (u.id, sess.id, dev.id)
    yield ids
    async with AsyncSession(engine) as s:
        await s.execute(delete(User).where(User.id == ids[0]))
        await s.commit()


@pytest.fixture
async def sse_server(engine, monkeypatch):  # type: ignore[no-untyped-def]
    from app.api.deps_base import get_principal
    from app.api.routes import events as events_route
    from app.main import app

    monkeypatch.setattr(events_route, "PING_SECONDS", 0.3)
    monkeypatch.setattr(events_route, "REVALIDATE_SECONDS", 0.3)
    holder: dict[str, Principal] = {}
    app.dependency_overrides[get_principal] = lambda: holder["p"]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/api/v1", holder
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, 5)
        app.dependency_overrides.pop(get_principal, None)


async def _publish(engine, user_id: int) -> None:  # type: ignore[no-untyped-def]
    async with AsyncSession(engine) as s:
        await bus.publish(s, user_id, ["transactions"], source="desktop")
        await s.commit()


async def _listen(base: str, on_hello) -> list[str]:  # type: ignore[no-untyped-def]
    """Read the stream until the server closes it (≤ 8 s); returns event names."""
    names: list[str] = []
    async with httpx.AsyncClient(timeout=10) as c:
        async with c.stream("GET", f"{base}/events/stream") as r:
            assert r.status_code == 200

            async def read() -> None:
                async for line in r.aiter_lines():
                    if line.startswith("event:"):
                        name = line.split(":", 1)[1].strip()
                        names.append(name)
                        if name == "hello":
                            await on_hello()

            await asyncio.wait_for(read(), 8)
    return names


@pytest.mark.parametrize("kind", ["session", "device"])
async def test_sse_stream_closes_after_revoke(engine, sse_server, auth_rows, rx, kind):
    """t2_edges G/H, t3: after logout / DELETE /devices/{id} the open stream gets no more
    change events and is closed (the client reconnects → 401)."""
    rx.route(host="127.0.0.1").pass_through()
    base, holder = sse_server
    uid, sid, did = auth_rows
    holder["p"] = (
        Principal(user_id=uid, kind="session", source="web", session_id=sid)
        if kind == "session"
        else Principal(user_id=uid, kind="device", source="desktop", device_id=did)
    )
    await bus.start_listener()
    model, pk = (Session, sid) if kind == "session" else (DeviceToken, did)

    async def revoke_then_change() -> None:
        async with AsyncSession(engine) as s:
            await s.execute(update(model).where(model.id == pk).values(revoked_at=func.now()))
            await s.commit()
        await _publish(engine, uid)

    names = await _listen(base, revoke_then_change)
    assert names[0] == "hello"
    assert "change" not in names  # nothing leaks after the revocation
    # an idle stream of a revoked credential is closed too (re-check every REVALIDATE_SECONDS)
    assert await principal_alive(holder["p"]) is False


async def test_sse_live_stream_still_gets_changes(engine, sse_server, auth_rows, rx):
    rx.route(host="127.0.0.1").pass_through()
    base, holder = sse_server
    uid, sid, _ = auth_rows
    holder["p"] = Principal(user_id=uid, kind="session", source="web", session_id=sid)
    await bus.start_listener()
    names: list[str] = []

    async def read(r: httpx.Response) -> None:
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                names.append(line.split(":", 1)[1].strip())
                if names[-1] == "hello":
                    await _publish(engine, uid)
                if names[-1] == "change":
                    return

    async with httpx.AsyncClient(timeout=10) as c:
        async with c.stream("GET", f"{base}/events/stream") as r:
            await asyncio.wait_for(read(r), 8)
    assert names[0] == "hello" and names[-1] == "change"  # pings/re-checks keep it open


async def test_principal_alive_rules(engine, auth_rows):
    uid, sid, did = auth_rows
    alive = Principal(user_id=uid, kind="session", source="web", session_id=sid)
    assert await principal_alive(alive) is True
    assert await principal_alive(Principal(user_id=uid, kind="session", source="web",
                                   session_id=uuid.uuid4())) is False  # fmt: skip
    assert await principal_alive(Principal(user_id=uid + 1, kind="device", source="desktop",
                                   device_id=did)) is False  # fmt: skip
    async with AsyncSession(engine) as s:  # expired session
        await s.execute(
            update(Session).where(Session.id == sid).values(expires_at=datetime.now(UTC))
        )
        await s.commit()
    assert await principal_alive(alive) is False
    assert await principal_alive(Principal(user_id=uid, kind="bot", source="telegram")) is True


# ------------------------------------------------------------------------------- rates


def _cg_history(rx, price: str | None, status: int = 200):  # type: ignore[no-untyped-def]
    body = {"market_data": {"current_price": {"usd": float(price)}}} if price else {}
    return rx.get(f"{P.COINGECKO_URL}/coins/the-open-network/history").mock(
        return_value=httpx.Response(status, json=body)
    )


async def test_crypto_yesterday_uses_history_not_a_16_day_old_price(session, rx, monkeypatch):
    """TON rates only for 10-10 (1.4536) and today (1.22) → yesterday's op got +19%."""
    monkeypatch.setattr(R, "_today", lambda: D27)
    await add_rate(session, "TON", "USD", "1.453594006636", date(2026, 10, 10), "mid", "coingecko")
    await add_rate(session, "TON", "USD", "1.22", D27, "mid", "coingecko")
    price = rx.get(f"{P.COINGECKO_URL}/simple/price").respond(500)
    hist = _cg_history(rx, "1.25")
    q = await R.get_rate(session, "TON", "USD", date(2026, 10, 26), kind="mid")
    assert q.rate == Decimal("1.25") and q.rate_date == date(2026, 10, 26) and not q.is_fallback
    assert hist.call_count == 1 and price.call_count == 0


async def test_fallback_prefers_the_nearest_date(session, rx, monkeypatch):
    monkeypatch.setattr(R, "_today", lambda: D27)
    await add_rate(session, "TON", "USD", "1.453594006636", date(2026, 10, 10), "mid", "coingecko")
    await add_rate(session, "TON", "USD", "1.22", D27, "mid", "coingecko")
    _cg_history(rx, None, status=500)  # history down too
    q = await R.get_rate(session, "TON", "USD", date(2026, 10, 26), kind="mid")
    assert q.rate == Decimal("1.22") and q.rate_date == D27 and q.is_fallback
    # a tie (same distance) → the earlier date
    await add_rate(session, "BTC", "USD", "100", date(2026, 10, 20), "mid", "coingecko")
    await add_rate(session, "BTC", "USD", "120", date(2026, 10, 24), "mid", "coingecko")
    q2 = await R.get_rate(session, "BTC", "USD", date(2026, 10, 22), fetch_missing=False)
    assert q2.rate == Decimal("100") and q2.is_fallback is False  # 2 days back: in the window


async def _vcb_day(session, on: date, rows: dict[str, tuple[str, str]]) -> None:  # type: ignore[no-untyped-def]
    for code, (buy, sell) in rows.items():
        b, s_ = Decimal(buy), Decimal(sell)
        await add_rate(session, code, "VND", sell, on, "sell", "vietcombank")
        await add_rate(session, code, "VND", buy, on, "buy", "vietcombank")
        await add_rate(session, code, "VND", str((b + s_) / 2), on, "mid", "vietcombank")


async def test_past_fiat_uses_vietcombank_of_that_day(session):
    """THB on 2026-09-01 got the open.er-api rate of 10-27 silently."""
    sep1 = date(2026, 9, 1)
    await _vcb_day(session, sep1, {"USD": ("25000", "25400"), "THB": ("740", "809.01")})
    await add_rate(session, "USD", "THB", "33.8", D27, "mid", "open_er_api")
    q = await R.get_rate(session, "THB", "USD", sep1, kind="mid", fetch_missing=False)
    assert q.rate_date == sep1 and q.source == "vietcombank" and not q.is_fallback
    assert q.rate == Decimal("774.505") / Decimal("25200")
    v = await R.get_rate(session, "THB", "VND", sep1, kind="sell", fetch_missing=False)
    assert v.rate_date == sep1 and not v.is_fallback
    # THB→USD of that day (VCB mid/mid) × VCB USD/VND sell of that day
    assert abs(v.rate - Decimal("774.505") / Decimal("25200") * Decimal("25400")) < Decimal("1e-9")
    a = await R.tx_amounts(session, Decimal(1000), "THB", sep1, fetch_missing=False)
    assert a.rate_date == sep1 and a.rate_source == "vietcombank" and not a.is_fallback


async def test_past_fiat_cbr_composite_when_vcb_has_no_quote(session):
    sep1 = date(2026, 9, 1)
    await add_rate(session, "KZT", "RUB", "0.16", sep1, "mid", "cbr")
    await add_rate(session, "USD", "RUB", "80", sep1, "mid", "cbr")
    q = await R.get_rate(session, "KZT", "USD", sep1, kind="mid", fetch_missing=False)
    assert q.rate == Decimal("0.002") and q.source == "cbr" and q.rate_date == sep1


async def test_past_fiat_fetches_vietcombank_for_the_day(session, rx, monkeypatch):
    monkeypatch.setattr(R, "_today", lambda: D27)
    sep1 = date(2026, 9, 1)
    body = {"Date": "2026-09-01T00:00:00", "Data": [
        {"currencyCode": "USD", "transfer": "25000", "sell": "25400"},
        {"currencyCode": "THB", "transfer": "740", "sell": "809.01"},
    ]}  # fmt: skip
    vcb = rx.get(P.VCB_URL, params={"date": "2026-09-01"}).respond(200, json=body)
    q = await R.get_rate(session, "THB", "USD", sep1, kind="mid")
    assert vcb.call_count == 1
    assert q.rate_date == sep1 and q.source == "vietcombank" and not q.is_fallback


async def test_backfill_improves_a_later_dated_fallback(session, build, monkeypatch):
    monkeypatch.setattr(R, "_today", lambda: D27)
    await build.category("Еда")
    t = await build.tx("expense", "1", "TON", date(2026, 10, 26), category="Еда", usd="1.22",
                       vnd="31744", rate_date=D27, rate_source="coingecko")  # fmt: skip
    await add_rate(session, "TON", "USD", "1.25", date(2026, 10, 26), "mid", "coingecko")
    await add_rate(session, "USD", "VND", "26000", date(2026, 10, 26), "sell", "vietcombank")
    assert await R.backfill_missing(session) == 1
    await session.refresh(t)
    assert t.rate_date == date(2026, 10, 26) and t.amount_usd == Decimal("1.25")


# ------------------------------------------------------------------------------- stats


async def test_future_dated_op_is_in_the_series(session, build):
    await build.category("Еда")
    await build.tx("expense", "100000", "VND", D27, category="Еда", usd="4", vnd="100000")
    await build.tx("expense", "50000", "VND", D27 + timedelta(days=1), category="Еда",
                   usd="2", vnd="50000")  # fmt: skip
    month = PeriodSpec(preset="this_month")
    s = await S.series(session, build.user_id, month, currency="VND", now=NOW)
    assert s.points[-1].date == D27 + timedelta(days=1)
    total = sum((p.expense for p in s.points), Decimal(0))
    bc = await S.by_category(session, build.user_id, month, currency="VND", now=NOW)
    assert total == bc.total == Decimal(150000)
    summ = await S.summary(session, build.user_id, "VND", now=NOW)
    assert sum((Decimal(x) for x in summ.spent_month.sparkline), Decimal(0)) == Decimal(150000)


async def test_refund_only_category_is_not_a_negative_slice(session, build):
    await build.category("Еда")
    await build.category("Покупки")
    await build.tx("expense", "100000", "VND", D27, category="Еда", usd="4", vnd="100000")
    await build.tx("expense", "30000", "VND", D27, category="Покупки", usd="1.2", vnd="30000",
                   hh=9)  # fmt: skip
    await build.tx("refund", "80000", "VND", D27, category="Покупки", usd="3.2", vnd="80000")
    today = PeriodSpec(preset="today")
    bc = await S.by_category(session, build.user_id, today, currency="VND", now=NOW)
    assert bc.total == Decimal(50000)  # expense − refund
    assert [(i.name, i.amount, i.share) for i in bc.items] == [("Еда", 100000, Decimal("1"))]
    card = await S.answer_stats_intent(
        session, build.user_id, StatsIntent(metric="spent", period=today), CTX, {}
    )
    assert sp(card.headline) == "−50 000 ₫"
    assert [(ln.label, sp(ln.value)) for ln in card.lines] == [
        ("Еда", "100 000 ₫"),
        ("Возвраты", "+50 000 ₫"),
    ]


async def test_income_only_day_says_no_expenses(session, build):
    await build.category("Клиенты", "income")
    day = D27 - timedelta(days=2)
    await build.tx("income", "1000000", "VND", day, category="Клиенты", usd="40", vnd="1000000")
    card = await S.answer_stats_intent(
        session, build.user_id,
        StatsIntent(metric="spent", period=PeriodSpec(preset="custom", date_from=day,
                                                       date_to=day)),
        CTX, {},
    )  # fmt: skip
    assert sp(card.headline) == "0 ₫" and card.lines == [] and card.text == "Расходов нет"
