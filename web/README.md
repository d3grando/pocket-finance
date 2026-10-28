# Pocket web (React + PWA + Quick Capture)

Shell, UI kit, API client, Home, login/onboarding, AI line, Quick Capture and PWA live in
`src/{app,components,lib,features/{assistant,auth,dashboard,shell}}`; operations, analytics,
accounts, debts and settings in `src/pages` and `src/features/*`.

## Commands (run in `web/`)

| | |
|---|---|
| `pnpm install` | deps (pnpm 12, settings in `pnpm-workspace.yaml`) |
| `pnpm exec vite --host 0.0.0.0 --port 5201` | dev server; `/api` → `VITE_PROXY_API` (default `http://127.0.0.1:8000`) |
| `pnpm build` | `tsc -b` + production build with PWA (`dist/`, `sw.js`, `manifest.webmanifest`) |
| `pnpm build:desktop` | Electron renderer → `../desktop/renderer` (`base: './'`, hash router, no SW) |
| `pnpm test` | vitest (unit + component) |
| `pnpm e2e` | Playwright smoke: builds, `vite preview` on :5201 + mock API `e2e/mock-api.mjs` on :15201 |
| `E2E_BASE_URL=http://… pnpm e2e` | the same smoke against a live stand (no mock) |
| `SCREENS=1 pnpm exec playwright test e2e/screens.spec.ts` | light/dark, desktop/mobile/Quick screenshots → `test-results/screens` |
| `node scripts/gen-icons.mjs` | re-render PWA icons/favicon from `assets/brand/*.svg` |

## Layout

```
src/lib/api/client.ts     fetch wrapper: cookie+CSRF or Bearer (Electron), ApiError/NetworkError, timeouts, onApiEvent
src/lib/api/endpoints.ts  typed functions for every route of docs/API.md (authApi, transactionsApi, statsApi, …)
src/lib/api/hooks.ts      TanStack Query hooks (useMe, useSettings, useSummary, useTransactions, useAccounts, …)
src/lib/api/keys.ts       query keys (ARCH §12) + SSE entity → keys
src/lib/api/sse.ts        /events/stream over fetch, invalidation, backoff 1→30 s, /health fallback
src/lib/connection.ts     online / reconnecting / offline store (DESIGN §8.4), useConnection()
src/lib/format.ts         formatMoney / formatCompact / formatPercent / dates in Asia/Ho_Chi_Minh (DESIGN §3)
src/lib/decimal.ts        BigInt fixed-point Dec — never float for money
src/lib/amountInput.ts    «2.5м», «800к», «312.4 USDT, 3 TON» → decimal strings (same rules as the parser)
src/lib/tx.ts             row amount/sign/approx/title for TransactionOut
src/lib/theme.ts          light/dark/system (`pf.theme`, `.dark` on <html>; public/theme-init.js pre-paint)
src/lib/desktop.ts        Electron preload bridge (window.pocket) — all members optional
src/components/ui/*       shadcn (new-york, Tailwind 4) — public API stable
src/components/pf/*       Money, Kbd, Spinner, TxRow, IconTile/categoryIcon/accountIcon, EmptyState,
                          SegmentedControl, Page/PageHeader/Section, ConnectionDot/OfflineBanner, ErrorBoundary
src/features/assistant/*  AI line, AssistantProvider (Idempotency-Key per attempt), ResultCard (all card kinds)
src/features/shell/*      AppShell: sidebar / bottom nav / palette (Ctrl+K) / manual sheet (N) / hotkeys
src/features/manual/*     ManualTxForm (no AI) — used by /add, the N sheet and «Вручную»
src/features/debts/*      «Долги» (docs/DEBTS.md §10): Home block, «Счета» → «Долги» + card of a person
                          (`/accounts?debt=all|<id>`), debt op form, assistant `card.debt` / `answer.debts`,
                          «Напоминать о долгах» (Settings → Telegram); helpers in debts/lib.ts
```

Money rules: amounts are strings from the API; format with `formatMoney`, add/subtract only via `Dec`.
Never show «сохранено» before a 2xx. Every create/undo/confirm sends `Idempotency-Key` (reused on retry).

## URL contract used by the shell links (the pages should honour)

- `/operations?period=<preset>&kind=expense|income&category_id=<id>` — from KPI tiles and the donut
- `/operations?status=needs_account` — «Разобрать: N»; `/operations?tx=<id>` — open one operation
- `/operations?focus=search` — global `/` key from other pages
- `/analytics?period=<preset>` — from answer cards («Открыть в аналитике →»)
- `/add?amount=&currency=&text=&kind=` — manual form prefill (Quick Capture «Вручную»)
- `/accounts?debt=all` — scroll to «Долги»; `/accounts?debt=<id>` — open the card of a person (bot «Открыть в Pocket», cards)

## Electron bridge from the desktop preload (`window.pocket`)

The contract is `PocketBridge` in [`desktop/src/shared/ipc.ts`](../desktop/src/shared/ipc.ts) (the single
source of truth); the web side reads it in `src/lib/desktop.ts`. In short: `auth.*` (device token),
`config.get()` (`serverUrl`, `apiBaseUrl`, `hotkey`, `platform` = `process.platform`, `deviceName`, …),
`window.*` (hide, open the full panel, resize, pin) and `window.onShown(cb)` — every time the tray window is
shown (`onShow` is a deprecated alias).
Routes inside Electron: `index.html#/quick` (or `app://pocket/quick`, normalised to the hash form).
