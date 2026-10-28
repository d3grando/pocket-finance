"""Normalize list-valued env vars for pydantic-settings.

Usage (in an entrypoint):  eval "$(python /usr/local/lib/pf/pf-env-normalize.py app.config:Settings)"

docs/ARCH.md §11 documents WEB_ORIGINS, WEBAUTHN_ORIGINS and TELEGRAM_ALLOWED_IDS as
comma-separated. pydantic-settings decodes list fields as JSON *before* field validators run,
so "https://a,https://b" (or an empty value) fails unless the settings class marks the field
NoDecode. This helper first tries the settings class as is; only if that fails does it print
`export VAR='[json]'` lines converting CSV values to JSON arrays. Prints nothing when no change
is needed, so it is a no-op once the settings classes accept CSV themselves.
"""

from __future__ import annotations

import importlib
import json
import os
import sys

LIST_VARS: dict[str, type] = {
    "WEB_ORIGINS": str,
    "WEBAUTHN_ORIGINS": str,
    "TELEGRAM_ALLOWED_IDS": int,
}


def _probe(target: str) -> bool:
    mod_name, _, cls_name = target.partition(":")
    try:
        cls = getattr(importlib.import_module(mod_name), cls_name)
        cls()
        return True
    except Exception:  # noqa: BLE001 — any parse error means "needs normalization"
        return False


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "app.config:Settings"
    if _probe(target):
        return 0
    lines = []
    for name, typ in LIST_VARS.items():
        raw = os.environ.get(name)
        if raw is None or raw.strip().startswith("["):
            continue
        items: list[object] = [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
        if typ is int:
            try:
                items = [int(x) for x in items]  # type: ignore[arg-type]
            except ValueError:
                print(f"{name} must be integers separated by commas: {raw!r}", file=sys.stderr)
                continue
        value = json.dumps(items, ensure_ascii=False).replace("'", "")
        lines.append(f"export {name}='{value}'")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
