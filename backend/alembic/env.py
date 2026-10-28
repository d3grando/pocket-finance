"""Alembic async env. Target metadata = app.models.Base.metadata.

DB URL: ``-x url=...`` > ``sqlalchemy.url`` set on the Config (tests) > DATABASE_URL (app.config).
A caller may also pass an open sync connection via ``config.attributes["connection"]``."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.config import get_settings
from app.models import Base

target_metadata = Base.metadata
config = context.config


def _url() -> str:
    x = context.get_x_argument(as_dictionary=True)
    return x.get("url") or config.get_main_option("sqlalchemy.url") or get_settings().database_url


def _configure(**kw) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        target_metadata=target_metadata, compare_type=True, compare_server_default=False, **kw
    )


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:  # type: ignore[no-untyped-def]
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
elif config.attributes.get("connection") is not None:
    _do_run(config.attributes["connection"])
else:
    asyncio.run(run_migrations_online())
