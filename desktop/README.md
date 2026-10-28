# Pocket desktop (Electron, Windows и macOS)

Трей «Pocket» + всплывающее окно Quick Capture 420×450 у трея, глобальный хоткей, автозапуск,
вход паролем один раз (токен устройства в `safeStorage`: DPAPI на Windows, Связка ключей на macOS),
«Полная панель» — большое окно с тем же фронтом.

На macOS: значок в строке меню (без значка в Dock), левый клик — окно под значком, правый — меню,
хоткей по умолчанию `⌘⇧Space`. Сборка `.dmg` только на macOS: `pnpm dist:mac` (Apple Silicon,
ad-hoc подпись) или релизный workflow `.github/workflows/release.yml` (раннер macOS).
Чек-лист ручной проверки: `docs/WINDOWS_CHECKLIST.md`.

## Как устроено
| Файл | Что делает |
|---|---|
| `src/main/index.ts` | композиция: single-instance lock, `app://pocket`, трей, popup, хоткей, автозапуск, IPC, монитор связи |
| `src/main/popup.ts` | окно 420×450 (создаётся один раз, только show/hide), Esc → страница / blur / Ctrl+Shift+F, ресайз вверх до 640 |
| `src/main/position.ts` | **чистая** логика позиции: сторона панели задач по `workArea` (macOS — всегда строка меню сверху), авто-скрытие, мульти-монитор, DIP |
| `src/main/visibility.ts` | **чистые** правила показа: трей-клик, хоткей, blur, Esc (сначала страница, потом main), «закреплено» (вход/диалог) |
| `src/main/hotkey.ts` | **чистый** менеджер хоткея: `register()==false` → «занято», старый остаётся, свободные варианты |
| `src/main/token-store.ts` | токен устройства: `safeStorage` → `<userData>/device-token.bin`; без шифрования — только в памяти |
| `src/main/protocol.ts`, `assets-resolver.ts` | `app://pocket/*` → web-сборка (`renderer/`, SPA-fallback), `app://pocket/__pocket/*` → свои страницы (`out/ui`), CSP |
| `src/main/security.ts` | запрет навигации/новых окон/webview/разрешений; в браузер — только ссылки на свой сервер и `t.me/<бот>` (`urls.ts:allowedExternalUrl`) |
| `src/main/cli-hardening.ts` | упакованная сборка: убрать `--remote-debugging-*` до `ready` (остальное закрыто fuses, `electron-builder.yml`) |
| `src/main/ipc-handlers.ts`, `ipc-policy.ts` | белый список IPC (только main frame наших окон с `app://pocket`) + какое окно/страница что может: адрес сервера, хоткей, автозапуск, выход — только окно настроек (адрес — ещё экран входа при первом запуске); смена адреса — через системный диалог (`server-change.ts`) |
| `src/preload/index.ts` | `window.pocket` через `contextBridge` (контракт — `src/shared/ipc.ts`, `PocketBridge`) |
| `src/ui/*` | свои страницы: вход, настройки, облегчённый Quick Capture (если web-сборки нет) |

**Фронт.** Если в `renderer/` лежит web-сборка (`pnpm build:renderer` = `web` в режиме `desktop`), popup открывает
`app://pocket/quick`, «Полная панель» — `app://pocket/<route>`. Если сборки нет — popup открывает встроенный
облегчённый `__pocket/capture.html` (ввод, карточка с «Отменить/Изменить», KPI, последние операции, связь),
а «Полная панель» открывает адрес сервера в браузере. Экран входа и настройки — всегда свои (`__pocket/`).

**Адрес сервера.** В сборку его можно вшить: `PF_DEFAULT_SERVER_URL=https://pocket.example.com pnpm dist:win`.
Без него первый запуск сначала спрашивает адрес сервера (экран входа), потом пароль.

## Контракт `window.pocket` для web (режим desktop)
```ts
if (window.pocket?.isDesktop) {
  const cfg = await window.pocket.config.get()            // { apiBaseUrl, serverUrl, hotkey, signedIn, … }
  configureApi({ baseUrl: cfg.apiBaseUrl, getToken: window.pocket.auth.getToken })
  window.pocket.window.onShown(() => aiInput.focus())     // окно показано → фокус в поле (+ анимация от трея: e.edge)
}
// 401 от API → await window.pocket.auth.clearToken()       // main покажет экран входа
// открыт диалог/выбор счёта/превью → window.pocket.window.setPinned(true); закрыт → false
// превью выросло → window.pocket.window.resize(px)  (окно растёт вверх от трея, максимум 640)
// «Полная панель» → window.pocket.window.openFull('/analytics?period=this_month')
// внешняя ссылка → window.pocket.openExternal('https://…')  (только свой сервер и https://t.me/<бот>)
```
Esc сначала получает страница (DESIGN §11: закрыть верхний слой — меню ⋯, диалог, превью; §9.1: очистить поле).
Main клавишу не глотает: preload после обработки сообщает, вызвала ли страница `preventDefault()`, и main прячет
окно только на Esc, который страница не обработала, и только если окно не было закреплено (`pinned`) в момент
нажатия. Страница, которая не ответила за 0,5 с (зависла, грузится), всё равно прячется. Обработал Esc сам —
вызывай `e.preventDefault()`; хочешь спрятать окно — `window.pocket.window.hide()` или просто не трогай Esc.
Ctrl+Shift+F — полная панель. CSP страниц: `script-src 'self'` (никаких inline-скриптов в `index.html`),
`connect-src 'self' <origin API>`.

## Команды
```sh
cd desktop
pnpm install
pnpm build                       # electron-vite: out/main, out/preload, out/ui
pnpm test                        # vitest: позиционирование, хоткей, токен, URL/протокол, API, формат денег
pnpm test:e2e                    # build + Playwright _electron под Xvfb против tests/e2e/mock-api.ts
pnpm typecheck
pnpm icons                       # пересобрать иконки из assets/brand/*.svg (DESIGN §13)
pnpm build:renderer              # (опц.) web → renderer/ (режим desktop)
pnpm dist:win                    # на Windows → release/Pocket-Setup-<ver>.exe
pnpm dist:win:docker             # на Linux/macOS: тот же electron-builder в образе electronuserland/builder:wine
pnpm test:packaged               # Linux dir-сборка: fuses, пробы (RUN_AS_NODE, --inspect, --remote-debugging-port), smoke
pnpm fuses:win                   # fuses готового release/win-unpacked/Pocket.exe (после dist:win)
bash scripts/wine-smoke.sh       # Pocket.exe под wine: стартует, RUN_AS_NODE не исполняет скрипт, подменённый app.asar не стартует
```
Dev-переменные (только в неупакованном режиме): `POCKET_SERVER_URL`, `POCKET_USER_DATA`, `POCKET_RENDERER_DIR`,
`POCKET_E2E=1` (тест-хуки `globalThis.__pocketTest`). Под root (Linux, CI/контейнер) — `ELECTRON_DISABLE_SANDBOX=1`.

## Решения и отклонения
- Окно непрозрачное (`backgroundColor` = `--card`, Windows 11 сам скругляет углы), без `acrylic`/`transparent`:
  их сочетание на Windows 10/11 ведёт себя по-разному, проверить без Windows нельзя.
- Маршрут мини-окна в web — `/quick` (ARCH §13, `web/src/app/routes.tsx`).
- Автозапуск — только у установленного приложения (`HKCU\…\Run`, `Pocket.exe --hidden`: старт в трей), по умолчанию включён один раз при первом запуске.
- API по http разрешён только для loopback (dev/тесты); в настройках — только `https://`. Смена сервера = новый вход
  и подтверждение в системном окне (страница не может его подделать или пропустить).
- Хоткеем нельзя назначить системные сочетания: Ctrl+C/V/X/A/Z/Y, Alt+F4, Alt+Tab, Ctrl+Alt+Del, Win+<клавиша> и др.
  (`shared/accelerator.ts:RESERVED_ACCELERATORS`).
- Защита упакованного приложения от кода под тем же пользователем (DPAPI и так привязан к пользователю — это защита в
  глубину): Electron fuses в `electron-builder.yml` (`electronFuses`) — выключены `ELECTRON_RUN_AS_NODE`, `NODE_OPTIONS`,
  `--inspect*`, приложение грузится только из `app.asar`, целостность `app.asar` проверяется при старте (хэш вшит в
  `Pocket.exe`), у `file://` нет лишних прав. `--remote-debugging-port/-pipe` main вычищает до `ready`
  (`src/main/cli-hardening.ts`). Поэтому Playwright `_electron` с настоящей упакованной сборкой не работает: packaged-smoke
  гоняет его на копии Linux-сборки, где обратно включён только fuse `--inspect`.
