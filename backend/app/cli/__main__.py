"""``python -m app.cli <command>`` — see app/cli/__init__.py for the command list."""

from __future__ import annotations

import sys

from app.cli import main

if __name__ == "__main__":
    sys.exit(main())
