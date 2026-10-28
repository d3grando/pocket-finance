# Pocket Finance

A self-hosted personal finance tracker that understands plain text. Type «кофе 85к» (coffee 85k),
«такси 120к налом» (taxi 120k, cash) or «перевёл 50 usdt с кошелька на биржу» (moved 50 USDT from
the wallet to the exchange): a built-in rule-based parser, with an LLM for harder phrases, turns it
into a transaction, files it under the right category and updates the balances. One database and
one backend serve the web app / PWA, a Telegram bot and a Windows / macOS tray app for quick capture.

Self-hosted, [MIT](LICENSE) licensed. **The UI and the bot speak Russian.**
Русская версия: [README.ru.md](README.ru.md).

![Home, light theme](docs/screenshots/home-light.png)

| Dark theme | Phone (PWA) | Quick Capture (Electron) |
|---|---|---|
| ![](docs/screenshots/home-dark.png) | ![](docs/screenshots/mobile-home.png) | ![](docs/screenshots/quick.png) |

| Analytics | Accounts and debts |
|---|---|
| ![](docs/screenshots/analytics.png) | ![](docs/screenshots/debts.png) |

## Features

- **Text input** on the web, in Telegram and in a tray window on Windows / macOS (`Ctrl+Alt+Space` / `⌘⇧Space`):
  several operations in one message, dates («вчера», «в пятницу»), accounts («налом», «с карты»),
  recipients (`@handle`), corrections («нет, 250к»), undo («отмени»), questions
  («сколько на еду в октябре?»).
- **Multi-currency and crypto**: ₫, $, ₽, USDT, TON, BTC… Multi-asset wallets, exchanges,
  transfers between accounts, rates on the date of the operation (Vietcombank, Bank of Russia,
  CoinGecko; open.er-api.com as the fiat fallback).
- **Debts**: who you lent to, who you borrowed from, partial returns, write-offs, Telegram reminders.
- **Recurring payments** and reminders, monthly and daily reports in Telegram.
- **Analytics**: expenses / income / cash flow, categories, trends, period comparison, business costs.
- **Everything is reversible**: «Отменить» (undo) on every action, change history, previews before
  bulk edits.
- **Export** to CSV / XLSX and full JSON, deletion of all data.
- **Sign-in**: password, passkeys / Face ID (WebAuthn), device tokens for the desktop app.
- **AI is optional**: with `AI_ENABLED=false` only the built-in rule-based parser runs.

## Architecture

```
 browser / PWA / Electron / Telegram bot
        │ HTTPS
 [your reverse proxy  OR  built-in Caddy (profile standalone, Let's Encrypt)]
        │
      web (Caddy): SPA static + /api/* ─────► api (FastAPI, migrations on start)
                                              worker (rates, reports, reminders)
      bot (aiogram, long polling) ── HTTP ────┘        │
      backup (scheduled pg_dump, rotation, rsync) ──── db (PostgreSQL 16, internal network)
```

| Directory | What it is |
|---|---|
| `backend/` | FastAPI + SQLAlchemy 2 + Alembic, Python 3.12 (`uv`); the `api` and `worker` processes |
| `bot/` | Telegram bot on aiogram 3, talks to the API over HTTP |
| `web/` | React + Vite, PWA (installable on iPhone), Quick Capture mode for Electron |
| `desktop/` | Electron app for Windows and macOS: tray / menu bar, global hotkey, quick capture window |
| `infra/`, `scripts/` | Dockerfiles, Caddy, backups, updates, stack checks |
| `docs/` | install, architecture, API, design, backups, updates (in Russian) |

Money is `Decimal` / `NUMERIC` only, never float. Details: [`docs/ARCH.md`](docs/ARCH.md), REST
contract: [`docs/API.md`](docs/API.md).

## Quick start (Docker Compose)

You need a Linux server with Docker Engine 25+ and `docker compose` v2.24+, and a domain with HTTPS.

```bash
git clone https://github.com/d3grando/pocket-finance.git /opt/pocket && cd /opt/pocket
scripts/gen-secrets.sh          # creates .env (mode 600) with SECRET_KEY, BOT_SERVICE_TOKEN, DB password
nano .env                       # PUBLIC_URL, AI_*, optionally TELEGRAM_* (see the table below)

docker compose up -d --build                          # behind your own reverse proxy (port 8080)
# or on a bare VPS: set PF_DOMAIN=… in .env, then
docker compose --profile standalone up -d --build     # Caddy obtains the certificate itself

docker compose exec api pf-entrypoint cli create-owner   # user + password (printed once)
curl -s http://127.0.0.1:8080/api/v1/health              # {"status":"ok","db":true,...}
```

Open the site at the `PUBLIC_URL` address, sign in and enter your opening balances. To try it
without a domain (through an SSH tunnel or directly at `http://127.0.0.1:8080`), keep `PUBLIC_URL`
an `https://` address (the API refuses to start with `http://`; any placeholder works) and add the
address you actually open to `WEB_ORIGINS`:

```bash
PUBLIC_URL=https://pocket.example.com
WEB_ORIGINS=http://127.0.0.1:8080
```

Without `WEB_ORIGINS` sign-in works but every change is rejected by the CSRF check (the origin
does not match `PUBLIC_URL`). Over plain `http://` only `127.0.0.1` / `localhost` work: the session
cookie is `Secure`, and browsers keep such cookies over http only for those addresses. From another
machine use an SSH tunnel (`ssh -L 8080:127.0.0.1:8080 your-server`, then open
`http://127.0.0.1:8080`) — on any other http address (a LAN IP, a server IP) sign-in fails silently;
for those use HTTPS (the `standalone` profile with a domain, or your own reverse proxy). If the standalone `caddy` container keeps restarting, see
`docker compose logs caddy` (usually `PF_DOMAIN` is empty). More (reverse proxy, client
IPs, PWA, Face ID): [`docs/INSTALL.md`](docs/INSTALL.md); updates:
[`docs/UPDATE.md`](docs/UPDATE.md); backups: [`docs/BACKUP.md`](docs/BACKUP.md).

### Main `.env` variables

| Variable | Needed | What it is |
|---|---|---|
| `PUBLIC_URL` | always | public https address of the site, e.g. `https://pocket.example.com` |
| `AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL` | if `AI_ENABLED=true` | any OpenAI-compatible API |
| `AI_ENABLED` | — | `false`: no LLM, only the rule-based parser |
| `TELEGRAM_BOT_TOKEN` | for the bot | token from @BotFather |
| `TELEGRAM_ALLOWED_IDS` | for the bot | your Telegram id (ask @userinfobot); empty: the bot answers nobody |
| `TELEGRAM_BOT_USERNAME` | for the bot | bot name without `@`, so the web app can link to it |
| `TRUSTED_PROXIES` | behind a reverse proxy | the exact proxy address, so login limits see the real client IP |
| `DEFAULT_TIMEZONE` | — | default time zone, `Asia/Ho_Chi_Minh` |

Every variable, with comments, is in [`.env.example`](.env.example); the full table is in
`docs/INSTALL.md` §8.

> **Product defaults.** Pocket grew out of bookkeeping in Vietnam: the default main currency is ₫,
> the default time zone is `Asia/Ho_Chi_Minh`, and the merchant dictionary knows local chains.
> Change the server zone with `DEFAULT_TIMEZONE`; each user sets the input currency, the analytics
> currency and the time zone in «Настройки → Общие» (Settings → General). The starter accounts are
> neutral («Карта», «Наличные», «Кошелёк», «Биржа»: card, cash, wallet, exchange). Rename them or
> add your own; aliases («налом», «с карты») are set in the same place.

### AI provider

Any OpenAI-compatible endpoint works: OpenAI (`https://api.openai.com/v1`), OpenRouter, a local
Ollama (`http://host:11434/v1`) or vLLM. Keep in mind what is sent to the LLM: the text of phrases
the built-in parser could not handle, plus a short context (names of accounts, categories,
merchants and people in debts, no amounts). Pick a provider you trust. To choose a model, run the
benchmark on your candidates: [`docs/BENCH.md`](docs/BENCH.md).

### Telegram bot

1. @BotFather → `/newbot` → put the token into `TELEGRAM_BOT_TOKEN` and the name into
   `TELEGRAM_BOT_USERNAME`.
2. Put your id into `TELEGRAM_ALLOWED_IDS`, then run `docker compose up -d bot` and send `/start`
   to the bot.

The bot uses long polling (no inbound ports). Without a token the `bot` container just waits.

### Desktop app (Windows, macOS)

A tray / menu bar window with a global hotkey for quick capture: `Ctrl+Alt+Space` on Windows,
`⌘⇧Space` on macOS. Ready builds are attached to every
[release](https://github.com/d3grando/pocket-finance/releases): `Pocket-Setup-*.exe` (Windows x64)
and `Pocket-*-mac-arm64.dmg` (Apple Silicon), with SHA256 sums. On first launch the app asks for
your server address.

The builds are not signed with a paid certificate. Windows SmartScreen: «More info → Run anyway».
macOS: open it once from «System Settings → Privacy & Security → Open Anyway», or run
`xattr -dr com.apple.quarantine /Applications/Pocket.app`.

To build it yourself: `desktop/` (see [`desktop/README.md`](desktop/README.md) and
`docs/INSTALL.md` §7); the server address can be baked in with `PF_DEFAULT_SERVER_URL`.

## Security

argon2 password hashes, cookie sessions with CSRF protection, device tokens and passkeys, login
rate limits, a strict CSP, an isolated Electron renderer, and a database on an internal network
with no outbound access. Threat model, known limitations and how to report a vulnerability:
[`SECURITY.md`](SECURITY.md).

## Development

```bash
cd backend && uv sync && uv run ruff check . && uv run pytest   # needs PostgreSQL 16 (TEST_DATABASE_URL)
cd bot && uv sync && uv run pytest
cd web && pnpm install && pnpm lint && pnpm test && pnpm build  # e2e: pnpm e2e
cd desktop && pnpm install && pnpm test && pnpm build
scripts/test-stack.sh                                           # the whole stack in Docker
```

Tests make no network calls: the LLM is a local fake server and Telegram is mocked. How to send
changes: [`CONTRIBUTING.md`](CONTRIBUTING.md). CI runs on GitHub Actions
(`.github/workflows/ci.yml`).

## License

[MIT](LICENSE) © 2026 d3grando
