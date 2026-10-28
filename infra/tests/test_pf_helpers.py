"""Unit tests for the backend-image helpers in infra/docker (stdlib only).

    python3 infra/tests/test_pf_helpers.py      (CI: scripts/ci-backup-tests.sh)
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "docker"))

import pf_logging  # noqa: E402
import pf_migrate  # noqa: E402
import pf_proxies  # noqa: E402

# /proc/net/route of a container on two compose networks (172.18.0.0/16, 172.19.0.0/16).
ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0
eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0
eth1\t000013AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0
"""
IF_INET6 = """00000000000000000000000000000001 01 80 10 80       lo
fe800000000000000042acfffe120002 02 40 20 80     eth0
fd00000000000000000000000000abcd 02 40 00 80     eth0
"""


class ProxiesTest(unittest.TestCase):
    def test_auto_expands_to_loopback_and_connected_subnets(self) -> None:
        got = pf_proxies.expand("auto", ROUTE, IF_INET6).split(",")
        self.assertEqual(
            got, ["127.0.0.1/32", "::1/128", "172.18.0.0/16", "172.19.0.0/16", "fd00::/64"]
        )

    def test_explicit_list_spaces_and_commas_dedup(self) -> None:
        got = pf_proxies.expand("10.0.0.1, 172.18.0.0/16 10.0.0.1/32", "", "")
        self.assertEqual(got, "10.0.0.1/32,172.18.0.0/16")

    def test_auto_plus_extra(self) -> None:
        got = pf_proxies.expand("auto,203.0.113.5", ROUTE, "")
        self.assertTrue(got.endswith(",203.0.113.5/32"))
        self.assertIn("172.18.0.0/16", got)

    def test_wildcards_and_garbage_are_refused(self) -> None:
        for bad in ("*", "0.0.0.0/0", "::/0", "auto,*", "not-an-ip"):
            with self.assertRaises(ValueError, msg=bad):
                pf_proxies.expand(bad, ROUTE, "")

    def test_empty_means_loopback_only(self) -> None:
        self.assertEqual(pf_proxies.expand("", "", ""), "127.0.0.1/32,::1/128")

    def test_cli_exit_code(self) -> None:
        self.assertEqual(pf_proxies.main(["x", "*"]), 2)


def _access_record(client: str, path: str, status: int = 200) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        (client, "GET", path, "1.1", status), None,
    )


class LoggingTest(unittest.TestCase):
    def test_json_formatter_shape(self) -> None:
        rec = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "Started %s", ("x",), None)
        rec.request_id = "abc12345"
        d = json.loads(pf_logging.JsonFormatter().format(rec))
        self.assertEqual(d["level"], "INFO")
        self.assertEqual(d["logger"], "uvicorn.error")
        self.assertEqual(d["msg"], "Started x")
        self.assertEqual(d["request_id"], "abc12345")

    def test_access_formatter_fields(self) -> None:
        d = json.loads(pf_logging.AccessJsonFormatter().format(_access_record("1.2.3.4:0", "/api/v1/x?a=1", 404)))
        self.assertEqual((d["client"], d["method"], d["path"], d["status"]), ("1.2.3.4:0", "GET", "/api/v1/x", 404))  # query strings carry user text: never logged
        self.assertNotIn("a=1", d["msg"])
        self.assertIn('"GET /api/v1/x HTTP/1.1" 404', d["msg"])

    def test_healthcheck_filter(self) -> None:
        f = pf_logging.HealthcheckFilter()
        self.assertFalse(f.filter(_access_record("127.0.0.1:50000", "/api/v1/health")))
        self.assertFalse(f.filter(_access_record("[::1]:50000", "/api/v1/health")))
        self.assertTrue(f.filter(_access_record("203.0.113.9:0", "/api/v1/health")))
        self.assertTrue(f.filter(_access_record("127.0.0.1:50000", "/api/v1/accounts")))

    def test_uvicorn_log_config_loads(self) -> None:
        import logging.config

        path = os.path.join(os.path.dirname(__file__), "..", "docker", "uvicorn-log.json")
        with open(path) as fh:
            cfg = json.load(fh)
        buf = io.StringIO()
        cfg["handlers"]["access"]["stream"] = buf
        cfg["handlers"]["default"]["stream"] = buf
        old = logging.getLogger().handlers[:]
        try:
            logging.config.dictConfig(cfg)
            logging.getLogger("uvicorn.access").handle(_access_record("127.0.0.1:1", "/api/v1/health"))
            logging.getLogger("uvicorn.access").handle(_access_record("10.0.0.1:0", "/api/v1/me", 401))
            logging.getLogger("uvicorn.error").info("Application startup complete.")
        finally:
            logging.getLogger().handlers[:] = old
        lines = [json.loads(x) for x in buf.getvalue().splitlines()]
        self.assertEqual([x.get("path") for x in lines], ["/api/v1/me", None])
        self.assertEqual(lines[1]["msg"], "Application startup complete.")


class MigrateTest(unittest.TestCase):
    def test_unknown_revision_detected(self) -> None:
        err = Exception("Can't locate revision identified by '0004'")
        self.assertEqual(pf_migrate.unknown_revision(err), "0004")
        self.assertIsNone(pf_migrate.unknown_revision(Exception("relation x does not exist")))
        self.assertIn("docs/UPDATE.md", pf_migrate.ROLLBACK_HINT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
