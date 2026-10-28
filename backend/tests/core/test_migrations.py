"""Alembic: one head, upgrade on an empty DB reproduces the ORM schema exactly, downgrade works.
Uses its own throw-away database (<test db>_mig)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.models import Base
from tests.conftest import TEST_DATABASE_URL

BACKEND = Path(__file__).resolve().parents[2]


def _cfg(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_single_head() -> None:
    script = ScriptDirectory.from_config(_cfg(TEST_DATABASE_URL))
    assert script.get_heads() == ["0005"]
    assert [r.revision for r in script.walk_revisions()] == [
        "0005", "0004", "0003", "0002", "0001"
    ]


async def _diff(url: str) -> list:
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            def run(sync_conn):  # type: ignore[no-untyped-def]
                mc = MigrationContext.configure(sync_conn, opts={"compare_type": True})
                return compare_metadata(mc, Base.metadata)

            return await conn.run_sync(run)
    finally:
        await eng.dispose()


async def _tables(url: str) -> set[str]:
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    finally:
        await eng.dispose()


async def _drop_db(admin: AsyncEngine, db: str) -> None:
    """DROP DATABASE that tolerates an autovacuum worker in it: WITH (FORCE) refuses to terminate
    another role's backend (the superuser's autovacuum) without pg_signal_backend, while a plain
    DROP cancels autovacuum itself; a short retry covers our own engine still disconnecting."""
    plain = f'DROP DATABASE IF EXISTS "{db}"'
    stmts = [f"{plain} WITH (FORCE)"] + [plain] * 10
    for i, stmt in enumerate(stmts):
        try:
            async with admin.connect() as c:
                await c.execute(text(stmt))
            return
        except DBAPIError:
            if i == len(stmts) - 1:
                raise
            await asyncio.sleep(0.5)


async def test_upgrade_matches_models_and_downgrade() -> None:
    base = make_url(TEST_DATABASE_URL)
    db = f"{base.database}_mig"
    admin = create_async_engine(
        base.set(database="postgres"), poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    await _drop_db(admin, db)
    async with admin.connect() as c:
        await c.execute(text(f'CREATE DATABASE "{db}"'))
    try:
        url = base.set(database=db).render_as_string(hide_password=False)
        cfg = _cfg(url)
        await asyncio.to_thread(command.upgrade, cfg, "head")
        diffs = await _diff(url)
        assert diffs == [], diffs
        tables = await _tables(url)
        assert {"transactions", "postings", "action_log", "alembic_version"} <= tables
        await asyncio.to_thread(command.downgrade, cfg, "base")
        assert await _tables(url) == {"alembic_version"}
        await asyncio.to_thread(command.upgrade, cfg, "head")
    finally:
        await _drop_db(admin, db)
        await admin.dispose()
