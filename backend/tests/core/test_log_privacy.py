"""Financial text never reaches the logs: parser crashes and malformed change
events are logged by length + short hash only."""

from __future__ import annotations

import logging

import pytest

SECRET = "зарплата 3500 USDT от @someone_private"


def test_parser_crash_log_has_no_text(monkeypatch, caplog):
    from app.services.parser import parser as parser_mod
    from tests.parser.ctxs import CTX

    def boom(text, ctx):  # type: ignore[no-untyped-def]
        raise RuntimeError("parser bug")

    monkeypatch.setattr(parser_mod, "_parse", boom)
    with caplog.at_level(logging.ERROR, logger=parser_mod.__name__):
        res = parser_mod.parse(SECRET, CTX)
    assert res.needs_llm and res.reason == "parser_error"
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "parser failed" in logged and f"len={len(SECRET)}" in logged
    assert "USDT" not in logged and "someone_private" not in logged
    assert parser_mod.redact(SECRET) == parser_mod.redact(SECRET)  # stable: matches a report


@pytest.mark.parametrize("payload", [
    '{"u": "not-a-number", "e": ["transactions"], "note": "' + SECRET + '"}',
    "garbage " + SECRET,
])
def test_bad_event_payload_log_has_no_payload(caplog, payload):
    from app.services.events import bus

    with caplog.at_level(logging.WARNING, logger=bus.log.name):
        assert bus.decode(payload) is None
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "payload" in logged and "USDT" not in logged and "someone_private" not in logged
