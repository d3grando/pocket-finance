"""Dump the backend's route table (method + path template) to tests/fixtures/openapi_routes.json.

Runs in the backend env (the bot env cannot import the backend):

    cd backend && APP_ENV=test uv run python ../bot/tests/contract/gen_openapi.py

``tests/test_contract.py`` checks that every request the bot's PocketApi makes hits an existing
backend route with the right method (a typo in a path would otherwise only show up in prod).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import create_app

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "openapi_routes.json"


def main() -> None:
    spec = create_app().openapi()
    routes = {path: sorted(m.upper() for m in ops) for path, ops in spec["paths"].items()}
    OUT.write_text(json.dumps(routes, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT} ({len(routes)} paths)")


if __name__ == "__main__":
    main()
