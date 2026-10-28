# Установка Pocket Finance

Два варианта:

- **A. За своим reverse-proxy** (Caddy, nginx, Traefik…): HTTPS делает прокси, стек отдаёт
  обычный HTTP на одном порту.
- **B. Голый VPS** — стек сам получает сертификат Let's Encrypt (профиль `standalone`, Caddy).

## 1. Требования

- Linux x86_64, 2 ГБ RAM (1 ГБ хватит без сборки образов на сервере), 5 ГБ диска + место под бэкапы.
- Docker Engine 25+ и плагин `docker compose` v2.24+ (`docker compose version`).
  Debian/Ubuntu: `curl -fsSL https://get.docker.com | sh`.
- `git`. Для варианта B — домен с A-записью на сервер и открытые порты 80/443 (TCP и UDP 443).
- Исходящий интернет у сервера: AI-провайдер, курсы валют, Telegram, сборка образов.

> LXC на Proxmox: контейнеру нужны `nesting=1` (и `keyctl=1` для непривилегированного).

## 2. Код и секреты

```bash
git clone https://github.com/d3grando/pocket-finance.git /opt/pocket
```

`scripts/update.sh` обновляет код через `git fetch` из того же remote.

```bash
cd /opt/pocket
scripts/gen-secrets.sh
```

`gen-secrets.sh` создаёт `.env` из `.env.example` (права 600) и заполняет пустые
`SECRET_KEY`, `BOT_SERVICE_TOKEN`, `POSTGRES_PASSWORD`. Уже заданные значения не трогает.
Файл `.env` никогда не коммитится и не попадает в образы.

Откройте `.env` и задайте как минимум:

| Переменная | Пример | Зачем |
|---|---|---|
| `PUBLIC_URL` | `https://pocket.example.com` | адрес сайта (ссылки в боте, WebAuthn) |
| `WEB_ORIGINS` | `https://pocket.example.com` | CORS; через запятую, если адресов несколько |
| `WEBAUTHN_RP_ID` | `pocket.example.com` | Face ID / passkeys: домен без схемы и порта |
| `WEBAUTHN_ORIGINS` | `https://pocket.example.com` | origin'ы для WebAuthn (пусто = `PUBLIC_URL`) |
| `AI_API_KEY` | `sk-…` | ключ OpenAI-совместимого API (`AI_BASE_URL`, `AI_MODEL`) |
| `TELEGRAM_BOT_TOKEN` | `123:ABC…` | токен бота от @BotFather (см. §6) |
| `TELEGRAM_ALLOWED_IDS` | `123456789` | кому бот отвечает (TG user id через запятую) |

Все переменные с комментариями — в `.env.example`; таблица — в §8.

## 3A. За своим reverse-proxy

В `.env`:

```ini
PF_HTTP_BIND=0.0.0.0              # или конкретный адрес, до которого достучится прокси
PF_HTTP_PORT=8080
TRUSTED_PROXIES=192.0.2.10/32     # точный адрес вашего прокси (см. ниже); не private_ranges
BACKUP_DIR=/srv/pocket/backups
```

```bash
docker compose up -d --build
docker compose ps                         # db/api/web/backup — healthy
curl -s http://127.0.0.1:8080/api/v1/health
```

Прокси отдаёт `https://pocket.example.com` → `http://<адрес сервера>:8080` (весь сайт, включая `/api`).
Пример Caddy:

```caddy
pocket.example.com {
	reverse_proxy 192.0.2.20:8080
}
```

Пример nginx (SSE `/api/v1/events/stream` требует `proxy_buffering off`):

```nginx
location / {
    proxy_pass http://192.0.2.20:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 1h;
}
```

**IP клиента** (лимиты входа, бан после неверных паролей, IP в списке сессий) идёт по цепочке
прокси → `web` → `api`:

1. Прокси передаёт `X-Forwarded-For`/`X-Forwarded-Proto`. Caddy ставит в `X-Forwarded-For`
   настоящий адрес клиента; nginx с `$proxy_add_x_forwarded_for` дописывает его в конец — оба
   варианта работают.
2. `web` (Caddy) верит `X-Forwarded-For` только от адресов из `TRUSTED_PROXIES` и читает его
   справа налево до первого недоверенного адреса (`trusted_proxies_strict`): всё, что клиент
   написал в заголовок сам, остаётся левее и игнорируется. В `api` уходит ровно один адрес.
3. `api` верит `X-Forwarded-For` только от контейнеров своей compose-сети
   (`API_TRUSTED_PROXIES=auto`); `uvicorn --forwarded-allow-ips '*'` не используется.

Поэтому в `TRUSTED_PROXIES` должны быть **точные** адреса, с которых прокси приходит на сервер
(за NAT — адрес шлюза). С `private_ranges` любой хост локальной сети, достучавшийся до `:8080`,
сможет подставить чужой IP. Обратная ошибка — адрес прокси не доверен: тогда у всех клиентов
один IP, и пять неверных паролей одного человека дают 429 всем остальным.

Проверка после установки: войдите на сайт с телефона через мобильный интернет → «Настройки →
Вход и устройства → Сессии и устройства»: у текущей сессии должен быть IP телефона, а не адрес
прокси. Если там адрес вида `172.x.0.1` — опубликованный порт обслуживает `docker-proxy`
(например, внутри LXC без iptables Docker) и исходный адрес теряется; тогда добавьте этот шлюз в
`TRUSTED_PROXIES` (`"192.0.2.10/32 172.x.0.1/32"`) и закройте `:8080` от остальной сети файрволом.

Наружу открыт только порт `web`. API, база и бэкапы не публикуются; база живёт в отдельной
внутренней сети `internal` без выхода в интернет.

## 3B. Голый VPS со своим HTTPS

В `.env`:

```ini
PF_DOMAIN=pocket.example.com
ACME_EMAIL=you@example.com     # необязательно, но желательно
PUBLIC_URL=https://pocket.example.com
WEB_ORIGINS=https://pocket.example.com
WEBAUTHN_RP_ID=pocket.example.com
WEBAUTHN_ORIGINS=https://pocket.example.com
COMPOSE_PROFILES=standalone    # чтобы обычные `docker compose up -d` тоже поднимали Caddy
```

```bash
docker compose --profile standalone up -d --build
docker compose logs -f caddy    # «certificate obtained successfully»
curl -s https://pocket.example.com/api/v1/health
```

Если `PF_DOMAIN` пуст, `up -d` всё равно отчитается об успехе, но контейнер `caddy` будет
перезапускаться с сообщением `PF_DOMAIN is required for the standalone profile` в
`docker compose logs caddy` — задайте домен и повторите `up -d`.

Сертификаты хранятся в томе `caddy_data` (переживают пересоздание контейнеров). HTTP
автоматически перенаправляется на HTTPS. Порт `web` (`PF_HTTP_PORT`) при этом можно оставить
на `127.0.0.1`.

## 4. Первый вход

```bash
docker compose exec api pf-entrypoint cli create-owner   # владелец, стартовые категории и счета,
                                                         # пароль — сгенерирует и покажет ОДИН раз
```

Свой пароль: `… cli create-owner --password '…'`. Сменить пароль позже:
`… cli set-password` (на базе без владельца она ответит «Пользователь 'owner' не найден» —
сначала `create-owner`). Откройте сайт по адресу из `PUBLIC_URL`, войдите паролем, пройдите
экран начальных остатков.

> Сайт принимает изменения только с origin из `PUBLIC_URL` и `WEB_ORIGINS`. Для пробы через
> SSH-туннель или на `http://127.0.0.1:8080` добавьте `WEB_ORIGINS=http://127.0.0.1:8080` в `.env`
> и выполните `docker compose up -d`. Иначе вход пройдёт, а сохранение ответит ошибкой
> «Сайт открыт не по адресу PUBLIC_URL…» (`csrf_failed`, `reason=origin`).

Face ID: «Настройки → Вход → Добавить Face ID» (нужен HTTPS и правильные `WEBAUTHN_*`).

> Административные команды запускайте через `pf-entrypoint …` (он подготавливает окружение),
> например `docker compose exec api pf-entrypoint alembic current`.

## 5. PWA на iPhone

Safari → сайт → «Поделиться» → «На экран „Домой“». Нужен настоящий HTTPS-домен.

## 6. Telegram-бот

1. @BotFather → `/newbot` (или существующий бот) → токен в `TELEGRAM_BOT_TOKEN`.
2. Свой TG id (например, у @userinfobot) → `TELEGRAM_ALLOWED_IDS`. Остальным бот не отвечает.
3. `docker compose up -d bot`, затем напишите боту `/start`. При первом обращении владельца
   бот привязывается к единственному пользователю.

Бот работает через long polling: входящие порты и webhook не нужны. **Один токен — один
запущенный бот**: не запускайте второй экземпляр с тем же токеном (тестовый сервер, ноутбук) — Telegram
будет отдавать апдейты то одному, то другому.

Если бот не нужен — просто не задавайте токен: контейнер `bot` пишет в лог одну строку
«TELEGRAM_BOT_TOKEN не задан — бот выключен» и ждёт, без перезапусков и обращений к Telegram.
Токен неверного формата (не `123456789:AA…`) — так же: одна строка с ошибкой, бот не стартует.
После правки токена в `.env`: `docker compose up -d bot`.

## 7. Electron (Windows)

Установщик **`Pocket-Setup-<версия>.exe`** — без подписи, NSIS «в один клик», ставится в профиль
пользователя без прав администратора. Готовые сборки публикуются в GitHub Releases рядом с
`.sha256` — сверяйте хэш перед установкой. Собрать самому (нужен Docker на Linux/macOS или Windows):

```bash
cd desktop
pnpm install --frozen-lockfile
pnpm build:renderer            # web в режиме desktop → desktop/renderer/ (без него — облегчённое мини-окно)
# адрес сервера (необязателен, см. ниже) вшивается на шаге сборки — задайте его в той же команде:
PF_DEFAULT_SERVER_URL=https://pocket.example.com pnpm dist:win:docker   # Linux/macOS (electronuserland/builder:wine)
# на Windows: PF_DEFAULT_SERVER_URL=https://pocket.example.com pnpm dist:win  (PowerShell: $env:PF_DEFAULT_SERVER_URL='…'; pnpm dist:win)
# → desktop/release/Pocket-Setup-<версия>.exe
sha256sum release/Pocket-Setup-*.exe
# необязательные проверки готовой сборки:
pnpm fuses:win                 # fuses release/win-unpacked/Pocket.exe → FUSES_OK (любая ОС с Node)
bash scripts/wine-smoke.sh     # только Linux с Docker и Xvfb: Pocket.exe под wine — старт, RUN_AS_NODE, подмена app.asar
```

Версия в имени файла берётся из `desktop/package.json`.

Установка: запустить `Pocket-Setup-<версия>.exe` → SmartScreen «Подробнее → Выполнить в любом
случае» (подписи нет). Если `.exe` собран без `PF_DEFAULT_SERVER_URL`, при первом запуске
приложение спросит адрес сервера (например `https://pocket.example.com`; API —
`<адрес>/api/v1`). Адрес меняется в настройках приложения (только `https://`, смена сервера =
новый вход). Вход — пароль один раз, дальше токен устройства хранится в защищённом хранилище
Windows (DPAPI); отозвать — в веб-версии «Настройки → Вход и устройства → Сессии и устройства».
CORS для Electron уже разрешён (`DESKTOP_ORIGIN=app://pocket`). Обновление приложения —
установить новый `.exe` поверх (вход и настройки сохраняются). Проверка на Windows —
`docs/WINDOWS_CHECKLIST.md`.

## 8. Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | `pocket` | префикс контейнеров и томов; после установки не менять |
| `COMPOSE_PROFILES` | — | `standalone` — поднимать edge-Caddy всегда |
| `PF_VERSION` | `1.0.0` | тег образов и `version` в health (ставит `update.sh`) |
| `APP_ENV` | `prod` | `prod` требует `SECRET_KEY` ≥ 32, `BOT_SERVICE_TOKEN` ≥ 24, https-адрес в `PUBLIC_URL` и настройки AI (или `AI_ENABLED=false`) — иначе api не стартует и пишет, чего не хватает |
| `LOG_LEVEL` | `INFO` | логи JSON в stdout (`docker compose logs`), включая uvicorn и миграции; запросы healthcheck не логируются |
| `PUBLIC_URL` | — (обязательно) | публичный https-адрес сайта; без него `docker compose` не запустится |
| `WEB_ORIGINS`, `DESKTOP_ORIGIN` | —, `app://pocket` | дополнительные origin'ы веба (origin из `PUBLIC_URL` разрешён всегда), origin Electron |
| `PF_HTTP_BIND`, `PF_HTTP_PORT` | `127.0.0.1`, `8080` | единственный опубликованный порт (web) |
| `TRUSTED_PROXIES` | `127.0.0.1/32` (никому) | чьим `X-Forwarded-For` верит `web` на опубликованном порту: точные адреса вашего reverse-proxy, CIDR через пробел (например `192.0.2.10/32`). Профилю `standalone` не нужен: edge-Caddy ходит на внутренний порт `web:8081` |
| `API_TRUSTED_PROXIES` | `auto` | чьим `X-Forwarded-For` верит `api` (через запятую); `auto` = compose-сеть (контейнер `web`); `*` запрещён |
| `PF_DOMAIN`, `ACME_EMAIL` | — | домен и e-mail для профиля `standalone` |
| `EDGE_BIND`, `EDGE_HTTP_PORT`, `EDGE_HTTPS_PORT` | `0.0.0.0`, `80`, `443` | порты edge-Caddy |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | `pocket`, `pocket`, — | база (пароль `[A-Za-z0-9_-]`) |
| `DATABASE_URL` | из `POSTGRES_*` | только для внешней БД |
| `DB_POOL_SIZE` | `10` | пул соединений API |
| `MIGRATE_ON_START` | `true` | `alembic upgrade head` при старте api |
| `SECRET_KEY` | — | HMAC токенов; смена разлогинит всех |
| `BOT_SERVICE_TOKEN` | — | общий токен бот ↔ API |
| `COOKIE_SECURE`, `SESSION_TTL_DAYS` | `true`, `90` | сессии веба |
| `WEBAUTHN_RP_ID`, `WEBAUTHN_RP_NAME`, `WEBAUTHN_ORIGINS` | домен из `PUBLIC_URL`, `Pocket`, origin из `PUBLIC_URL` | Face ID / passkeys |
| `AI_ENABLED` | `true` | `false` — без LLM, только встроенный правиловый парсер |
| `AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL` | — | любой OpenAI-совместимый API (OpenAI, OpenRouter, локальный Ollama/vLLM); в `prod` при `AI_ENABLED=true` обязательны все три |
| `AI_TIMEOUT`, `AI_MAX_CONCURRENCY` | `20`, `4` | таймаут и параллельность LLM |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_IDS` | —, — | бот; пустой список = бот не отвечает никому и уведомления не шлются |
| `TELEGRAM_BOT_USERNAME` | — | имя бота без `@`: веб показывает ссылку на бота |
| `BOT_API_BASE_URL`, `OUTBOX_POLL_SECONDS` | `http://api:8000/api/v1`, `5` | бот → API |
| `DEFAULT_TIMEZONE` | `Asia/Ho_Chi_Minh` | пояс по умолчанию |
| `COINGECKO_API_KEY`, `RATES_HTTP_TIMEOUT` | —, `5` | курсы |
| `WORKER_TICK_SECONDS` | `30` | тик планировщика |
| `PARSER_LEXICON_EXTRA` | — | путь (внутри контейнера api) к JSON с дополнительными счетами/алиасами парсера; файл монтируется через `docker-compose.override.yml` |
| `WEB_API_BASE_URL` | `/api/v1` | база API, вшиваемая в сборку веба |
| `RATE_LIMIT_*` | см. `.env.example` | лимиты входа и запросов (умолчания — `backend/app/core/security_config.py`) |
| `BACKUP_*` | см. `docs/BACKUP.md` | расписание, ротация, удалённая копия, префикс имён дампов |
| `PF_DEFAULT_SERVER_URL` | — | только сборка Electron (§7): адрес сервера по умолчанию, вшивается в `.exe`; пусто — приложение спросит адрес при первом запуске |

Списки (`WEB_ORIGINS`, `WEBAUTHN_ORIGINS`, `TELEGRAM_ALLOWED_IDS`) — через запятую.
После правки `.env`: `docker compose up -d` (пересоздаст изменившиеся сервисы).
`WEB_API_BASE_URL` вшивается при сборке: после его смены — `docker compose up -d --build web`.

## 9. Что где лежит

| Что | Где |
|---|---|
| База | том `<проект>_pgdata` |
| Сертификаты (standalone) | тома `<проект>_caddy_data`, `<проект>_caddy_config` |
| Бэкапы | `BACKUP_DIR` (по умолчанию `./infra/var/backups`) |
| SSH-ключ для удалённых бэкапов | `BACKUP_SSH_DIR` (по умолчанию `./infra/var/ssh`) |
| Логи | `docker compose logs <сервис>` (json-file, ротация 20 МБ × 5) |

## 10. Проверка и неполадки

```bash
docker compose ps                       # статусы и health
docker compose logs --tail 100 api      # миграции, ошибки старта
scripts/backup.sh status                # последний бэкап
```

- **api не становится healthy** — смотрите `docker compose logs api`: обычно неверный
  `SECRET_KEY`/`BOT_SERVICE_TOKEN` (в prod обязательны), ошибка миграции или база недоступна
  (healthcheck требует `"db": true`, состояние `degraded` = unhealthy). «База данных уже
  обновлена более новой версией» — откат кода без отката базы, см. `docs/UPDATE.md` «Откат».
- **502/503 на `/api`** — api перезапускается; web в это время отвечает JSON
  `service_unavailable`.
- **Face ID не предлагается** — проверьте HTTPS и `WEBAUTHN_RP_ID` = домен сайта.
- **Бот молчит** — `docker compose logs bot`; id отправителя должен быть в `TELEGRAM_ALLOWED_IDS`.
- **Проверить весь стек** на тестовой машине: `scripts/test-stack.sh` (поднимает копию на
  порту 8180 под проектом `pf-infra`, проверяет health, прокси, бэкап ↔ восстановление,
  HTTPS-профиль и всё убирает).
