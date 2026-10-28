"""Worker loop. Single instance via pg_try_advisory_lock(778899).
Tick every WORKER_TICK_SECONDS; jobs in scheduler/jobs.py; table in docs/ARCH.md §9.

The advisory lock lives on a dedicated connection held for the whole life of the worker; a
second copy waits (retrying every tick) and takes over if the first one dies.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.config import get_settings
from app.core.timeutil import utcnow
from app.services.scheduler import jobs

log = logging.getLogger("pocket.worker")

ADVISORY_LOCK_ID = 778899

JobFn = Callable[[AsyncSession, datetime], Awaitable[None]]


@dataclass
class Job:
    name: str
    fn: JobFn
    every: timedelta  # 0 → every tick
    run_at_start: bool = True
    last_run: datetime | None = None
    timeout: float = 120.0

    def due(self, now: datetime) -> bool:
        if self.last_run is None:
            return self.run_at_start or self.every == timedelta(0)
        return now - self.last_run >= self.every


def default_jobs() -> list[Job]:
    return [
        Job("refresh_fiat_rates", jobs.refresh_fiat_rates, timedelta(hours=1)),
        Job("refresh_crypto_rates", jobs.refresh_crypto_rates, timedelta(minutes=15)),
        Job("backfill_missing_rates", jobs.backfill_missing_rates, timedelta(minutes=10)),
        Job("enqueue_reports", jobs.enqueue_reports, timedelta(0)),
        Job("recurring_reminders", jobs.recurring_reminders, timedelta(0)),
        Job("debt_reminders", jobs.debt_reminders, timedelta(0)),
        Job("cleanup", jobs.cleanup, timedelta(hours=1)),
    ]


@dataclass
class Scheduler:
    """Runs due jobs; each job in its own session, failures are logged and isolated."""

    session_factory: Callable[[], AsyncSession]
    jobs: list[Job] = field(default_factory=lambda: default_jobs())

    async def tick(self, now: datetime | None = None) -> list[str]:
        now = now or utcnow()
        ran: list[str] = []
        for job in self.jobs:
            if not job.due(now):
                continue
            job.last_run = now
            try:
                async with self.session_factory() as session:
                    await asyncio.wait_for(job.fn(session, now), timeout=job.timeout)
                ran.append(job.name)
            except Exception:
                log.exception("job %s failed", job.name)
        return ran


async def try_lock(conn: AsyncConnection) -> bool:
    got = await conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_ID})
    await conn.commit()
    return bool(got)


async def run_forever(
    engine: AsyncEngine | None = None, *, stop: asyncio.Event | None = None
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import get_engine

    eng = engine or get_engine()
    sm = async_sessionmaker(eng, expire_on_commit=False)
    tick = max(int(get_settings().worker_tick_seconds), 1)
    sched = Scheduler(session_factory=sm)
    stop = stop or asyncio.Event()
    lock_conn: AsyncConnection | None = None
    waiting_logged = False
    while not stop.is_set():
        try:
            if lock_conn is None:
                lock_conn = await eng.connect()
                if not await try_lock(lock_conn):
                    await lock_conn.close()
                    lock_conn = None
                    if not waiting_logged:
                        log.info("another worker holds the lock; waiting")
                        waiting_logged = True
                else:
                    log.info("worker lock acquired")
                    waiting_logged = False
            else:
                await lock_conn.scalar(text("SELECT 1"))  # still alive (lock still ours)
                await lock_conn.commit()
            if lock_conn is not None:
                await sched.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker loop error")
            if lock_conn is not None:
                with contextlib.suppress(Exception):
                    await lock_conn.close()
                lock_conn = None
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=tick)
    if lock_conn is not None:
        with contextlib.suppress(Exception):
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_ID})
            await lock_conn.close()
