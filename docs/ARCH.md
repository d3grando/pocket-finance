# Pocket Finance — архитектура и контракты (ARCH)

> Устройство системы и контракты между модулями. Контракт REST — `docs/API.md`, дизайн — `docs/DESIGN.md`,
> долги — `docs/DEBTS.md`. Изменения контрактов (схемы в `backend/app/schemas/`, модели, API.md) — только
> аддитивные: существующие поля **не ломать**.

## 0. Коротко
- Один backend (FastAPI) + один PostgreSQL. Все клиенты (web/PWA, Electron, Telegram-бот) ходят в `/api/v1`.
- Ввод текстом: **парсер → (needs_llm) → LLM → валидация → диспетчер → сервисы**. Интенты — одни и те же Pydantic-модели у парсера и LLM (`backend/app/schemas/intents.py`).
- Деньги — `Decimal`/`NUMERIC(28,8)`, курсы — `NUMERIC(30,12)`. В JSON деньги — **строки** (`"150000"`, `"3.5"`).
- Учёт по проводкам (`postings`): операция (`transactions`) + 0..2 проводки по счетам. Балансы = начальный остаток + сумма проводок.
- Отмена — через `action_log` (снимки до/после). История — `audit_logs`.
- Синхронизация — SSE «что-то изменилось» поверх PostgreSQL `LISTEN/NOTIFY` + перезапрос при фокусе.
- Фоновые задачи — отдельный процесс `worker` (тот же образ, `python -m app.worker`), одна копия (advisory lock).
- Бот — отдельный пакет `bot/`, long polling, ходит в backend по HTTP с сервисным токеном + `X-Telegram-User-Id`.

## 1. Стек и версии

### Backend (`backend/`, Python 3.12, менеджер `uv`)
| Пакет | Версия | Примечание |
|---|---|---|
| python | 3.12.x | `requires-python = ">=3.12,<3.13"` |
| fastapi | 0.162.0 | |
| uvicorn[standard] | 0.54.0 | 1 воркер на процесс |
| sqlalchemy[asyncio] | 2.1.6 | async ORM, `Mapped[]` |
| asyncpg | 0.32.0 | драйвер БД + LISTEN/NOTIFY |
| alembic | 1.20.1 | async env |
| pydantic | 2.14.1 | |
| pydantic-settings | 2.16.1 | env-конфиг |
| httpx | 0.28.1 | LLM, курсы |
| argon2-cffi | 25.1.0 | хеш пароля |
| webauthn | 3.0.1 | passkeys (py_webauthn) |
| sse-starlette | 3.6.0 | SSE |
| openpyxl | 3.1.5 | XLSX-экспорт |
| orjson | 3.13.0 | |
| tzdata | 2026.5 | |
| dev: pytest 9.1.1, pytest-asyncio 1.4.1, respx 0.23.1, ruff 0.17.3, mypy 2.4.0 | | |

Rate limiting — свой in-memory token bucket (один процесс API), без slowapi. Планировщик — свой asyncio-цикл, без APScheduler.

### Bot (`bot/`, Python 3.12, `uv`)
aiogram 3.31.0 (**требует pydantic < 2.14** → в боте pydantic 2.13.x; поэтому бот — отдельный пакет, а не часть backend), httpx 0.28.1, pydantic-settings 2.16.1; dev: pytest 9.1.1, pytest-asyncio 1.4.1, respx 0.23.1.

### Web (`web/`, pnpm, Node 22)
| Пакет | Версия |
|---|---|
| react / react-dom | 19.3.1 |
| typescript | 6.0.3 (**не 7.x**: typescript-eslint 8.72 поддерживает `<6.1`) |
| vite | 8.3.5 |
| @vitejs/plugin-react | 6.1.2 |
| tailwindcss / @tailwindcss/vite | 4.3.3 |
| shadcn (CLI) | 4.22.1, стиль new-york, Tailwind v4, `radix-ui` 1.8.1 |
| class-variance-authority 0.7.1, clsx 2.1.1, tailwind-merge 3.7.0, tw-animate-css 1.4.0 | |
| lucide-react | 1.62.0 |
| @tanstack/react-query | 5.105.0 |
| react-router | 8.5.0 (data mode: `createBrowserRouter`) |
| recharts | 3.10.1 |
| vite-plugin-pwa | 2.0.0 (+ workbox-window 7.4.1) |
| sonner 2.0.8, cmdk 1.1.1, vaul 1.1.2, date-fns 4.4.0, zod 4.6.7 | |
| @simplewebauthn/browser | 14.0.0 |
| @fontsource-variable/inter 5.3.0, @fontsource-variable/jetbrains-mono 5.3.0 | |
| dev: vitest 5.0.5, @testing-library/react 16.3.3, jsdom 30.1.3, @playwright/test 1.64.1, eslint 10.13.0, typescript-eslint 8.72.1, @types/node 22.20.5 | |

### Desktop (`desktop/`, pnpm)
electron **44.10.0**, electron-builder 26.18.0, electron-vite 6.0.0, typescript 6.0.3. Токен устройства — `safeStorage` Electron (DPAPI на Windows), без keytar. Фронт — общий `web/` в режиме Quick Capture (`/quick`), собранный в `desktop/renderer/` и отдаваемый через протокол `app://pocket`.

### Infra
Docker Compose, PostgreSQL 16, Caddy (профиль `standalone` для голого VPS; иначе HTTPS делает ваш reverse-proxy). Образ backend (`api` и `worker` — один образ), образ bot, статика web раздаётся Caddy в контейнере `web` (или через `api`, см. INSTALL).

## 2. Раскладка монорепо
```
backend/                 FastAPI app (пакет app), alembic, tests, bench
  app/
    config.py db.py main.py worker.py
    core/ errors.py money.py timeutil.py principal.py
    core/security*.py
    api/router.py api/deps.py
    api/routes/*.py
    schemas/common.py intents.py assistant.py
    schemas/<area>.py
    models/**
    services/<area>/**
    middleware/**
    cli/**
  alembic/
  tests/conftest.py
  bench/
bot/        aiogram 3 (пакет pocket_bot)
web/        React SPA + PWA + Quick Capture
desktop/    Electron
infra/ docker-compose.yml .env.example scripts/ .github/ README.md docs/
```

## 3. Модель данных (PostgreSQL 16)

Общее: все таблицы с `user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE` (кроме `users`, `currency_rates`). `created_at/updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`. Деньги `NUMERIC(28,8)`, курсы `NUMERIC(30,12)`. Коды валют/активов — `VARCHAR(16)` в верхнем регистре (`VND`, `USD`, `RUB`, `USDT`, `TON`, `BTC`…). Enum'ы — `VARCHAR` + `CHECK` (проще миграции), значения в `app/models/enums.py`. Расширения: `pg_trgm`, `citext`.
Точное определение — `backend/app/models/*.py` (ORM = схема; Alembic autogenerate от них).

### 3.1 Пользователь и вход
**users**: `id BIGINT IDENTITY PK`, `username CITEXT UNIQUE NOT NULL` (по умолчанию `owner`), `password_hash TEXT NULL` (argon2id), `is_active BOOL DEFAULT true`, `created_at`, `updated_at`.

**user_settings** (1:1): `user_id PK FK`, `timezone VARCHAR(64) DEFAULT 'Asia/Ho_Chi_Minh'`, `default_currency VARCHAR(16) DEFAULT 'VND'`, `analytics_currency VARCHAR(16) DEFAULT 'USD'`, `summary_currency VARCHAR(16) DEFAULT 'VND'` (плитки/бот/мини-окно), `small_amount_thousands BOOL DEFAULT true` («кофе 85» → 85 000 ₫), `theme VARCHAR(8) DEFAULT 'system'` (`light|dark|system`), `desktop_hotkey VARCHAR(64) DEFAULT 'Ctrl+Alt+Space'`, `telegram_user_id BIGINT UNIQUE NULL`, `report_monthly BOOL DEFAULT true`, `report_daily BOOL DEFAULT false`, `report_weekly BOOL DEFAULT false`, `report_time TIME DEFAULT '10:00'` (месячный), `report_daily_time TIME DEFAULT '22:00'`, `report_weekly_day SMALLINT DEFAULT 6` (0=Пн…6=Вс, CHECK 0..6), `report_weekly_time TIME DEFAULT '21:00'`, `reminders_enabled BOOL DEFAULT true` (миграция 0003), `debt_reminders_enabled BOOL DEFAULT false`, `debt_reminder_days SMALLINT DEFAULT 30` (CHECK 1..365, миграция 0005), `onboarding_done BOOL DEFAULT false`, `version INT DEFAULT 1`, `updated_at`.

**sessions** (веб/PWA): `id UUID PK`, `user_id`, `token_hash BYTEA UNIQUE NOT NULL` (HMAC-SHA256(SECRET_KEY, token)), `csrf_hash BYTEA NOT NULL`, `user_agent TEXT`, `ip INET NULL`, `created_at`, `last_seen_at`, `expires_at TIMESTAMPTZ NOT NULL` (+90 дней, скользящее продление раз в сутки), `revoked_at NULL`. Индекс `(user_id) WHERE revoked_at IS NULL`.

**device_tokens** (Electron): `id UUID PK`, `user_id`, `name VARCHAR(100)`, `platform VARCHAR(16)` (`windows|macos|linux`), `token_hash BYTEA UNIQUE`, `created_at`, `last_used_at NULL`, `last_ip INET NULL`, `revoked_at NULL`. Без срока жизни, отзыв в настройках.

**passkeys**: `id UUID PK`, `user_id`, `credential_id BYTEA UNIQUE`, `public_key BYTEA`, `sign_count BIGINT DEFAULT 0`, `transports TEXT[] DEFAULT '{}'`, `aaguid VARCHAR(36) NULL`, `name VARCHAR(100)`, `created_at`, `last_used_at NULL`.

**auth_challenges** (WebAuthn): `id UUID PK`, `user_id NULL`, `purpose VARCHAR(16)` (`register|login`), `challenge BYTEA`, `expires_at` (+5 мин), `used_at NULL`.

### 3.2 Счета
**accounts**: `id BIGINT PK`, `user_id`, `name VARCHAR(64)`, `kind VARCHAR(16)` (`bank|cash|card|crypto_wallet|exchange|other|debt`; `debt` — служебный счёт человека, DEBTS.md, скрыт из списков/резолва/итогов), `currency VARCHAR(16) NULL` — валюта счёта для одновалютных; `NULL` при `is_multi_asset`, `is_multi_asset BOOL DEFAULT false` (кошелёк держит любые монеты), `aliases TEXT[] DEFAULT '{}'` (в нижнем регистре), `default_for_currencies TEXT[] DEFAULT '{}'` (₫→Карта, ₽→Карта RUB; одна валюта не может быть дефолтной у двух счетов — проверяет сервис), `color VARCHAR(16) NULL`, `icon VARCHAR(32) NULL`, `sort_order INT DEFAULT 0`, `is_archived BOOL DEFAULT false`, `version INT DEFAULT 1`, `created_at`, `updated_at`, `deleted_at NULL`.
UNIQUE `(user_id, lower(name)) WHERE deleted_at IS NULL`; GIN `(aliases)`; CHECK `is_multi_asset OR currency IS NOT NULL`.

**account_assets** (баланс по монете/валюте на счёте): `id BIGINT PK`, `user_id`, `account_id FK CASCADE`, `asset VARCHAR(16)`, `opening_balance NUMERIC(28,8) NULL` — `NULL` = остаток не задан → баланс не показывается, `opening_at TIMESTAMPTZ NULL` (момент, на который задан остаток), `created_at`, `updated_at`. UNIQUE `(account_id, asset)`.
Одновалютный счёт имеет ровно одну строку `asset = currency`. Мультиактивный — строка на каждую монету, создаётся при первом упоминании (`accounts.ensure_asset`).

**debts** (0005, DEBTS.md §3): человек-контрагент долга 1:1 со служебным счётом `kind='debt'` (мультиактивный, `opening_balance=0` по каждой валюте): `id`, `user_id`, `account_id UNIQUE FK CASCADE`, `name`, `name_key` (`app/core/person_names.name_key`), `aliases TEXT[]`, `handle CITEXT NULL`, `note`, `is_archived`, `remind_snoozed_until DATE NULL`, `last_reminded_on DATE NULL`, `version`. Баланс долга = баланс счёта: `> 0` — должны мне, `< 0` — должен я.

**my_accounts** («мои аккаунты» для переводов на @ник): `id BIGINT PK`, `user_id`, `handle CITEXT` (без `@`), `label VARCHAR(64)`, `account_id FK accounts` (кошелёк этого аккаунта, создаётся автоматически мультиактивным), `created_at`. UNIQUE `(user_id, handle)`. По умолчанию пусто.

### 3.3 Категории и продавцы
**categories**: `id BIGINT PK`, `user_id`, `name VARCHAR(64)`, `kind VARCHAR(8)` (`expense|income`), `is_business BOOL DEFAULT false`, `icon VARCHAR(32) NULL` (имя lucide), `color VARCHAR(16) NULL`, `aliases TEXT[] DEFAULT '{}'` (нижний регистр: `еда, food, обед…`), `is_system BOOL DEFAULT false` (из стартового набора), `is_new BOOL DEFAULT false` (🆕 — создана моделью, не подтверждена), `merged_into_id FK categories NULL`, `sort_order INT`, `is_archived BOOL DEFAULT false`, `version INT`, `created_at`, `updated_at`.
UNIQUE `(user_id, kind, lower(name)) WHERE merged_into_id IS NULL`. GIN `(aliases)`.
Стартовый набор (сид `services/categories/seed.py`):
- расход, личное: Еда, Продукты, Кофе, Такси/транспорт, Жильё, Связь, Подписки, Здоровье, Покупки, Развлечения, Подарки, Переводы людям, Прочее;
- расход, бизнес: Серверы/инфра, Софт и AI, Реклама, Подрядчики, Комиссии/обмен, Закупка товара;
- доход: Клиенты (бизнес), Продажи (бизнес), Зарплата (личное), Прочее (личное).
  (Допущение: «Клиенты» — бизнес-доход; меняется в настройках.)

**merchants** (правила продавцов/получателей): `id BIGINT PK`, `user_id`, `name VARCHAR(128)` (канон: `vast.ai`), `aliases TEXT[]` (`васт, vast`), `handle CITEXT NULL` (`alice` для переводов на @ник), `category_id FK categories NULL`, `default_account_id FK accounts NULL`, `hits INT DEFAULT 0`, `last_used_at NULL`, `created_at`, `updated_at`. UNIQUE `(user_id, lower(name))`; UNIQUE `(user_id, handle) WHERE handle IS NOT NULL`; GIN `(aliases)`.
Правило обучается: при создании операции с продавцом — `hits+1`; при смене категории операции с продавцом — `category_id` правила = новая категория.

### 3.4 Операции и проводки
**transactions** — одна запись = одна пользовательская операция:
| колонка | тип | смысл |
|---|---|---|
| id | BIGINT PK | |
| user_id | FK | |
| kind | VARCHAR(12) | `expense|income|refund|transfer|exchange|adjustment` |
| status | VARCHAR(16) | `posted` (обычная), `needs_account` (ждёт «С какого?») |
| occurred_at | TIMESTAMPTZ | момент операции (для «вчера» без времени — 12:00 локального) |
| occurred_on | DATE | локальная дата в поясе пользователя (группировки) |
| amount | NUMERIC(28,8) > 0 | исходная сумма, как написал пользователь |
| currency | VARCHAR(16) | исходная валюта/монета |
| account_id | FK accounts NULL | счёт списания/зачисления (для transfer/exchange — источник). `NULL`: `needs_account` или «без счёта» |
| account_amount | NUMERIC(28,8) NULL | сумма в активе счёта (> 0) |
| account_asset | VARCHAR(16) NULL | актив счёта, в котором проведено |
| account_rate | NUMERIC(30,12) NULL | курс currency→account_asset (1 при совпадении) |
| is_estimated | BOOL DEFAULT false | списание посчитано по курсу (≈), ещё не подтверждено фактом |
| counter_account_id | FK accounts NULL | счёт-получатель (transfer/exchange) |
| counter_amount | NUMERIC(28,8) NULL | сумма зачисления (exchange — фактическая) |
| counter_asset | VARCHAR(16) NULL | |
| category_id | FK categories NULL | обязательна для expense/income/refund |
| is_business | BOOL DEFAULT false | копия `categories.is_business` (поддерживает сервис категорий) |
| merchant_id | FK merchants NULL | |
| counterparty | VARCHAR(128) NULL | получатель/плательщик (`@bob`, «клиент») |
| description | TEXT NULL | назначение («аккаунт GPT Pro») |
| debt_op | VARCHAR(12) NULL | `lend|repay_in|borrow|repay_out|forgive` — долговая операция (0005, DEBTS.md §3.1), у прочих NULL |
| amount_usd | NUMERIC(28,8) NULL | сумма в USD на дату операции (NULL — курса пока нет) |
| amount_vnd | NUMERIC(28,8) NULL | сумма в VND на дату операции |
| usd_rate | NUMERIC(30,12) NULL | курс currency→USD на дату |
| rate_date | DATE NULL | дата применённого курса |
| rate_source | VARCHAR(16) NULL | `vietcombank|cbr|coingecko|open_er_api|manual|identity` |
| refund_of_id | FK transactions NULL | для `refund` (необязательно) |
| recurring_id | FK recurring_transactions NULL | создана из напоминания |
| source | VARCHAR(12) | `web|desktop|telegram|system` |
| raw_text | TEXT NULL | исходный текст ввода |
| ai_request_id | FK ai_requests NULL | |
| created_action_id | UUID NULL | действие, создавшее запись |
| version | INT DEFAULT 1 | оптимистичная блокировка |
| created_at, updated_at | | |
| deleted_at | TIMESTAMPTZ NULL | мягкое удаление |

CHECK: `amount > 0`; `kind IN (...)`; `status='needs_account' → account_id IS NULL OR debt_op IN ('repay_in','borrow')` (у долгового притока источник — счёт-долг, ждём получателя); `kind IN ('expense','income','refund') = (category_id IS NOT NULL)` не жёсткий — проверяет сервис; `status='needs_account' → account_id IS NULL`.
Индексы: `(user_id, occurred_on DESC, id DESC) WHERE deleted_at IS NULL`; `(user_id, category_id, occurred_on)`; `(user_id, account_id)`; `(user_id, status) WHERE status='needs_account' AND deleted_at IS NULL`; `(user_id, kind, occurred_on)`; GIN trgm `(coalesce(description,'') || ' ' || coalesce(counterparty,'') || ' ' || coalesce(raw_text,''))`.

**postings** — движения по счетам (только для учёта остатков): `id BIGINT PK`, `user_id`, `transaction_id FK CASCADE`, `account_id FK`, `asset VARCHAR(16)`, `amount NUMERIC(28,8)` (со знаком, ≠ 0), `occurred_at TIMESTAMPTZ` (копия). Индексы `(account_id, asset, occurred_at)`, `(transaction_id)`.
Проводки **перестраиваются целиком** (delete + insert) сервисом ledger при каждом создании/изменении/удалении/восстановлении операции. У удалённой (`deleted_at`) и `needs_account` проводок нет.

| kind | проводки |
|---|---|
| expense | `account: −account_amount account_asset` (если есть счёт) |
| income | `account: +account_amount` |
| refund | `account: +account_amount` |
| transfer | `account: −amount currency`, `counter_account: +counter_amount counter_asset` (= amount/currency) |
| exchange | `account: −amount currency`, `counter_account: +counter_amount counter_asset` (фактический курс = counter_amount/amount) |
| adjustment | `account: account_amount` (со знаком) |

Корректировка: `amount` > 0 всегда (модуль), знак разницы хранится в `account_amount` — единственная колонка, которая может быть отрицательной (только у `adjustment`).

### 3.5 Курсы
**currency_rates**: `id BIGINT PK`, `base VARCHAR(16)`, `quote VARCHAR(16)`, `rate NUMERIC(30,12) > 0` (1 base = rate quote), `rate_date DATE`, `kind VARCHAR(8)` (`mid|sell|buy`), `source VARCHAR(16)`, `fetched_at TIMESTAMPTZ`. UNIQUE `(base, quote, rate_date, kind, source)`. Индекс `(base, quote, rate_date DESC)`.
Источники: VND — Vietcombank (USD/VND `sell` — для списаний и сводок), RUB — ЦБ РФ (USD/RUB), крипта — CoinGecko (X/USD), запасной — open.er-api (фиат). USD — пивот. Нет курса на дату → ближайший предыдущий (≤ 7 дней), иначе последний известный + `is_estimated`; нет вообще → `amount_usd/vnd = NULL`, воркер дозаполняет (`backfill_missing_rates`).

### 3.6 AI, аудит, отмена, служебное
**ai_requests** (журнал разборов): `id BIGINT PK`, `user_id`, `source`, `input_text TEXT`, `route VARCHAR(12)` (`parser|llm|parser_llm|none`), `model VARCHAR(64) NULL`, `status VARCHAR(16)` (`ok|invalid|error|timeout|unavailable|clarify`), `parser_result JSONB NULL`, `llm_request JSONB NULL` (без секретов), `llm_response JSONB NULL`, `intents JSONB NULL` (после валидации), `error TEXT NULL`, `prompt_tokens INT NULL`, `completion_tokens INT NULL`, `latency_ms INT NULL`, `created_at`. Индекс `(user_id, created_at DESC)`. Хранить 180 дней (чистит воркер).

**audit_logs** (история изменений): `id BIGINT PK`, `user_id`, `entity_type VARCHAR(32)` (`transaction|account|account_asset|category|merchant|my_account|settings|recurring`), `entity_id VARCHAR(64)`, `operation VARCHAR(16)` (`create|update|delete|restore|merge`), `before JSONB NULL`, `after JSONB NULL`, `action_id UUID NULL FK action_log`, `source VARCHAR(12)`, `created_at`. Индекс `(user_id, entity_type, entity_id, created_at)`.

**action_log** (единица отмены и контекст диалога): `id UUID PK`, `user_id`, `source`, `kind VARCHAR(32)` (`create_transaction|update_transaction|delete_transaction|bulk_update|bulk_delete|transfer|exchange|adjust_balance|assign_account|set_opening|create_category|merge_categories|restore_transaction|debt|undo`), `summary TEXT` (рус., «Еда 150 000 ₫»), `input_text TEXT NULL`, `entity_refs JSONB` (`[{"type":"transaction","id":123}]`), `undo_data JSONB` (`[{"type","id","before","after_version"}]` — снимки «до», `before=null` для созданных), `is_undoable BOOL DEFAULT true`, `undone_at NULL`, `undone_by_action_id UUID NULL`, `ai_request_id NULL`, `idempotency_key VARCHAR NULL`, `request_hash BYTEA NULL`, `response_status INT NULL`, `response JSONB NULL` (идемпотентность действий ledger, `ledger.idempotency.run_idempotent` читает `idem.key`), `created_at`. Индексы `(user_id, created_at DESC)`, UNIQUE `uq_action_log_user_idem_key (user_id, idempotency_key)`.
FK `transactions`/`postings` → `accounts`/`categories` — `NO ACTION` (не `RESTRICT`); `my_accounts.account_id` — `ON DELETE CASCADE`.
Контекст диалога = `SELECT … WHERE user_id=? AND created_at > now()-30min AND undone_at IS NULL ORDER BY created_at DESC LIMIT 10` — общий для всех клиентов.

**idempotency_keys**: PK `(user_id, key)`, `key VARCHAR(128)`, `method VARCHAR(8)`, `path VARCHAR(256)`, `request_hash BYTEA` (sha256 тела), `state VARCHAR(12)` (`in_progress|done`), `response_status INT NULL`, `response_body JSONB NULL`, `created_at`, `expires_at` (+48 ч). Чистит воркер.

**preview_tokens** (массовые изменения, уточнения): `id UUID PK` (это и есть токен), `user_id`, `kind VARCHAR(16)` (`bulk_update|bulk_delete`), `intent JSONB` (провалидированный интент), `affected JSONB` (`[{"id":1,"version":3}]`), `summary TEXT`, `action_source`, `expires_at` (+10 мин), `consumed_at NULL`, `result_action_id UUID NULL`, `created_at`.

**recurring_transactions** (только напоминания): `id BIGINT PK`, `user_id`, `title VARCHAR(128)`, `kind` (`expense|income`), `amount NUMERIC(28,8) NULL`, `currency VARCHAR(16)`, `account_id NULL`, `category_id NULL`, `merchant_id NULL`, `description TEXT NULL`, `frequency VARCHAR(8)` (`daily|weekly|monthly|yearly`), `interval INT DEFAULT 1`, `day_of_month SMALLINT NULL` (1–31; 31 = последний день), `weekday SMALLINT NULL` (0=пн), `month SMALLINT NULL`, `next_due_on DATE`, `remind_time TIME DEFAULT '10:00'`, `is_active BOOL DEFAULT true`, `last_reminded_on DATE NULL`, `version`, `created_at`, `updated_at`.

**notifications_outbox** (для бота): `id BIGINT PK`, `user_id`, `channel VARCHAR(16) DEFAULT 'telegram'`, `kind VARCHAR(24)` (`report_monthly|report_daily|report_weekly|recurring_reminder|debt_reminder|info`), `payload JSONB` (`{"text": "...HTML...", "buttons": [[{"text","callback"}]]}`), `dedupe_key VARCHAR(128) UNIQUE NULL` (`report_monthly:2026-10`), `status VARCHAR(12)` (`pending|sending|sent|failed`), `attempts INT DEFAULT 0`, `available_at`, `locked_until NULL`, `sent_at NULL`, `error TEXT NULL`, `created_at`. Индекс `(status, available_at)`.

## 4. Семантика денег (обязательна для ledger, stats, AI)
1. **Расход** = Σ `expense` − Σ `refund` (по той же категории). **Доход** = Σ `income`. **Денежный поток** = доход − расход.
2. `transfer`, `exchange`, `adjustment` **никогда** не входят в доходы/расходы. Перевод на чужой `@ник` — это **`expense`** с `counterparty` (не transfer). Перевод на ник из `my_accounts` — `transfer` на привязанный кошелёк.
3. Изменение цены монеты — не доход/расход (нет переоценки). Балансы крипты показываются в монетах; ≈$ — по текущему курсу, только для отображения.
4. В статистику входят операции `deleted_at IS NULL` и `status IN ('posted','needs_account')`. `needs_account` — в расходах, но **не в остатках** (нет проводок). На главной — значок «разобрать: N» (`GET /transactions/needs-account/count`).
5. Сумма для сводок: `amount_vnd` (₫, плитки/бот/мини-окно) и `amount_usd` (аналитика, переключатель ₫/$). Считаются **при записи** по курсу на `occurred_on`. Пересчитываются при изменении суммы/валюты/даты. Курс на дату пишется в операцию (`usd_rate`, `rate_date`, `rate_source`). Если списание подтверждено в ₫ (счёт в VND, `is_estimated=false`), `amount_vnd = account_amount` (фактическое списание, не пересчёт по курсу); `backfill_missing` это значение не трогает.
6. **«Без счёта»** — `status='posted'`, `account_id IS NULL` (пользователь явно выбрал «без счёта»): в статистике есть, проводок нет.
7. **Выбор счёта**: явный в тексте → он; иначе `₫ → счёт с default_for_currencies∋VND` (Карта), `₽ → Карта RUB`; продавец с `default_account_id` → он; крипта/USD/USDT без счёта → `needs_account` + карточка «С какого?» с кнопками счетов (мультиактивные + счета в этой валюте + «Без счёта»).
8. **Списание в активе счёта**: если счёт мультиактивный → `account_asset = currency`, `account_amount = amount` (монета добавляется в кошелёк `ensure_asset`). Если одновалютный и валюта совпала → 1:1. Иначе → `account_amount = amount × курс(currency→account.currency; для ₫-счёта: расход — sell VCB, доход — buy VCB)`, `is_estimated = true` («≈»). Монету (крипту) нельзя списать с одновалютного счёта (Карта, Наличные, Карта RUB) → 422; для неё предлагаются только мультиактивные кошельки и счета в этой валюте. Фраза «васт списали 21.3м» → `UpdateTransaction` с `changes.account_amount=21300000` по последней `is_estimated` операции продавца → `is_estimated=false`, `account_rate = account_amount/amount`. Комиссия 0%.
9. **Балансы**: только для `account_assets.opening_balance IS NOT NULL`: `balance = opening_balance + Σ postings.amount WHERE occurred_at >= opening_at`. Иначе `balance = null` («остаток не задан»). Остаток в ₫/$ ≈ — только отображение.
10. **Сверка** «на карте 5.2м» (`AdjustBalance`): если остаток задан → операция `adjustment` на разницу (если разница 0 — ничего не пишем, ответ «сходится»); если не задан → `opening_balance` = указанная сумма, `opening_at = now()` (действие `set_opening`, отменяемо).
11. **Возврат** («вернули 200к за наушники») = `refund` той же категории (по продавцу/описанию/последней похожей). **Долги** (с 0005) — `docs/DEBTS.md`: займы/возвраты = `transfer` между реальным счётом и служебным счётом человека (не доход/расход), прощение = расход «Переводы людям» / доход «Прочее».
12. Точность: фиат VND — 0 знаков при отображении, USD/RUB/USDT — 2, крипта — до 8 значащих. Хранение — полная точность `NUMERIC(28,8)`, округление `ROUND_HALF_EVEN` только при выводе и при пересчёте в `account_amount` (до точности актива счёта: VND 0, фиат 2, крипта 8). Утилиты — `app/core/money.py`.
13. Валюты: `USD ≠ USDT`. Голое число < 1000 при валюте VND = тысячи (`small_amount_thousands`). `к/k` = ×1000, `м/m/млн/kk` = ×1 000 000. «баксов/долларов/$» → USD, «руб/₽» → RUB, «тон/тона/тонов» → TON, «юсдт/usdt» → USDT. Неоднозначная валюта (вне настроек) → не угадываем, `clarify`.

## 5. Конвейер ввода
```
POST /api/v1/assistant/messages  (text, Idempotency-Key)
  └─ services/ai/pipeline.handle_message(session, principal, req)
       1. ctx = ai/context.build_parse_context(...)     # счета/алиасы/категории/продавцы/my_accounts/today/tz + последние 10 действий за 30 мин
       2. pr = parser.parse(text, ctx) -> ParseResult    # чисто, без БД, < 5 мс
       3. if pr.needs_llm: llm = ai/client.extract_intents(text, ctx_for_llm) -> LLMResult
              (в LLM уходит: текст, сегодняшняя дата/пояс, имена счетов/категорий/алиасы, краткий контекст последних действий
               — суммы последних действий только своих операций в контексте; НЕ уходят балансы, история, SQL)
          AI недоступен/таймаут → если pr.intents частично есть и pr.partial_ok → выполнить их, иначе карточка ai_unavailable
       4. vi = ai/validator.validate(intents, ctx) -> list[ValidatedIntent] | ValidationFailure
              (пересчёт сумм из amount_raw парсером, валюты, даты не в будущем > 1 дня, ссылки на счета/категории/цели существуют)
       5. resp = ai/dispatcher.dispatch(session, principal, vi, meta) -> AssistantResponse  # одна транзакция БД на всё сообщение
       6. запись ai_requests; commit; NOTIFY
```
- Интенты: `backend/app/schemas/intents.py` (discriminated union по `type`). Парсер и LLM возвращают **одни и те же** модели (`IntentList`). LLM отдаёт JSON по JSON Schema `IntentList.model_json_schema()` (OpenAI `response_format: json_schema`, при неподдержке — `json_object` + валидация Pydantic, 1 повтор с ошибкой).
- LLM **не считает** суммы: возвращает `amount_raw` («600к») и нормированное `amount`; валидатор пересчитывает `amount` из `amount_raw` функцией парсера `parse_amount`, при расхождении берётся результат парсера.
- Атомарность: сообщение из нескольких интентов исполняется в одной транзакции БД. Ошибка проверки любого → **ничего не записано**, карточка `error` с причиной.
- Диспетчер вызывает только функции сервисов (§7). Никакого SQL от модели.
- Если парсер уверен (`needs_llm=false`) — LLM не вызывается вообще.
- Таймаут LLM: `AI_TIMEOUT` (по умолчанию 20 с). Не более `AI_MAX_CONCURRENCY` (4) одновременных вызовов.

### 5.1 Правила подтверждений
- Создание операций, правка/удаление **последней** (цель `last*`, `context`, `id` из кнопки «Изменить») — сразу; в карточке `[Отменить] [Изменить]`.
- Затрагивает > 1 существующей записи **или** цель найдена поиском (`target.kind = "search"`) → **превью** (`preview_tokens`, 10 мин) → `POST /assistant/previews/{token}/confirm` (Enter). Если версии записей изменились — 409 `preview_stale`, клиент просит повторить.
- Создание категории моделью — сразу, флаг `is_new` (🆕) + кнопка «в другую».
- Отмена: `POST /actions/{action_id}/undo` откатывает действие целиком (созданные → мягко удалены, изменённые → снимок «до», удалённые → восстановлены). Если запись изменена после действия (version ≠ after_version) → 409 `undo_conflict`. Повторная отмена → 200 с тем же результатом (идемпотентно).

### 5.2 Идемпотентность
- Заголовок `Idempotency-Key` (UUID v4/≤128 символов) **обязателен** на: `POST /assistant/messages`, `POST /assistant/previews/{t}/confirm`, `POST /transactions`, `POST /transfers`, `POST /exchanges`, `POST /adjustments`, `POST /actions/{id}/undo`, `POST /recurring/{id}/record`. На остальных мутациях — по желанию.
- Реализация: `app/middleware/idempotency.py` — FastAPI-зависимость `idempotent()`: `INSERT … ON CONFLICT DO NOTHING` в `idempotency_keys` (`in_progress`) в **отдельной** транзакции; тот же ключ + тот же хеш тела → вернуть сохранённый ответ (или 409 `idempotency_in_progress`, если ещё выполняется); другой хеш → 409 `idempotency_mismatch`. После успеха — сохранить ответ (`done`). Ошибки 5xx ключ удаляют (повтор разрешён).
- Клиенты: один ключ на одну попытку пользователя (Enter); повтор после обрыва — **тот же** ключ. Бот: ключ `tg:{chat_id}:{message_id}`, для кнопок `tgcb:{callback_id}`, для кнопок вариантов `t:{n}` — `tgt:{chat_id}:{message_id}:{n}`.

## 6. Аутентификация и безопасность
Принципал (`app/core/principal.py`): `Principal(user_id, kind: session|device|bot, source: web|desktop|telegram, session_id|device_id, telegram_user_id)`.
- **Web/PWA**: `POST /auth/login {password}` → куки `pf_session` (HttpOnly, Secure, SameSite=Lax, Path=/, Max-Age 90 дней) + `pf_csrf` (не HttpOnly, SameSite=Strict). Все мутации с куки-сессией требуют заголовок `X-CSRF-Token` = значение `pf_csrf` (сверяется с `sessions.csrf_hash`) → иначе 403 `csrf_failed`. Скользящее продление: `last_seen_at`/`expires_at` обновляются не чаще раза в сутки.
- **Passkeys (Face ID)**: WebAuthn, RP ID = `WEBAUTHN_RP_ID` (`pocket.example.com`), `userVerification=required`, resident key preferred. Регистрация — только из активной сессии. Вход паскеем выдаёт ту же сессию, что и пароль.
- **Electron**: `POST /auth/device-login {password, device_name, platform}` → `{token}` (32 байта, base64url, префикс `pfd_`), хранится в `safeStorage`. Запросы: `Authorization: Bearer pfd_…`. CSRF не нужен. CORS разрешает origin `app://pocket` (`DESKTOP_ORIGIN`). Отзыв — `DELETE /devices/{id}`.
- **Бот**: `Authorization: Bearer <BOT_SERVICE_TOKEN>` + `X-Telegram-User-Id: <id>`. Backend: токен совпал (constant-time) **и** id ∈ `TELEGRAM_ALLOWED_IDS` **и** есть `user_settings.telegram_user_id = id` (при первом обращении владельца — привязывается к единственному пользователю, если поле пусто). Иначе 403. Бот сам дополнительно отбрасывает чужие апдейты. Webhook не используется (long polling) — «проверка подлинности webhook» не применима; если включат webhook — `X-Telegram-Bot-Api-Secret-Token`.
- Токены в БД — только `HMAC-SHA256(SECRET_KEY, token)`. Пароль — argon2id. CLI: `python -m app.cli set-password` (печатает сгенерированный пароль один раз).
- **Rate limiting** (`app/middleware/ratelimit.py`, in-memory token bucket): `/auth/login*` и `/auth/device-login` — 5/мин на IP и 20/час глобально; `/assistant/messages` — 60/мин на принципала и на пользователя (`RATE_LIMIT_AI_PER_MINUTE`); неверные пароли в `/users/me/password`, `/users/me/wipe`, `/passkeys/register/options` — 20/час на пользователя (`RATE_LIMIT_LOGIN_FAILURES_PER_HOUR`); прочее — 300/мин на принципала. 429 + `Retry-After`.
- Заголовки (middleware `security_headers`): `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `X-Frame-Options: DENY`. CSP для статики — в Caddy (infra).
- `X-Request-Id` на каждый ответ (middleware `request_id`), логи JSON в stdout.
- Секреты только в env. В web не попадает ничего, кроме публичного URL API.

## 7. Сервисный слой — контракты между модулями
Все функции async, первый аргумент `session: AsyncSession`, не коммитят (коммит — в роуте/пайплайне через `get_session` / явный `session.commit()`), бросают `AppError` из `app/core/errors.py`. Сигнатуры и docstring — в коде модулей; кратко:

**Учёт** (`services/ledger`, `accounts`, `categories`, `merchants`):
- `ledger.transactions.create_transaction(session, principal, data: TransactionCreate, *, raw_text=None, ai_request_id=None, action=None) -> Transaction` (ORM-объект; модель API — `ledger.transactions.to_out(session, tx)`). С переданным `action` ledger только вызывает `action.record(...)` и ставит `created_action_id`, а `finish_action` делает вызывающий (assistant). `adjust_balance(..., action=ctx)` с результатом `matches` ничего не пишет — вызывающий делает `ledger.actions.discard_action(session, ctx)`.
- `ledger.bulk.create_preview(session, principal, intent, matches, *, patch=None)` — для update ссылки уже разрешены в id (`patch={"category_id": …}`), без них → 422. `ledger.query.last_estimated(session, user_id, merchant_id=…)` — цель «васт списали 21.3м» (применяется сразу, без превью). `search_filter_to_filter` переводит `merchant_ref` в `merchant_id` через `ctx.merchants`. `categories.service.create_category(..., is_new=True, action=None)` — 409 `already_exists` на дубль. `ledger.wipe.wipe_financial_data(session, user_id)` / `delete_user(session, user_id)`.
- `ledger.transactions.update_transaction(session, principal, tx_id, patch: TransactionPatch, *, action=None) -> TransactionOut` (проверка `version`, пересчёт курсов/проводок, обучение продавца)
- `ledger.transactions.delete_transaction(...)`, `restore_transaction(...)`, `assign_account(session, principal, tx_id, account_id|None) -> TransactionOut`
- `ledger.transfers.create_transfer(session, principal, data: TransferCreate, ...)`, `create_exchange(...)`, `adjust_balance(session, principal, data: AdjustmentCreate, ...) -> AdjustmentResult`
- `ledger.query.list_transactions(session, user_id, f: TransactionFilter) -> TransactionPage`, `get_transaction(...)`, `resolve_target(session, user_id, target: TargetSpec, ctx) -> list[Transaction]`
- `ledger.actions.begin_action(session, principal, kind, input_text=None) -> ActionContext`, `ActionContext.record(entity, before, after)`, `undo_action(session, principal, action_id) -> UndoResult`, `recent_actions(session, user_id, limit=10, minutes=30) -> list[ActionBrief]`
- `ledger.bulk.create_preview(session, principal, intent, matches) -> PreviewCard`, `confirm_preview(session, principal, token) -> AssistantResponse`
- `ledger.audit.write_audit(session, ...)`; `ledger.export.export_transactions(session, user_id, f, fmt)`, `export_all_json(...)`
- `accounts.service.resolve_account(session, user_id, ref: str) -> Account | None`, `default_account_for(session, user_id, currency) -> Account | None`, `ensure_asset(session, account, asset) -> AccountAsset`, `list_accounts`, `create/update/archive`, `set_opening_balance(...)`; `accounts.balances.get_balances(session, user_id) -> list[AccountBalanceOut]`
- `categories.service.resolve_category(session, user_id, ref, kind) -> CategoryMatch` (точное имя/алиас → похожие по trigram ≥ 0.45 → None), `create_category(..., is_new)`, `merge_categories(session, principal, source_id, target_id)`, `seed_defaults(session, user_id)`; аналогично `accounts.seed.seed_default_accounts`
- `merchants.service.match_merchant(session, user_id, text_or_handle) -> Merchant | None`, `learn(session, user_id, merchant_name|handle, category_id, account_id=None) -> Merchant`

**Аналитика, курсы, события**:
- `rates.service.get_rate(session, base, quote, on: date, kind="sell", fetch_missing=True) -> RateQuote` (ledger: `kind="sell"` если в паре есть VND, иначе `"mid"`; пусто → одна повторная попытка с другим `kind` и `fetch_missing=False`; качаются только плечи X↔USD, обратные и через USD считает ledger; `backfill_missing` дозаполняет `amount_usd/amount_vnd/usd_rate/rate_date/rate_source` через `ledger.transactions.apply_valuation`, не трогая `version` и проводки), `convert(session, amount, from_, to, on) -> Converted | None`, `refresh_all(session)`; провайдеры в `rates/providers.py` (httpx, мокаются respx)
- `stats.service.summary(session, user_id, period, currency) -> SummaryOut`, `by_category`, `by_day`, `by_month`, `top_transactions`, `compare_periods`, `business`, `daily_average`, `answer_stats_intent(session, user_id, intent: StatsIntent, settings) -> Card`
- `events.bus.publish(session, user_id, entities: list[str], action_id=None, source=None)` — `SELECT pg_notify('pf_events', json)` в текущей транзакции (доставка при commit). `events.bus.listen(user_id) -> AsyncIterator[ChangeEvent]` — общий LISTEN-коннект на процесс + fan-out по очередям.
- `events.outbox.enqueue(session, user_id, kind, payload, dedupe_key=None)`, `claim(session, limit) -> list`, `ack(session, id, ok, error)`
- `scheduler.runner.run_forever()` (вызывается `app/worker.py`), `scheduler.jobs.*`; `reports.service.build_monthly_report(session, user_id, month) -> str`; `recurring.service.*`

**Парсер**: `parser.parse(text: str, ctx: ParseContext) -> ParseResult`; `parser.amounts.parse_amount(raw: str, currency: str|None, small_thousands: bool) -> Decimal`; `parser.dates.resolve_date(token, today) -> date`; `parser.currencies.detect_currency(token) -> str|None`. Чистые функции, **без БД и сети**.

**AI**: `ai.pipeline.handle_message(session, principal, req: AssistantMessageIn) -> AssistantResponse`; `ai.context.build_parse_context(session, principal) -> ParseContext`; `ai.client.LLMClient.extract_intents(text, ctx) -> LLMResult`; `ai.validator.validate(intents, ctx, session) -> list[ValidatedIntent]`; `ai.dispatcher.dispatch(...)`; `ai.cards.*` — сборка карточек (`TransactionCard` и т.д.) из результатов сервисов. Тексты карточек — через `app/core/money.format_money`.

**Вход**: `auth.principal.resolve_principal(request, session) -> Principal`, `auth.passwords.*`, `auth.sessions.*`, `auth.devices.*`, `auth.passkeys.*`, `auth.bot.verify_bot(...)`; middleware: `csrf`, `ratelimit`, `idempotency`, `request_id`, `security_headers`.

## 8. Синхронизация (SSE)
- `GET /api/v1/events/stream` (`text/event-stream`, любая авторизация). События:
  - `hello` `{ "server_time": "...", "version": "..." }` — сразу после подключения;
  - `change` `{ "entities": ["transactions","accounts","stats"], "action_id": "uuid|null", "source": "telegram", "ts": "..." }`;
  - `ping` `{}` каждые 20 с (хартбит для индикатора связи).
- Сервисы после мутаций вызывают `events.bus.publish(...)` в той же транзакции (`pg_notify` уходит при commit). Сущности: `transactions`, `accounts`, `categories`, `merchants`, `settings`, `recurring`, `stats` (stats ставится автоматически вместе с transactions).
- Клиенты: при `change` — `queryClient.invalidateQueries` по ключам сущностей; при фокусе окна/вкладки — refetch активных запросов; переподключение с backoff 1→30 с. Web использует `fetch` + ReadableStream (нужны заголовки Bearer для Electron), не `EventSource`.
- Бот SSE не слушает: забирает outbox раз в 5 с.

## 9. Фоновые задачи (worker)
Отдельный процесс `python -m app.worker` (тот же образ). Держит `pg_try_advisory_lock(778899)` — вторая копия ждёт. Цикл тиков раз в 30 с, задачи (`services/scheduler/jobs.py`):
| задача | когда |
|---|---|
| `refresh_fiat_rates` (VCB, ЦБ, open.er-api) | каждый час + при старте |
| `refresh_crypto_rates` (CoinGecko, активы из account_assets/transactions за 30 дней) | каждые 15 мин |
| `backfill_missing_rates` (amount_usd/vnd IS NULL) | каждые 10 мин |
| `enqueue_reports` (monthly 1-го числа в `report_time`, daily/weekly если включены) | каждый тик, `dedupe_key` |
| `recurring_reminders` (next_due_on ≤ сегодня и время ≥ remind_time) | каждый тик |
| `cleanup` (idempotency > 48 ч, preview > 1 сут, auth_challenges, сессии истёкшие, ai_requests > 180 дн) | раз в час |
Курсы по запросу (новая монета при вводе) API получает синхронно через `rates.service.get_rate` (провайдер вызывается из API-процесса с таймаутом 5 с, результат сохраняется).

## 10. Модель ошибок API
Любая ошибка (кроме 2xx) — JSON:
```json
{ "error": { "code": "validation_error", "message": "Сумма должна быть больше нуля", "details": {"field": "amount"}, "request_id": "a1b2…" } }
```
| HTTP | code | когда |
|---|---|---|
| 400 | `bad_request` | синтаксически неверный запрос |
| 401 | `unauthorized` | нет/неверная сессия/токен |
| 403 | `csrf_failed`, `forbidden`, `telegram_forbidden` | |
| 404 | `not_found` | |
| 409 | `version_conflict` (details: `current_version`), `idempotency_in_progress`, `idempotency_mismatch`, `undo_conflict`, `preview_stale`, `already_exists` | |
| 410 | `preview_expired`, `action_expired` | |
| 422 | `validation_error` (details: `fields: [{loc,msg}]`) | Pydantic и бизнес-проверки |
| 428 | `idempotency_key_required` | |
| 429 | `rate_limited` | + `Retry-After` |
| 503 | `ai_unavailable` (только для не-assistant эндпоинтов), `rates_unavailable` | |
| 500 | `internal` | |
Классы — `app/core/errors.py` (`AppError(code, message, status, details)` + наследники). Тексты `message` — по-русски, показываются пользователю как есть.
**Ассистент** на ошибки разбора/проверки/AI отвечает **200** с карточкой `error` (см. API.md), чтобы клиент рисовал её единообразно; 4xx у ассистента — только авторизация, идемпотентность, лимиты, пустой текст.

## 11. Конфигурация (env, `app/config.py`)
| переменная | по умолчанию | |
|---|---|---|
| `APP_ENV` | `dev` | `dev|test|prod` |
| `DATABASE_URL` | `postgresql+asyncpg://pf:pf@127.0.0.1:5432/pf` | |
| `SECRET_KEY` | — (обязателен в prod, ≥ 32 символа) | HMAC токенов |
| `PUBLIC_URL` | `http://localhost:5173` | |
| `WEB_ORIGINS` | пусто | дополнительные origin'ы веба (origin из `PUBLIC_URL` разрешён всегда), через запятую |
| `DESKTOP_ORIGIN` | `app://pocket` | |
| `COOKIE_SECURE` | `true` (в dev `false`) | |
| `SESSION_TTL_DAYS` | `90` | |
| `WEBAUTHN_RP_ID` / `WEBAUTHN_RP_NAME` / `WEBAUTHN_ORIGINS` | host из `PUBLIC_URL` / `Pocket` / `PUBLIC_URL` | |
| `AI_ENABLED` | `true` | `false` — без LLM, только правиловый парсер |
| `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` / `AI_TIMEOUT` / `AI_MAX_CONCURRENCY` | — / — / — / `20` / `4` | любой OpenAI-совместимый API; в prod при `AI_ENABLED=true` обязательны все три; сравнить модели — `docs/BENCH.md` |
| `BOT_SERVICE_TOKEN` | — | общий с ботом |
| `TELEGRAM_ALLOWED_IDS` | пусто (бот не отвечает никому) | через запятую |
| `TELEGRAM_BOT_USERNAME` | пусто | имя бота без `@` для ссылки в вебе |
| `PARSER_LEXICON_EXTRA` | пусто | путь к JSON с дополнительными счетами/алиасами парсера |
| `DEFAULT_TIMEZONE` | `Asia/Ho_Chi_Minh` | |
| `COINGECKO_API_KEY` | пусто | необязателен |
| `RATES_HTTP_TIMEOUT` | `5` | |
| `LOG_LEVEL` | `INFO` | |
| `WORKER_TICK_SECONDS` | `30` | |
| `WEB_ORIGINS`/`WEBAUTHN_ORIGINS`/`TELEGRAM_ALLOWED_IDS` | | CSV в env (`NoDecode`), JSON не нужен |
| `TRUSTED_PROXIES` | `127.0.0.1/32,::1/128` | IP/CIDR Caddy/docker-сети/reverse-proxy (X-Forwarded-For); uvicorn `--proxy-headers` с другим набором не включать |
| `RATE_LIMIT_ENABLED`, `RATE_LIMIT_*` | вкл. (в `APP_ENV=test` выкл.) | см. `app/core/security_config.py`; compose пробрасывает числовые `RATE_LIMIT_*` из `.env` |
| CORS | | prod: только `DESKTOP_ORIGIN`, без credentials (Bearer); dev: `WEB_ORIGINS` + desktop с credentials |
Бот (`bot/`): `TELEGRAM_BOT_TOKEN`, `API_BASE_URL` (`http://api:8000/api/v1`), `BOT_SERVICE_TOKEN`, `TELEGRAM_ALLOWED_IDS`, `OUTBOX_POLL_SECONDS=5`.
Web: только `VITE_API_BASE_URL` (по умолчанию `/api/v1`; в Electron — `<адрес сервера>/api/v1`: `PF_DEFAULT_SERVER_URL` при сборке или ввод при первом запуске).

## 12. Соглашения
- Python: ruff (line 100), mypy — по желанию, async везде, `from __future__ import annotations` не обязателен. Логи — stdlib `logging` (JSON-formatter в `main.py`).
- Тесты backend: `cd backend && uv run pytest tests/<area>`. Фикстуры в `tests/conftest.py`: `engine` (создаёт схему `Base.metadata.create_all` в БД `TEST_DATABASE_URL`, по умолчанию `postgresql+asyncpg://pf:pf@127.0.0.1:5432/pf_main_test`), `session` (транзакция с откатом после теста), `user` (пользователь + настройки + сид категорий/счетов), `client` (httpx `AsyncClient` поверх ASGI с переопределённой сессией), `principal`. LLM в тестах — мок (respx/фейковый сервер), реальных вызовов нет.
- Web: `pnpm test` (vitest), `pnpm build`, e2e — Playwright (`pnpm e2e`).
- shadcn набор: button, input, textarea, label, card, badge, dialog, sheet, drawer(vaul), dropdown-menu, popover, select, tabs, table, tooltip, switch, checkbox, separator, skeleton, sonner, command, scroll-area, toggle-group, calendar(или свой date-picker), alert, progress, avatar.
- Query-ключи (web): `['transactions', filter]`, `['transaction', id]`, `['accounts']`, `['balances']`, `['categories']`, `['merchants']`, `['stats', kind, params]`, `['settings']`, `['recurring']`, `['actions']`, `['needs-account']`, `['devices']`, `['passkeys']`, `['me']`. Маппинг SSE `entities` → ключи — в `web/src/lib/api/sse.ts`.
- Форматирование денег на фронте — `web/src/lib/format.ts` (по DESIGN.md §3), бэкенд — `app/core/money.format_money` (одинаковые правила: `150 000 ₫`, `$12.40`, `3 TON`, неразрывные пробелы).

## 13. Оговорки
- `transactions.version` — `version_id_col` с `version_id_generator=False`: версию **поднимает сервис вручную, один раз на логическое изменение** (не на каждый flush); ORM по-прежнему проверяет `WHERE version=…` и бросает `StaleDataError` → 409 `version_conflict`. Явная проверка `patch.version != tx.version` → тоже 409. Прямая правка полей ORM без `version += 1` версию не меняет.
- Маршруты web (контракт для ссылок из бота): `/`, `/login`, `/onboarding`, `/add`, `/quick` (Electron), `/operations` (`?status=needs_account` = «Разобрать», `?tx=<id>`), `/analytics?period=<preset>`, `/accounts`, `/settings/*`.
- Карточки ассистента = DESIGN.md §9.2 (`kind`: created, created_many, needs_account, preview, answer, unclear, error + updated, deleted, applied, balance, category, undone). Telegram-шаблоны — DESIGN.md §12, callback-формат — API.md §13.
