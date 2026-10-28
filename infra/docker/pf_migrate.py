"""`alembic upgrade head` for the container entrypoint, with JSON logs.

    python -m pf_migrate            (cwd = /app, where alembic.ini lives)

* Every Alembic step ("Running upgrade 0002 -> 0003, …") is logged as a JSON line, followed by
  a summary: how many migrations were applied and the head revision.
* Database revision unknown to this code (a newer version migrated the database, then the code
  was rolled back) → explains what to do (docs/UPDATE.md «Откат») and exits with code 3
  instead of a bare "Can't locate revision".

Exit codes: 0 ok, 3 database is newer than the code, 1 any other migration error.
"""

from __future__ import annotations

import logging
import os
import re
import sys

import pf_logging

log = logging.getLogger("pf.migrate")

ROLLBACK_HINT = (
    "База данных уже обновлена более новой версией Pocket (ревизия {rev} неизвестна этому коду). "
    "Старый код на ней не запустится. Варианты (docs/UPDATE.md, «Откат»): "
    "1) вернуть базу из бэкапа, сделанного перед обновлением: "
    "scripts/update.sh --ref <версия> --restore daily/<дамп перед обновлением>; "
    "2) сначала откатить миграции НОВЫМ кодом (alembic downgrade <ревизия старой версии>), "
    "потом переключить код; 3) вернуться на новую версию: scripts/update.sh --ref main."
)
_UNKNOWN_REV = re.compile(r"Can't locate revision identified by '([^']*)'")


class _StepCounter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self.steps = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if record.getMessage().startswith("Running upgrade"):
            self.steps += 1
        return True


def unknown_revision(exc: BaseException) -> str | None:
    """Revision id from Alembic's "Can't locate revision identified by '…'" error."""
    m = _UNKNOWN_REV.search(str(exc))
    return m.group(1) if m else None


def main() -> int:
    pf_logging.configure(os.environ.get("LOG_LEVEL", "INFO"))
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.util import CommandError

    counter = _StepCounter()
    logging.getLogger("alembic.runtime.migration").addFilter(counter)
    cfg = Config(os.environ.get("ALEMBIC_CONFIG", "alembic.ini"))
    heads = ",".join(ScriptDirectory.from_config(cfg).get_heads()) or "-"
    log.info("alembic upgrade head (code head: %s)", heads)
    try:
        command.upgrade(cfg, "head")
    except CommandError as e:
        rev = unknown_revision(e)
        if rev is not None:
            log.error(ROLLBACK_HINT.format(rev=rev))
            return 3
        log.error("migration failed: %s", e)
        return 1
    except Exception:
        log.exception("migration failed")
        return 1
    if counter.steps:
        log.info("migrations applied: %d, database at %s", counter.steps, heads)
    else:
        log.info("no new migrations, database at %s", heads)
    return 0


if __name__ == "__main__":
    sys.exit(main())
