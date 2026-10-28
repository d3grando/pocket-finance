"""Live tests are opt-in: a plain ``pytest`` (CI, a fresh clone) skips them instead
of going to the network."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def test_live_smoke_is_skipped_without_pf_live():
    env = {k: v for k, v in os.environ.items() if k != "PF_LIVE"}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs",
         "tests/analytics/test_live_smoke.py"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120,
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "2 skipped" in out and "PF_LIVE=1" in out, out
