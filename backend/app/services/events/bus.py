"""Change events over PostgreSQL LISTEN/NOTIFY. docs/ARCH.md §8.

* ``publish`` runs ``SELECT pg_notify('pf_events', payload)`` inside the caller's transaction:
  PostgreSQL delivers it only on COMMIT (rolled back → nothing is sent) and folds identical
  payloads of one transaction into one, so calling it many times is cheap. The payload is
  deterministic (no timestamp) for that folding; the listener stamps ``ts`` on receipt.
* Every API process keeps ONE dedicated asyncpg connection with ``LISTEN pf_events`` and fans
  events out to per-user bounded queues (SSE subscribers). Lost connection → reconnect with
  backoff 1→30 s, then a «resync» event (all entities) so clients refetch what they missed.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Iterable
from typing import Any

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import utcnow
from app.schemas.events import ChangeEvent

log = logging.getLogger("pocket.events")

CHANNEL = "pf_events"
ENTITIES: tuple[str, ...] = (
    "transactions",
    "accounts",
    "categories",
    "merchants",
    "settings",
    "recurring",
    "stats",
    "debts",
)
QUEUE_SIZE = 100
CONNECT_WAIT_SECONDS = 5.0


def normalize_entities(entities: Iterable[str]) -> list[str]:
    """Known entities only, ordered; 'transactions' implies 'stats'."""
    s = {e for e in entities if e in ENTITIES}
    if "transactions" in s:
        s.add("stats")
    return [e for e in ENTITIES if e in s]


def encode(
    user_id: int, entities: list[str], action_id: uuid.UUID | str | None, source: str | None
) -> str:
    return json.dumps(
        {"u": user_id, "e": entities, "a": str(action_id) if action_id else None, "s": source},
        separators=(",", ":"),
        sort_keys=True,
    )


def decode(payload: str) -> ChangeEvent | None:
    try:
        d = json.loads(payload)
        ents = normalize_entities(d.get("e") or [])
        if not ents:
            return None
        return ChangeEvent(
            user_id=int(d["u"]),
            entities=ents,  # type: ignore[arg-type]
            action_id=uuid.UUID(d["a"]) if d.get("a") else None,
            source=d.get("s"),
            ts=utcnow(),
        )
    except (ValueError, KeyError, TypeError) as e:
        # the payload is not logged (it may carry user data): its size and hash only
        digest = hashlib.sha256(str(payload).encode("utf-8", "surrogatepass")).hexdigest()[:8]
        log.warning("bad %s payload <len=%s sha256=%s>: %s", CHANNEL, len(str(payload)), digest,
                    type(e).__name__)
        return None


async def publish(
    session: AsyncSession,
    user_id: int,
    entities: Iterable[str],
    *,
    action_id: uuid.UUID | None = None,
    source: str | None = None,
) -> None:
    """``SELECT pg_notify('pf_events', :json)`` inside the current transaction (delivered on
    commit). 'transactions' implies 'stats'. Safe to call many times per transaction."""
    ents = normalize_entities(entities)
    if not ents:
        return
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": CHANNEL, "payload": encode(user_id, ents, action_id, source)},
    )


# ------------------------------------------------------------------------ listening


class Subscription:
    """Per-client bounded queue of ChangeEvent (drops the oldest on overflow)."""

    def __init__(self, hub: _Hub, user_id: int, maxsize: int = QUEUE_SIZE) -> None:
        self.hub = hub
        self.user_id = user_id
        self.queue: asyncio.Queue[ChangeEvent | None] = asyncio.Queue(maxsize)
        self.closed = False

    def put(self, ev: ChangeEvent | None) -> None:
        if self.closed:
            return
        while True:
            try:
                self.queue.put_nowait(ev)
                return
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    self.queue.get_nowait()

    async def get(self) -> ChangeEvent | None:
        """Next event; None when the subscription/listener was closed."""
        if self.closed and self.queue.empty():
            return None
        return await self.queue.get()

    def __aiter__(self) -> AsyncIterator[ChangeEvent]:
        return self

    async def __anext__(self) -> ChangeEvent:
        ev = await self.get()
        if ev is None:
            raise StopAsyncIteration
        return ev

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.hub.remove(self)
            self.put_final()

    def put_final(self) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(None)

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()


class _Hub:
    def __init__(self) -> None:
        self.subs: dict[int, set[Subscription]] = {}
        self.conn: asyncpg.Connection | None = None
        self.task: asyncio.Task[None] | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.connected = asyncio.Event()
        self.reconnects = 0

    # subscribers ------------------------------------------------------------
    def add(self, user_id: int) -> Subscription:
        sub = Subscription(self, user_id)
        self.subs.setdefault(user_id, set()).add(sub)
        return sub

    def remove(self, sub: Subscription) -> None:
        group = self.subs.get(sub.user_id)
        if group is not None:
            group.discard(sub)
            if not group:
                self.subs.pop(sub.user_id, None)

    def dispatch(self, ev: ChangeEvent) -> None:
        for sub in list(self.subs.get(ev.user_id, ())):
            sub.put(ev)

    def broadcast_resync(self) -> None:
        for uid, group in list(self.subs.items()):
            ev = ChangeEvent(user_id=uid, entities=list(ENTITIES), ts=utcnow())  # type: ignore[arg-type]
            for sub in list(group):
                sub.put(ev)

    # connection -------------------------------------------------------------
    def _on_notify(self, conn: Any, pid: int, channel: str, payload: str) -> None:
        ev = decode(payload)
        if ev is not None:
            self.dispatch(ev)

    async def _run(self) -> None:
        backoff = 1.0
        first = True
        while True:
            lost = asyncio.Event()
            try:
                conn = await asyncpg.connect(_dsn(), timeout=10)
                conn.add_termination_listener(lambda _c, ev=lost: ev.set())
                await conn.add_listener(CHANNEL, self._on_notify)
                self.conn = conn
                self.connected.set()
                backoff = 1.0
                if not first:
                    self.reconnects += 1
                    log.info("events listener reconnected")
                    self.broadcast_resync()
                first = False
                while not lost.is_set() and not conn.is_closed():
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(lost.wait(), timeout=15)
                    if not lost.is_set() and not conn.is_closed():
                        await conn.execute("SELECT 1")  # detect half-open connections
            except asyncio.CancelledError:
                raise
            except Exception as e:  # connection refused, dropped, …
                log.warning("events listener connection problem: %s", e)
            finally:
                self.connected.clear()
                c, self.conn = self.conn, None
                if c is not None and not c.is_closed():
                    with contextlib.suppress(Exception):
                        await c.close(timeout=2)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        if self.task is not None and not self.task.done() and self.loop is loop:
            return
        self.loop = loop
        self.connected = asyncio.Event()
        self.task = loop.create_task(self._run(), name="pf-events-listener")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.connected.wait(), CONNECT_WAIT_SECONDS)

    async def stop(self) -> None:
        t, self.task = self.task, None
        if t is not None and not t.done():
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        for group in list(self.subs.values()):
            for sub in list(group):
                sub.closed = True
                sub.put_final()
        self.subs.clear()


def _dsn() -> str:
    from app.db import get_engine

    url = get_engine().url.set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


_hub = _Hub()


def hub() -> _Hub:
    return _hub


async def start_listener() -> None:
    """Open one dedicated asyncpg connection with LISTEN pf_events; fan out to subscribers.
    Idempotent; never raises (keeps retrying in the background)."""
    await _hub.start()


async def stop_listener() -> None:
    await _hub.stop()


def subscribe(user_id: int) -> Subscription:
    """Async iterator of ChangeEvent for this user (bounded queue; drops oldest on overflow).
    Call ``close()`` (or use ``async with``) when done."""
    return _hub.add(user_id)
