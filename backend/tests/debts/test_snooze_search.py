"""«Напомнить позже» keeps its promise with the default N = 30, and the global
search titles debt operations by what they are, never «Расход · Коля»."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.timeutil import local_today
from app.models import NotificationOutbox, Transaction, UserSettings
from app.services.debts.reminders import enqueue_debt_reminders
from app.services.stats import queries as Q
from tests.debts.conftest import op, open_bank

D = Decimal
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _at(day, hour: int = 12) -> datetime:  # type: ignore[no-untyped-def]
    return datetime.combine(day, time(hour), tzinfo=TZ).astimezone(UTC)


async def test_snooze_reminds_after_7_days_with_default_n(api, session, user):
    await open_bank(api, session, user.id)
    today = local_today("Asia/Ho_Chi_Minh")
    d = (await op(api, op="lend", person_name="Старый", amount=500000,
                  occurred_on=(today - timedelta(days=40)).isoformat()))["debt"]
    st = await session.get(UserSettings, user.id)
    st.debt_reminders_enabled = True  # N stays the default 30
    await session.flush()
    assert st.debt_reminder_days == 30
    assert await enqueue_debt_reminders(session, _at(today)) == 1
    r = await api.post(f"/debts/{d['id']}/snooze", json={"days": 7})
    assert r.status_code == 200, r.text
    # nothing while snoozed …
    for k in (1, 3, 6):
        assert await enqueue_debt_reminders(session, _at(today + timedelta(days=k))) == 0, k
    # … the promised reminder on day 7 (not on day 30) …
    assert await enqueue_debt_reminders(session, _at(today + timedelta(days=7))) == 1
    keys = [r.dedupe_key for r in await session.scalars(
        select(NotificationOutbox).where(NotificationOutbox.user_id == user.id)
        .order_by(NotificationOutbox.id))]
    assert keys[-1] == f"debt_reminder:{d['id']}:{today + timedelta(days=7)}"
    # … then back to once every N days
    for k in (8, 14, 36):
        assert await enqueue_debt_reminders(session, _at(today + timedelta(days=k))) == 0, k
    assert await enqueue_debt_reminders(session, _at(today + timedelta(days=37))) == 1
    # a snooze that has not ended yet still wins over the N-day repeat
    r = await api.post(f"/debts/{d['id']}/snooze", json={"days": 90})
    assert r.status_code == 200
    assert await enqueue_debt_reminders(session, _at(today + timedelta(days=67))) == 0


async def test_search_titles_of_debt_operations(api, session, user):
    await open_bank(api, session, user.id)
    lend = await op(api, op="lend", person_name="Зина", amount=300000)
    did = lend["debt"]["id"]
    await op(api, op="repay_in", debt_id=did, amount=100000, description="за ремонт")
    await op(api, op="forgive", debt_id=did, amount=None)
    await op(api, op="borrow", person_name="Зина", amount=50000)
    await op(api, op="repay_out", debt_id=did, amount=None)
    hits = (await api.get("/search", params={"q": "Зина"})).json()["hits"]
    titles = {h["title"] for h in hits if h["type"] == "transaction"}
    assert titles == {
        "Дал в долг: Зина", "Возврат долга: Зина · за ремонт", "Списан долг: Зина",
        "Занял: Зина", "Вернул долг: Зина",
    }, titles
    # renamed person → the current name
    ver = (await api.get(f"/debts/{did}")).json()["debt"]["version"]
    r = await api.patch(f"/debts/{did}", json={"version": ver, "name": "Зинаида"})
    assert r.status_code == 200, r.text
    hits = (await api.get("/search", params={"q": "Зина"})).json()["hits"]
    assert "Дал в долг: Зинаида" in {h["title"] for h in hits}
    # other (non-debt) kinds are not «Расход» either
    t = Transaction(kind="transfer", counterparty="Ваня", currency="VND", amount=D(1))
    assert Q.tx_title(None, None, t) == "Перевод · Ваня"
    t = Transaction(kind="exchange", currency="USDT", amount=D(1))
    assert Q.tx_title(None, None, t) == "Обмен"
    t = Transaction(kind="expense", counterparty="@vasya", currency="VND", amount=D(1))
    assert Q.tx_title(None, None, t) == "Расход · @vasya"


async def test_search_finds_debt_operations_by_current_name(api, session, user):
    """A debt operation keeps the counterparty of recording time; the search
    also matches the person's current name / alias / @handle, so a rename does not hide them."""
    await open_bank(api, session, user.id)
    lend = await op(api, op="lend", person_name="Гоша", amount=300000)
    did = lend["debt"]["id"]
    await op(api, op="repay_in", debt_id=did, amount=100000)
    ver = (await api.get(f"/debts/{did}")).json()["debt"]["version"]
    r = await api.patch(f"/debts/{did}", json={"version": ver, "name": "Георгий",
                                               "aliases": ["жора"]})
    assert r.status_code == 200, r.text
    for q in ("Георгий", "жора", "Гоша"):
        hits = (await api.get("/search", params={"q": q})).json()["hits"]
        titles = {h["title"] for h in hits if h["type"] == "transaction"}
        assert titles == {"Дал в долг: Георгий", "Возврат долга: Георгий"}, (q, titles)
        page = (await api.get("/transactions", params={"q": q})).json()
        assert page["total"] == 2, (q, page["total"])
    # a non-debt operation with the same word in another field is not pulled in by the person
    hits = (await api.get("/search", params={"q": "Георгий"})).json()["hits"]
    assert all(h["title"].endswith("Георгий") for h in hits if h["type"] == "transaction")
