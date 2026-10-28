"""Release-config regression tests (stdlib only; Docker optional).

    python3 infra/tests/test_release_config.py      (CI: scripts/ci-backup-tests.sh)

Guards the public defaults: compose/env examples point nowhere and fail closed, the web/edge
Caddy hide the Server header (also on errors), images and CI actions are pinned, Docker build
contexts never pick up env files, and the image label matches the prune filter in update.sh.
Checks that need ``docker compose config`` are skipped when Docker is not available.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REPO_URL = "https://github.com/d3grando/pocket-finance"
SECRETS = (
    "POSTGRES_PASSWORD=ci\n"
    "SECRET_KEY=ci-secret-key-0123456789abcdef0123456789\n"
    "BOT_SERVICE_TOKEN=ci-bot-token-0123456789abcdef\n"
)


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def env_lines(rel: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in read(rel).splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _has_compose() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    return r.returncode == 0


HAS_COMPOSE = _has_compose()


class EnvExampleTest(unittest.TestCase):
    def test_root_env_example_has_placeholders_only(self) -> None:
        env = env_lines(".env.example")
        for key in ("AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "TELEGRAM_ALLOWED_IDS",
                    "TELEGRAM_BOT_USERNAME", "TELEGRAM_BOT_TOKEN", "WEB_ORIGINS",
                    "WEBAUTHN_RP_ID", "WEBAUTHN_ORIGINS", "TRUSTED_PROXIES",
                    "PARSER_LEXICON_EXTRA", "SECRET_KEY", "BOT_SERVICE_TOKEN",
                    "POSTGRES_PASSWORD"):
            self.assertEqual(env.get(key), "", key)
        # no ready-made address: a forgotten PUBLIC_URL / PF_DOMAIN must stop the stack
        self.assertEqual(env["PUBLIC_URL"], "")
        self.assertEqual(env["PF_DOMAIN"], "")
        self.assertEqual(env["AI_ENABLED"], "true")
        self.assertIn("192.0.2.10/32", read(".env.example"))  # documented proxy example

    def test_every_compose_variable_is_documented(self) -> None:
        env = read(".env.example")
        used = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", read("docker-compose.yml")))
        missing = sorted(v for v in used
                         if not re.search(r"^(# )?" + v + "=", env, re.M))
        self.assertEqual(missing, [])

    def test_bot_env_example(self) -> None:
        env = env_lines("bot/.env.example")
        self.assertEqual(env["TELEGRAM_ALLOWED_IDS"], "")
        self.assertEqual(env["TELEGRAM_BOT_TOKEN"], "")
        self.assertEqual(env["PUBLIC_URL"], "https://pocket.example.com")


class ComposeTextTest(unittest.TestCase):
    """Defaults written in docker-compose.yml (no Docker needed)."""

    def setUp(self) -> None:
        self.c = read("docker-compose.yml")

    def defaults(self, var: str) -> set[str]:
        return {m.group(1) for m in re.finditer(r"\$\{" + var + r"(:[-?][^}]*)\}", self.c)}

    def test_public_url_is_required(self) -> None:
        d = self.defaults("PUBLIC_URL")
        self.assertTrue(d and all(x.startswith(":?") for x in d), d)
        self.assertEqual(len(re.findall(r"PUBLIC_URL: \$\{PUBLIC_URL:\?", self.c)), 2)

    def test_optional_settings_default_to_empty(self) -> None:
        for var in ("WEB_ORIGINS", "WEBAUTHN_RP_ID", "WEBAUTHN_ORIGINS", "AI_BASE_URL",
                    "AI_MODEL", "AI_API_KEY", "TELEGRAM_ALLOWED_IDS", "TELEGRAM_BOT_USERNAME",
                    "PARSER_LEXICON_EXTRA"):
            self.assertEqual(self.defaults(var), {":-"}, var)
        self.assertEqual(self.defaults("AI_ENABLED"), {":-true"})
        self.assertEqual(len(re.findall(r"TELEGRAM_ALLOWED_IDS: \$\{TELEGRAM_ALLOWED_IDS:-\}",
                                        self.c)), 2)

    def test_web_trusts_no_proxy_by_default(self) -> None:
        self.assertEqual(self.defaults("TRUSTED_PROXIES"), {":-127.0.0.1/32"})
        self.assertIn("trusted_proxies static {$TRUSTED_PROXIES:127.0.0.1/32}",
                      read("infra/caddy/web.Caddyfile"))
        self.assertIn("TRUSTED_PROXIES=127.0.0.1/32", read("infra/docker/web.Dockerfile"))
        self.assertNotIn("private_ranges", re.sub(r"(?m)^\s*#.*$", "", self.c))

    def test_edge_uses_internal_listener(self) -> None:
        self.assertIn("WEB_UPSTREAM: web:8081", self.c)
        self.assertIn("{$WEB_UPSTREAM:web:8081}", read("infra/caddy/edge.Caddyfile.tmpl"))
        caddy = read("infra/caddy/web.Caddyfile")
        self.assertIn(":8080, :8081 {", caddy)
        # Only :8080 is ever published.
        self.assertNotRegex(self.c, r'(?m)^\s*-\s*"[^"\n]*:8081(/\w+)?"')


class CaddyTest(unittest.TestCase):
    def test_server_header_removed_on_errors_too(self) -> None:
        for rel in ("infra/caddy/web.Caddyfile", "infra/caddy/edge.Caddyfile.tmpl"):
            caddy = read(rel)
            i = caddy.index("handle_errors")
            self.assertIn("header -Server", caddy[i:], rel)
            self.assertIn("header -Server", caddy[:i], rel)


class DockerTest(unittest.TestCase):
    def test_dockerignore_excludes_env_files_everywhere(self) -> None:
        files = sorted(glob.glob(os.path.join(ROOT, "infra/docker/*.dockerignore")))
        self.assertEqual(len(files), 4)
        for path in files:
            with open(path, encoding="utf-8") as f:
                lines = [x.strip() for x in f]
            for pat in ("**/.env*", "!**/.env.example", "**/node_modules", "**/.venv",
                        "**/test-results", "**/playwright-report"):
                self.assertIn(pat, lines, f"{os.path.basename(path)}: {pat}")
            self.assertLess(lines.index("**/.env*"), lines.index("!**/.env.example"))

    def test_base_images_pinned_and_no_curl_pipe_sh(self) -> None:
        for rel in ("infra/docker/backend.Dockerfile", "infra/docker/bot.Dockerfile",
                    "infra/docker/web.Dockerfile"):
            text = read(rel)
            args = re.findall(r"^ARG [A-Z_]+_IMAGE=(\S+)$", text, re.M)
            self.assertTrue(args, rel)
            for image in args:
                self.assertRegex(image, r"@sha256:[0-9a-f]{64}$", f"{rel}: {image}")
            self.assertNotRegex(text, r"curl[^\n|]*\|\s*(ba)?sh", rel)

    def test_image_label_matches_update_prune_filter(self) -> None:
        for path in glob.glob(os.path.join(ROOT, "infra/docker/*.Dockerfile")):
            rel = os.path.relpath(path, ROOT)
            self.assertIn(f'org.opencontainers.image.source="{REPO_URL}"', read(rel), rel)
        self.assertIn(f"label=org.opencontainers.image.source={REPO_URL}",
                      read("scripts/update.sh"))


class LicenseTest(unittest.TestCase):
    def test_python_packages_are_mit(self) -> None:
        for rel in ("backend/pyproject.toml", "bot/pyproject.toml"):
            self.assertRegex(read(rel), r'(?m)^license = "MIT"$', rel)


class WorkflowTest(unittest.TestCase):
    PATH = ".github/workflows/ci.yml"

    def setUp(self) -> None:
        self.wf = read(self.PATH)

    def test_actions_pinned_to_full_sha(self) -> None:
        uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)(.*)$", self.wf, re.M)
        self.assertTrue(uses)
        for ref, comment in uses:
            self.assertRegex(ref, r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$", ref)
            self.assertRegex(comment, r"#\s*v\d+", f"{ref}: version comment")

    def test_least_privilege_hosted_runners_no_secrets(self) -> None:
        self.assertRegex(self.wf, r"(?m)^permissions:\n  contents: read$")
        self.assertNotIn("self-hosted", self.wf)
        self.assertNotIn("secrets.", self.wf)
        self.assertNotRegex(self.wf, r"curl[^\n|]*\|\s*(ba)?sh")
        runs_on = set(re.findall(r"runs-on:\s*(\S+)", self.wf))
        self.assertEqual(runs_on, {"ubuntu-24.04"})


@unittest.skipUnless(HAS_COMPOSE, "docker compose not available")
class ComposeConfigTest(unittest.TestCase):
    """What compose actually renders from .env.example + generated secrets."""

    def render(self, env_text: str, *extra: str) -> subprocess.CompletedProcess[str]:
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write(env_text)
            path = f.name
        try:
            clean = {k: v for k, v in os.environ.items() if not re.match(
                r"^(PUBLIC_URL|AI_|TELEGRAM_|TRUSTED_PROXIES|WEB_ORIGINS|WEBAUTHN_)", k)}
            return subprocess.run(
                ["docker", "compose", "-p", "pf-release-test", "--project-directory", ROOT,
                 "-f", os.path.join(ROOT, "docker-compose.yml"), "--env-file", path, *extra,
                 "config", "--format", "json"],
                capture_output=True, text=True, env=clean, timeout=60,
            )
        finally:
            os.unlink(path)

    def test_example_env_renders_neutral_values(self) -> None:
        r = self.render(read(".env.example") + SECRETS + "PUBLIC_URL=https://pocket.example.com\n"
                        "PF_DOMAIN=pocket.example.com\n", "--profile", "standalone")
        self.assertEqual(r.returncode, 0, r.stderr)
        svc = json.loads(r.stdout)["services"]
        api = svc["api"]["environment"]
        self.assertEqual(api["PUBLIC_URL"], "https://pocket.example.com")
        for key in ("AI_BASE_URL", "AI_MODEL", "TELEGRAM_ALLOWED_IDS", "WEBAUTHN_RP_ID",
                    "WEB_ORIGINS", "TELEGRAM_BOT_USERNAME"):
            self.assertEqual(api[key], "", key)
        self.assertEqual(api["AI_ENABLED"], "true")
        self.assertEqual(svc["bot"]["environment"]["TELEGRAM_ALLOWED_IDS"], "")
        self.assertEqual(svc["bot"]["environment"]["TIMEZONE"], "Asia/Ho_Chi_Minh")
        self.assertEqual(api["RATE_LIMIT_LOGIN_FAILURES_PER_HOUR"], "20")
        self.assertEqual(svc["web"]["environment"]["TRUSTED_PROXIES"], "127.0.0.1/32")
        for name, caps in (("backup", {"SETUID", "SETGID", "DAC_OVERRIDE"}),
                           ("caddy", {"NET_BIND_SERVICE"})):
            s = svc[name]
            self.assertEqual(s.get("cap_drop"), ["ALL"], name)
            self.assertEqual(set(s.get("cap_add", [])), caps, name)
            self.assertIn("no-new-privileges:true", s.get("security_opt", []), name)
            self.assertGreater(int(s.get("mem_limit", 0)), 0, name)
            self.assertGreater(int(s.get("pids_limit", 0)), 0, name)
        ports = [p.get("target") for p in svc["web"].get("ports", [])]
        self.assertEqual(ports, [8080])

    def test_missing_public_url_fails_with_hint(self) -> None:
        env = "\n".join(x for x in read(".env.example").splitlines()
                        if not x.startswith("PUBLIC_URL="))
        r = self.render(env + "\n" + SECRETS)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("set PUBLIC_URL", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
