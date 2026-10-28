"""Events: pg_notify delivered on COMMIT only (cross-connection, as between processes), fan-out
per user, and the SSE stream end-to-end over a real uvicorn server."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

import httpx
import pytest
import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal
from app.services.events import bus


async def _publish_committed(engine, user_id: int, entities, **kw) -> None:  # type: ignore[no-untyped-def]
    async with AsyncSession(engine) as s:
        await bus.publish(s, user_id, entities, **kw)
        await s.commit()


async def _next(sub: bus.Subscription, wait: float = 5.0):  # type: ignore[no-untyped-def]
    return await asyncio.wait_for(sub.get(), wait)


async def _nothing(sub: bus.Subscription, wait: float = 0.4) -> bool:
    try:
        await asyncio.wait_for(sub.get(), wait)
    except TimeoutError:
        return True
    return False


def test_encode_decode_and_entities():
    assert bus.normalize_entities(["transactions", "bogus", "accounts"]) == [
        "transactions", "accounts", "stats",
    ]  # fmt: skip
    p = bus.encode(5, ["accounts"], None, "web")
    ev = bus.decode(p)
    assert ev.user_id == 5 and ev.entities == ["accounts"] and ev.source == "web"
    assert bus.decode("not json") is None and bus.decode('{"u": 1, "e": []}') is None


async def test_notify_delivered_on_commit_only(engine, session):
    await bus.start_listener()
    assert bus.hub().connected.is_set()
    uid = 900001
    sub = bus.subscribe(uid)
    other = bus.subscribe(uid + 1)
    try:
        aid = uuid.uuid4()
        await _publish_committed(engine, uid, ["transactions"], action_id=aid, source="telegram")
        ev = await _next(sub)
        assert ev.entities == ["transactions", "stats"]
        assert ev.action_id == aid and ev.source == "telegram" and ev.ts.tzinfo is not None
        assert await _nothing(other)  # another user's stream is not touched

        # rolled back → nothing
        async with AsyncSession(engine) as s:
            await bus.publish(s, uid, ["accounts"])
            await s.rollback()
        assert await _nothing(sub)

        # many identical publishes in one transaction → folded into one event
        async with AsyncSession(engine) as s:
            for _ in range(5):
                await bus.publish(s, uid, ["categories"])
            await s.commit()
        ev = await _next(sub)
        assert ev.entities == ["categories"]
        assert await _nothing(sub)

        # the test-session (never committed) publishes nothing
        await bus.publish(session, uid, ["merchants"])
        assert await _nothing(sub)
    finally:
        sub.close()
        other.close()


async def test_queue_overflow_drops_oldest():
    h = bus._Hub()
    sub = h.add(1)
    sub.queue = asyncio.Queue(maxsize=3)
    for i in range(5):
        sub.put(bus.decode(bus.encode(1, ["stats"], None, f"s{i}")))
    got = [sub.queue.get_nowait().source for _ in range(3)]
    assert got == ["s2", "s3", "s4"]
    sub.close()
    assert 1 not in h.subs


async def test_resync_after_reconnect(engine):
    await bus.start_listener()
    h = bus.hub()
    uid = 900003
    sub = bus.subscribe(uid)
    try:
        before = h.reconnects
        await h.conn.close()  # simulate a dropped LISTEN connection
        ev = await _next(sub, wait=10)
        assert set(ev.entities) == set(bus.ENTITIES)  # «refetch everything»
        assert h.reconnects == before + 1
        await _publish_committed(engine, uid, ["recurring"])
        ev = await _next(sub)
        assert ev.entities == ["recurring"]
    finally:
        sub.close()


@pytest.fixture
async def live_server(engine, monkeypatch):
    """The real app on 127.0.0.1:<random> (lifespan off: it would dispose the test engine)."""
    from app.api.deps_base import get_principal
    from app.api.routes import events as events_route
    from app.main import app

    monkeypatch.setattr(events_route, "PING_SECONDS", 0.5)
    principal = Principal(user_id=900002, kind="device", source="desktop")
    app.dependency_overrides[get_principal] = lambda: principal
    config = uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/api/v1", principal
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, 5)
        app.dependency_overrides.pop(get_principal, None)


async def test_sse_stream_end_to_end(engine, live_server, rx):
    base, principal = live_server
    rx.route(host="127.0.0.1").pass_through()  # local server only; the internet stays blocked
    events: list[tuple[str, dict]] = []
    async with httpx.AsyncClient(timeout=10) as c:
        async with c.stream("GET", f"{base}/events/stream") as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            assert r.headers["cache-control"] == "no-cache"
            assert r.headers["x-accel-buffering"] == "no"
            name = None
            published = False
            async for line in r.aiter_lines():
                if line.startswith("event:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("data:") and name:
                    events.append((name, json.loads(line.split(":", 1)[1].strip())))
                    if name == "hello" and not published:
                        published = True
                        await _publish_committed(
                            engine, principal.user_id, ["transactions"], source="telegram"
                        )
                        await _publish_committed(engine, principal.user_id + 1, ["accounts"])
                    names = [e[0] for e in events]
                    if "change" in names and "ping" in names:
                        break
    names = [e[0] for e in events]
    assert names[0] == "hello"
    assert set(events[0][1]) == {"server_time", "version"}
    change = next(d for n, d in events if n == "change")
    assert change["entities"] == ["transactions", "stats"] and change["source"] == "telegram"
    assert "user_id" not in change and change["ts"]
    assert all(d.get("entities") != ["accounts"] for n, d in events if n == "change")
    assert next(d for n, d in events if n == "ping") == {}
    # the subscriber is removed after the client disconnects
    for _ in range(50):
        if principal.user_id not in bus.hub().subs:
            break
        await asyncio.sleep(0.1)
    assert principal.user_id not in bus.hub().subs
