"""Background worker entrypoint: ``python -m app.worker`` (docs/ARCH.md §9)."""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.main import setup_logging


def main() -> None:
    s = get_settings()
    s.check_prod()
    setup_logging(s.log_level)
    from app.services.scheduler.runner import run_forever

    logging.getLogger("pocket.worker").info("worker starting")
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
