"""Admin CLI: ``python -m app.cli <command>``.

Commands:
  init [--username owner]                    create owner user + settings + seeds (idempotent)
  create-owner [--username owner] [--password P | --password-stdin] [--force] [--keep-passkeys]
                                             init + set the owner's password; without a password
                                             one is generated and printed ONCE; --force on an
                                             existing password also signs out everywhere
                                             (sessions, devices and passkeys)
  set-password [--username owner] [--password P | --password-stdin] [--keep-sessions]
               [--keep-passkeys]             change a password (generated if not given); signs out
                                             every session and device unless --keep-sessions and
                                             deletes every passkey unless --keep-passkeys
  create-test-user [--username test] [--password P | --password-stdin] [--force]
                                             extra user for tests/demo (refused in prod w/o --force)
  list-users
  list-devices [--username owner] / revoke-device <id>
  logout-all [--username owner] [--keep-passkeys]
                                             revoke every session and device token, delete
                                             every passkey (unless --keep-passkeys)
  seed [--username owner]                    (re)seed default categories/accounts if missing
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.errors import AppError
from app.core.security import generate_password

Handler = Callable[[Any, argparse.Namespace], Awaitable[int]]


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _read_password(args: argparse.Namespace) -> tuple[str, bool]:
    """Return (password, generated)."""
    if getattr(args, "password_stdin", False):
        pw = sys.stdin.readline().rstrip("\r\n")
        return pw, False
    if getattr(args, "password", None):
        return args.password, False
    return generate_password(), True


def _print_revoked(n_sessions: int, n_devices: int, n_passkeys: int) -> None:
    _out(
        f"Отозвано сессий: {n_sessions}, устройств: {n_devices}, "
        f"удалено ключей входа (passkey): {n_passkeys}."
    )


def _print_password(username: str, password: str, generated: bool) -> None:
    if generated:
        _out(f"Пароль пользователя {username} (показывается один раз, сохраните):")
        _out(f"  {password}")
    else:
        _out(f"Пароль пользователя {username} установлен.")


async def _user_or_fail(session: Any, username: str) -> Any:
    from app.services.auth.users import get_user_by_username

    user = await get_user_by_username(session, username)
    if user is None:
        raise AppError(f"Пользователь {username!r} не найден", code="not_found", status=404)
    return user


async def cmd_init(session: Any, args: argparse.Namespace) -> int:
    from app.services.auth.users import ensure_owner

    user = await ensure_owner(session, args.username)
    await session.commit()
    _out(f"Владелец {user.username!r} готов (id={user.id}).")
    if not user.password_hash:
        _out("Пароль ещё не задан: python -m app.cli set-password")
    return 0


async def cmd_create_owner(session: Any, args: argparse.Namespace) -> int:
    from app.services.auth.users import ensure_owner, logout_everywhere, set_password

    user = await ensure_owner(session, args.username)
    replacing = bool(user.password_hash)
    if replacing and not args.force:
        await session.commit()
        _err(f"У {user.username!r} уже есть пароль. Сменить: set-password (или --force).")
        return 2
    password, generated = _read_password(args)
    await set_password(session, user, password)
    if replacing:
        # --force over an existing password = «the password leaked»: nothing signed in with the
        # old one may stay.
        _print_revoked(
            *await logout_everywhere(session, user.id, remove_passkeys=not args.keep_passkeys)
        )
    await session.commit()
    _out(f"Владелец {user.username!r} создан (id={user.id}).")
    _print_password(user.username, password, generated)
    return 0


async def cmd_set_password(session: Any, args: argparse.Namespace) -> int:
    from app.services.auth.passkeys import delete_all
    from app.services.auth.users import logout_everywhere, set_password

    user = await _user_or_fail(session, args.username)
    password, generated = _read_password(args)
    await set_password(session, user, password)
    if not args.keep_sessions:
        _print_revoked(
            *await logout_everywhere(session, user.id, remove_passkeys=not args.keep_passkeys)
        )
    elif not args.keep_passkeys:
        _print_revoked(0, 0, await delete_all(session, user.id))
    await session.commit()
    _print_password(user.username, password, generated)
    return 0


async def cmd_create_test_user(session: Any, args: argparse.Namespace) -> int:
    from app.config import get_settings
    from app.services.auth.users import create_user

    if get_settings().is_prod and not args.force:
        _err("APP_ENV=prod: тестового пользователя не создаю (нужен --force).")
        return 2
    password, generated = _read_password(args)
    user = await create_user(session, args.username, password=password, seed=True)
    await session.commit()
    _out(f"Тестовый пользователь {user.username!r} создан (id={user.id}).")
    _print_password(user.username, password, generated)
    return 0


async def cmd_list_users(session: Any, args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.models import User

    for u in (await session.execute(select(User).order_by(User.id))).scalars():
        state = "активен" if u.is_active else "выключен"
        pw = "пароль задан" if u.password_hash else "без пароля"
        _out(f"{u.id}\t{u.username}\t{state}\t{pw}\t{u.created_at:%Y-%m-%d %H:%M}")
    return 0


async def cmd_list_devices(session: Any, args: argparse.Namespace) -> int:
    from app.services.auth.devices import list_devices

    user = await _user_or_fail(session, args.username)
    for d in await list_devices(session, user.id):
        last = f"{d.last_used_at:%Y-%m-%d %H:%M}" if d.last_used_at else "—"
        _out(f"{d.id}\t{d.platform}\t{d.name}\tпоследний вход: {last}")
    return 0


async def cmd_revoke_device(session: Any, args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.models import DeviceToken
    from app.services.auth.devices import revoke_device

    try:
        device_id = uuid.UUID(args.device_id)
    except ValueError:
        _err("Нужен UUID устройства (см. list-devices).")
        return 2
    user_id = await session.scalar(select(DeviceToken.user_id).where(DeviceToken.id == device_id))
    if user_id is None:
        _err("Устройство не найдено.")
        return 1
    await revoke_device(session, user_id, device_id)
    await session.commit()
    _out("Устройство отозвано.")
    return 0


async def cmd_logout_all(session: Any, args: argparse.Namespace) -> int:
    from app.services.auth.users import logout_everywhere

    user = await _user_or_fail(session, args.username)
    revoked = await logout_everywhere(session, user.id, remove_passkeys=not args.keep_passkeys)
    await session.commit()
    _print_revoked(*revoked)
    return 0


async def cmd_seed(session: Any, args: argparse.Namespace) -> int:
    from app.services.auth.users import seed_user_data

    user = await _user_or_fail(session, args.username)
    done = await seed_user_data(session, user.id)
    await session.commit()
    _out("Сиды: " + (", ".join(done) if done else "ещё не реализованы"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.cli", description="Pocket Finance admin CLI")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name: str, fn: Handler, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(handler=fn)
        return sp

    def pw_opts(sp: argparse.ArgumentParser) -> None:
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--password", help="пароль (лучше --password-stdin)")
        g.add_argument("--password-stdin", action="store_true", help="прочитать пароль из stdin")

    sp = add("init", cmd_init, "создать владельца, настройки и сиды (идемпотентно)")
    sp.add_argument("--username", default="owner")
    def keep_passkeys_opt(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--keep-passkeys", action="store_true", help="не удалять ключи входа (passkey)"
        )

    sp = add("create-owner", cmd_create_owner, "создать владельца и задать пароль")
    sp.add_argument("--username", default="owner")
    sp.add_argument(
        "--force",
        action="store_true",
        help="перезаписать существующий пароль (и выйти везде: сессии, устройства, passkey)",
    )
    keep_passkeys_opt(sp)
    pw_opts(sp)
    sp = add("set-password", cmd_set_password, "сменить пароль")
    sp.add_argument("--username", default="owner")
    sp.add_argument("--keep-sessions", action="store_true", help="не отзывать сессии/устройства")
    keep_passkeys_opt(sp)
    pw_opts(sp)
    sp = add("create-test-user", cmd_create_test_user, "создать тестового пользователя")
    sp.add_argument("--username", default="test")
    sp.add_argument("--force", action="store_true", help="разрешить в prod")
    pw_opts(sp)
    add("list-users", cmd_list_users, "список пользователей")
    sp = add("list-devices", cmd_list_devices, "устройства Electron пользователя")
    sp.add_argument("--username", default="owner")
    sp = add("revoke-device", cmd_revoke_device, "отозвать устройство")
    sp.add_argument("device_id")
    sp = add("logout-all", cmd_logout_all, "выйти со всех устройств")
    sp.add_argument("--username", default="owner")
    keep_passkeys_opt(sp)
    sp = add("seed", cmd_seed, "досоздать стартовые категории и счета")
    sp.add_argument("--username", default="owner")
    return p


async def run(args: argparse.Namespace, session_factory: Any = None) -> int:
    """Run one command in its own DB session (``session_factory`` is injectable for tests)."""
    if session_factory is None:
        from app.db import dispose_engine, get_sessionmaker

        factory = get_sessionmaker()
        try:
            async with factory() as session:
                return await args.handler(session, args)
        finally:
            await dispose_engine()
    async with session_factory() as session:
        return await args.handler(session, args)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except AppError as e:
        _err(f"Ошибка: {e.message}")
        return 1


__all__ = ["build_parser", "main", "run"]
