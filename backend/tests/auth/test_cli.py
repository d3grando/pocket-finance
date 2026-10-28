"""Admin CLI: create owner, set password, create test user."""

from __future__ import annotations

import io
from contextlib import asynccontextmanager

from sqlalchemy import select

from app.cli import build_parser, run
from app.config import get_settings
from app.core.security import verify_password
from app.models import Session as WebSession
from app.models import User, UserSettings
from tests.auth.conftest import login


def _factory(session):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


async def _run(session, *argv: str) -> int:
    return await run(build_parser().parse_args(list(argv)), _factory(session))


async def test_create_owner_generates_password(session, capsys):
    code = await _run(session, "create-owner", "--username", "boss")
    assert code == 0
    out = capsys.readouterr().out
    pw = out.strip().splitlines()[-1].strip()
    assert "показывается один раз" in out
    u = (await session.execute(select(User).where(User.username == "boss"))).scalar_one()
    assert u.password_hash.startswith("$argon2id$") and verify_password(u.password_hash, pw)
    st = await session.get(UserSettings, u.id)
    assert st is not None and st.timezone == "Asia/Ho_Chi_Minh"
    # second run refuses to overwrite without --force
    assert await _run(session, "create-owner", "--username", "boss") == 2
    assert (
        await _run(
            session,
            "create-owner",
            "--username",
            "boss",
            "--force",
            "--password",
            "another-long-password",
        )
        == 0
    )
    await session.refresh(u)
    assert verify_password(u.password_hash, "another-long-password")


async def test_create_owner_binds_telegram_when_free(session, capsys):
    assert (
        await _run(session, "create-owner", "--username", "solo", "--password", "solo-password-123")
        == 0
    )
    u = (await session.execute(select(User).where(User.username == "solo"))).scalar_one()
    st = await session.get(UserSettings, u.id)
    assert st.telegram_user_id in get_settings().telegram_allowed_ids


async def test_set_password_revokes_sessions(session, raw_client, user, capsys, monkeypatch):
    assert await _run(session, "set-password", "--password", "first-password-123") == 0
    assert (await login(raw_client, "first-password-123")).status_code == 200
    monkeypatch.setattr("sys.stdin", io.StringIO("second-password-123\n"))
    assert await _run(session, "set-password", "--password-stdin") == 0
    out = capsys.readouterr().out
    assert "Отозвано сессий: 1" in out
    row = (await session.execute(select(WebSession))).scalar_one()
    await session.refresh(row)
    assert row.revoked_at is not None
    assert (await raw_client.get("/auth/me")).status_code == 401
    assert (await login(raw_client, "second-password-123")).status_code == 200


async def test_set_password_policy(session, user, capsys):
    from app.core.errors import AppError

    try:
        await _run(session, "set-password", "--password", "short")
    except AppError as e:
        assert e.status == 422
    else:  # pragma: no cover
        raise AssertionError("short password accepted")


async def test_set_password_unknown_user(session, capsys):
    from app.core.errors import AppError

    try:
        await _run(session, "set-password", "--username", "nobody", "--password", "x" * 14)
    except AppError as e:
        assert e.status == 404
    else:  # pragma: no cover
        raise AssertionError


async def test_create_test_user(session, user, capsys, monkeypatch):
    assert (
        await _run(
            session, "create-test-user", "--username", "tester", "--password", "tester-password-1"
        )
        == 0
    )
    u = (await session.execute(select(User).where(User.username == "tester"))).scalar_one()
    assert verify_password(u.password_hash, "tester-password-1")
    st = await session.get(UserSettings, u.id)
    assert st.telegram_user_id is None  # owner keeps the bot
    monkeypatch.setattr(get_settings(), "app_env", "prod")
    assert await _run(session, "create-test-user", "--username", "t2") == 2


async def test_list_and_logout_all(session, user, capsys):
    await _run(session, "init")
    assert await _run(session, "list-users") == 0
    assert "owner" in capsys.readouterr().out
    assert await _run(session, "logout-all") == 0
    assert await _run(session, "list-devices") == 0
    assert await _run(session, "revoke-device", "not-a-uuid") == 2
    assert await _run(session, "seed") == 0


def test_main_reports_errors(monkeypatch, capsys):
    """main() turns AppError into exit code 1 (DB access mocked away)."""
    import app.cli as cli
    from app.core.errors import NotFound

    async def fake_run(args, session_factory=None):
        raise NotFound("Пользователь 'x' не найден")

    monkeypatch.setattr(cli, "run", fake_run)
    assert cli.main(["list-users"]) == 1
    assert "не найден" in capsys.readouterr().err
