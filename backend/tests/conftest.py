"""Shared pytest fixtures. docs/ARCH.md §12.

DB: TEST_DATABASE_URL, default postgresql+asyncpg://pf:pf@127.0.0.1:5432/pf_main_test.
The database is created if the pf role may create DBs; otherwise create it once:
  sudo -u postgres createdb -O pf pf_main_test
Schema is created from ORM metadata (fast); Alembic migrations are tested separately.
Each test runs inside a transaction that is rolled back (route commits become savepoints).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

OWNER_TG_ID = 123456789

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef0123456789")
os.environ.setdefault("BOT_SERVICE_TOKEN", "test-bot-token-0123456789abcdef")
os.environ.setdefault("AI_BASE_URL", "http://127.0.0.1:9/v1")  # never a real LLM in tests
os.environ.setdefault("AI_API_KEY", "test")
os.environ.setdefault("AI_MODEL", "test-model")
# The app defaults to "nobody"; tests act as the bound owner account.
os.environ.setdefault("TELEGRAM_ALLOWED_IDS", str(OWNER_TG_ID))

from app.core.principal import Principal  # noqa: E402
from app.models import Account, AccountAsset, Base, User, UserSettings  # noqa: E402

#: Accounts the tests use on top of the neutral default seed (accounts.seed): a second and a
#: third multi-asset wallet and a RUB card that is default for ₽.
#: (name, kind, currency|None, default_for, icon, aliases)
TEST_EXTRA_ACCOUNTS: list[tuple[str, str, str | None, list[str], str, list[str]]] = [
    ("Копилка", "crypto_wallet", None, [], "PiggyBank", ["копилка"]),
    ("Сейф", "crypto_wallet", None, [], "Nfc", ["сейф"]),
    ("Карта RUB", "card", "RUB", ["RUB"], "CreditCard",
     ["карта руб", "рублёвая карта", "рублевая карта", "руб карта"]),
]


async def seed_test_accounts(session: AsyncSession, user_id: int) -> None:
    """Idempotent: adds TEST_EXTRA_ACCOUNTS after the default accounts (sort order 5…)."""
    from sqlalchemy import func, select

    existing = {n.lower() for n in await session.scalars(
        select(func.lower(Account.name)).where(Account.user_id == user_id))}
    order = len(existing)
    for name, kind, currency, defaults, icon, aliases in TEST_EXTRA_ACCOUNTS:
        if name.lower() in existing:
            continue
        order += 1
        acc = Account(user_id=user_id, name=name, kind=kind, currency=currency,
                      is_multi_asset=currency is None, aliases=aliases,
                      default_for_currencies=defaults, icon=icon, sort_order=order, version=1)
        session.add(acc)
        await session.flush()
        if currency:
            session.add(AccountAsset(user_id=user_id, account_id=acc.id, asset=currency))
    await session.flush()

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://pf:pf@127.0.0.1:5432/pf_main_test"
)


async def _ensure_database(url: str) -> None:
    u = make_url(url)
    admin = create_async_engine(u.set(database="postgres"), poolclass=NullPool,
                                isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": u.database}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{u.database}"'))
    except Exception as e:  # pragma: no cover - environment specific
        raise RuntimeError(
            f"Cannot create test DB {u.database!r}: {e}. Run: "
            f"sudo -u postgres createdb -O pf {u.database}"
        ) from e
    finally:
        await admin.dispose()


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    await _ensure_database(TEST_DATABASE_URL)
    eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    from app import db as app_db

    app_db.set_engine(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Session bound to an outer transaction rolled back after the test."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        s = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield s
        finally:
            await s.close()
            if outer.is_active:
                await outer.rollback()


@pytest.fixture
async def user(session: AsyncSession) -> User:
    """Owner user + settings (Telegram id bound) + default seeds when implemented."""
    u = User(username="owner")
    session.add(u)
    await session.flush()
    session.add(UserSettings(user_id=u.id, telegram_user_id=OWNER_TG_ID))
    await session.flush()
    for mod, fn in (
        ("app.services.categories.seed", "seed_defaults"),
        ("app.services.accounts.seed", "seed_default_accounts"),
    ):
        seed = getattr(__import__(mod, fromlist=[fn]), fn)
        await seed(session, u.id)
    await seed_test_accounts(session, u.id)
    await session.flush()
    return u


@pytest.fixture
def principal(user: User) -> Principal:
    return Principal(user_id=user.id, kind="session", source="web")


@pytest.fixture
def make_principal(user: User):
    """make_principal('telegram') → bot principal; 'desktop' → device principal."""

    def _make(source: str = "web") -> Principal:
        kind = {"web": "session", "desktop": "device", "telegram": "bot"}[source]
        return Principal(
            user_id=user.id,
            kind=kind,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
            telegram_user_id=OWNER_TG_ID if source == "telegram" else None,
        )

    return _make


@pytest.fixture
async def client(session: AsyncSession, principal: Principal) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client with DB session and principal overridden (auth bypassed).
    auth tests that need real auth should use ``raw_client``."""
    from app.api.deps_base import get_principal
    from app.db import get_session
    from app.main import app

    async def _session() -> AsyncIterator[AsyncSession]:
        yield session

    async def _principal() -> Principal:
        return principal

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = _principal
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def raw_client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client with only the DB session overridden (real auth/CSRF)."""
    from app.db import get_session
    from app.main import app

    async def _session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c
    app.dependency_overrides.clear()
