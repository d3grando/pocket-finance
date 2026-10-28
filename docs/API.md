# Pocket Finance — REST API v1 (контракт)

> Базовый путь: `/api/v1`. JSON UTF-8. Схемы-источник: `backend/app/schemas/*.py` (Pydantic); OpenAPI в dev — `/api/docs`.
> Общие правила — `docs/ARCH.md` §4–§10. Тексты (`message`, `title`…) — по-русски.

## 0. Общие правила
- **Деньги** — строки-Decimal: `"150000"`, `"3.5"`, `"-120000"`. Числа с плавающей точкой в теле запроса → 422. Курсы — тоже строки.
- **Даты** — `YYYY-MM-DD` (локальная дата пользователя), **время** — ISO 8601 с поясом (`2026-10-27T14:32:00+07:00`).
- **Валюты/активы** — `^[A-Z0-9]{2,16}$` (`VND`, `USD`, `RUB`, `USDT`, `TON`).
- **Авторизация** (колонка «Auth»):
  - `any` — сессия (cookie) / устройство (Bearer `pfd_…`) / бот (Bearer service + `X-Telegram-User-Id`);
  - `human` — сессия или устройство; `session` — только cookie-сессия; `bot-svc` — сервисный токен бота без TG-id; `none` — без авторизации.
- **Cookie**: несколько `pf_session` в одном запросе → 400 `bad_request` (браузер шлёт одну).
- **CSRF**: при cookie-сессии все POST/PUT/PATCH/DELETE требуют `X-CSRF-Token: <значение cookie pf_csrf>` (и `MeOut.csrf_token`).
- **Идемпотентность**: `Idempotency-Key: <uuid>` — колонка «Idem»: `req` обязателен (428 без него), `opt` — по желанию. Повтор с тем же ключом и тем же телом → тот же ответ с заголовком `Idempotent-Replayed: true`; с другим телом → 409 `idempotency_mismatch`; ещё выполняется → 409 `idempotency_in_progress`; некорректный ключ → 400 `bad_request`.
- **Оптимистичная блокировка**: PATCH принимает `version` → 409 `version_conflict` (`details.current_version`).
- **Ошибки** — `{ "error": { "code", "message", "details", "request_id" } }` (ARCH §10). Ниже в «Ошибки» перечислены специфичные коды; 401/403/422/429/500 возможны везде.
- **Пагинация** — `limit`/`offset`, ответ содержит `total`.
- **Заголовки ответа**: `X-Request-Id` всегда; `Retry-After` при 429 (и `details.retry_after`, секунды).
- **CORS**: в prod веб same-origin; кросс-доменно ходит только Electron (`app://pocket`) и только с `Authorization: Bearer pfd_…` (без cookies, `allow_credentials=false`). Cookie-сессия с этого origin → 401.

## 1. Health
| Метод | Путь | Auth | Ответ |
|---|---|---|---|
| GET | `/health` | none | `{ "status": "ok"|"degraded", "db": true, "version": "1.0.0", "server_time": "…" }` |

## 2. Auth
| Метод | Путь | Auth | Idem | Тело → Ответ | Ошибки |
|---|---|---|---|---|---|
| POST | `/auth/login` | none | — | `LoginIn` → `MeOut` + Set-Cookie `pf_session`, `pf_csrf` | 401 `unauthorized` («Неверный пароль»), 429 |
| POST | `/auth/logout` | any | — | → `{ "ok": true }` (сессия: отзыв + очистка кук; устройство: отзыв токена) | |
| POST | `/auth/logout-all?keep_current=false&remove_passkeys=true` | human | — | → `LogoutAllOut` `{ ok, sessions_revoked, devices_revoked, passkeys_removed }` — «Выйти со всех устройств» (все сессии и токены устройств; `keep_current=true` оставляет текущую; `remove_passkeys` — по умолчанию `true`, удаляет и ключи входа). Cookie-сессия — с CSRF | |
| GET | `/auth/me` | any | — | → `MeOut` | 401 |
| POST | `/auth/device-login` | none | — | `DeviceLoginIn` → 201 `DeviceLoginOut` | 401, 429 |
| GET | `/auth/sessions` | session | — | → `SessionOut[]` | |
| DELETE | `/auth/sessions/{id}` | session | — | → ok | 404 |

```jsonc
// LoginIn
{ "username": "owner", "password": "…" }           // username по умолчанию "owner"
// MeOut
{ "user_id": 1, "username": "owner", "auth": "session", "source": "web",
  "onboarding_done": false, "has_passkeys": true, "csrf_token": "…" /* только для session */ }
// DeviceLoginIn
{ "username": "owner", "password": "…", "device_name": "DESKTOP-ABC", "platform": "windows" }
// DeviceLoginOut — токен показывается один раз, хранить в safeStorage
{ "token": "pfd_…", "device_id": "uuid" }
// SessionOut
{ "id": "uuid", "user_agent": "…", "ip": "1.2.3.4", "created_at": "…", "last_seen_at": "…", "expires_at": "…", "is_current": true }
```

### Passkeys (Face ID)
| Метод | Путь | Auth | Тело → Ответ |
|---|---|---|---|
| POST | `/passkeys/register/options` | session | `{ "password": string \| null }` (≤ 256; пустое тело = `null`) → `{ "challenge_id": "uuid", "options": PublicKeyCredentialCreationOptionsJSON }`. Пароль обязателен, кроме cookie-сессии, созданной ≤ 300 с назад; иначе 403 `reauth_required`, неверный пароль — 401 `wrong_password` (лимит неверных паролей на пользователя, как у `/users/me/password`). После успешной регистрации владельцу уходит уведомление в Telegram (если привязан) |
| POST | `/passkeys/register/verify` | session | `{ "challenge_id", "credential": RegistrationResponseJSON, "name": "iPhone" }` → 201 `PasskeyOut` |
| POST | `/passkeys/login/options` | none | `{ "username": "owner" }` → `{ "challenge_id", "options": PublicKeyCredentialRequestOptionsJSON }` |
| POST | `/passkeys/login/verify` | none | `{ "challenge_id", "credential": AuthenticationResponseJSON }` → `MeOut` + куки как у login |
| GET | `/passkeys` | human | → `PasskeyOut[]` `{ id, name, created_at, last_used_at }` |
| PATCH | `/passkeys/{id}` | human | `{ "name" }` → `PasskeyOut` |
| DELETE | `/passkeys/{id}` | human | → ok |
Ошибки: 401 (проверка не прошла), 410 `gone` (челлендж истёк, 5 мин). Клиент: `@simplewebauthn/browser` `startRegistration({optionsJSON})` / `startAuthentication({optionsJSON})`.

### Devices (Electron)
| GET | `/devices` | human | → `DeviceOut[]` `{ id, name, platform, created_at, last_used_at, is_current }` |
|---|---|---|---|
| DELETE | `/devices/{id}` | human | → ok (токен сразу перестаёт работать → 401 у устройства) |

### Users
| Метод | Путь | Auth | Тело → Ответ |
|---|---|---|---|
| GET | `/users/me` | human | → `{ id, username, created_at }` |
| POST | `/users/me/password` | human | `{ "current_password", "new_password" (≥12), "revoke_other_sessions": true, "remove_passkeys": true }` → ok (`remove_passkeys` по умолчанию `true` — удаляет ключи входа); 401 `wrong_password` при неверном текущем (ошибка формы, не выход) |
| POST | `/users/me/wipe` | human | `{ "password", "confirm": "УДАЛИТЬ" }` → ok (удаляет все финансовые данные, пересоздаёт стартовые счета/категории, `onboarding_done=false`); 401 `wrong_password` |

### Settings
| GET | `/settings` | any | → `SettingsOut` |
|---|---|---|---|
| PATCH | `/settings` | any | `SettingsPatch` (`version` обязателен) → `SettingsOut`; 409 `version_conflict` |
| GET | `/settings/server` | any | → `ServerInfoOut` (только чтение, без секретов) |

```jsonc
// SettingsOut
{ "timezone": "Asia/Ho_Chi_Minh", "default_currency": "VND", "analytics_currency": "USD",
  "summary_currency": "VND", "small_amount_thousands": true, "theme": "system",
  "desktop_hotkey": "Ctrl+Alt+Space", "telegram_user_id": 123456789,
  "report_monthly": true, "report_daily": false, "report_weekly": false,
  "report_time": "10:00:00",          // месячный отчёт, 1-го числа
  "report_daily_time": "22:00:00",    // ежедневный (до 12:00 — за вчера, иначе за сегодня)
  "report_weekly_day": 6,             // 0=Пн … 6=Вс
  "report_weekly_time": "21:00:00",
  "reminders_enabled": true,          // напоминания о регулярных платежах
  "debt_reminders_enabled": false,    // напоминать о долгах (DEBTS.md §9), по умолчанию выкл.
  "debt_reminder_days": 30,           // «старше N дней», 1..365
  "onboarding_done": true, "version": 3 }
// ServerInfoOut (Telegram id не раскрываются)
{ "ai_base_url": "https://llm.example.com/v1", "ai_model": "your-model", "ai_configured": true,
  "telegram_bot_configured": true, "telegram_bot_username": "your_pocket_bot", // или null
  "version": "1.0.0", "public_url": "https://pocket.example.com" }
```

## 3. Transactions
| Метод | Путь | Auth | Idem | Тело → Ответ | Ошибки |
|---|---|---|---|---|---|
| GET | `/transactions` | any | — | query `TransactionFilter` → `TransactionPage` | |
| GET | `/transactions/needs-account/count` | any | — | → `{ "count": 2 }` | |
| GET | `/transactions/{id}` | any | — | → `TransactionOut` | 404 |
| POST | `/transactions` | any | req | `TransactionCreate` → 201 `MutationResult` | 422 |
| PATCH | `/transactions/{id}` | any | opt | `TransactionPatch` → `MutationResult` | 404, 409 `version_conflict`, 422 |
| DELETE | `/transactions/{id}?version=N` | any | opt | → `MutationResult` (мягкое удаление; `transaction.deleted` не показывается в списках) | 404, 409 |
| POST | `/transactions/{id}/restore` | any | opt | → `MutationResult` | 404 |
| POST | `/transactions/{id}/account` | any | opt | `AssignAccountIn` → `MutationResult` (ответ на «С какого?») | 404, 409, 422 |
| GET | `/transactions/{id}/history` | any | — | → `AuditEntryOut[]` | 404 |

**Query `GET /transactions`** (все необязательны): `date_from`, `date_to` (включительно), `kind` (повторяемый: `expense|income|refund|transfer|exchange|adjustment`), `category_id` (повт.), `account_id` (повт.), `currency`, `min_amount`, `max_amount`, `amount_currency` (`USD` → сравнение по `amount_usd`, `VND` → по `amount_vnd`, иначе — исходная сумма), `q` (поиск по описанию/получателю/исходному тексту/продавцу/категории), `is_business`, `status` (`posted|needs_account`), `merchant_id`, `sort` (`date_desc` по умолчанию, `date_asc`, `amount_desc`, `amount_asc`), `limit` (1–500, 50), `offset`.

```jsonc
// TransactionOut
{
  "id": 123, "kind": "expense", "status": "posted",
  "occurred_at": "2026-10-27T14:32:05+07:00", "occurred_on": "2026-10-27",
  "amount": "15", "currency": "USD",
  "account": { "id": 1, "name": "Карта", "icon": "Landmark" },
  "account_amount": "392100", "account_asset": "VND", "account_rate": "26140", "is_estimated": true,
  "counter_account": null, "counter_amount": null, "counter_asset": null,
  "category": { "id": 7, "name": "Подписки", "kind": "expense", "icon": "Repeat", "color": null, "is_business": false, "is_new": false },
  "is_business": false,
  "merchant": { "id": 4, "name": "vast.ai" },
  "counterparty": null, "description": "подписка",
  "amount_usd": "15", "amount_vnd": "392100", "usd_rate": "1", "rate_date": "2026-10-27", "rate_source": "vietcombank",
  "refund_of_id": null, "recurring_id": null,
  "source": "desktop", "raw_text": "потратил 15 долларов на подписку",
  "created_action_id": "uuid", "version": 1,
  "created_at": "…", "updated_at": "…",
  "title": "vast.ai",         // DESIGN §9.4: описание или продавец/получатель, иначе категория; категория — вторая строка у клиента, если отличается
  "debt": null                // долговая операция: { "id": 5, "name": "Коля", "op": "lend|repay_in|borrow|repay_out|forgive" } (§15, аддитивно)
}
// TransactionPage
{ "items": [TransactionOut], "total": 412, "limit": 50, "offset": 0,
  "totals": { "expense_vnd": "12450000", "income_vnd": "30000000", "expense_usd": "476.3", "income_usd": "1147.6", "count": 412 } }
// TransactionCreate (ручная форма). kind: expense|income|refund
{ "kind": "expense", "amount": "150000", "currency": "VND", "category_id": 1,
  "account_id": null,        // null + account_none=false → дефолт по валюте или needs_account
  "account_none": false,     // true = «без счёта»
  "account_amount": null,    // факт списания в валюте счёта (иначе по курсу, is_estimated)
  "merchant_id": null, "counterparty": null, "description": "обед",
  "occurred_on": "2026-10-26", "occurred_at": null, "refund_of_id": null, "recurring_id": null }
// TransactionPatch — только изменяемые поля + version
{ "version": 1, "amount": "250000" }
// counter_amount — только для kind=exchange (полученная сумма; account_rate = counter_amount/amount)
// AssignAccountIn
{ "account_id": 3, "version": 2 }   // account_id null = «Без счёта»; version необязательна:
                                    // с ней — 409 version_conflict при несовпадении; без неё —
                                    // 409 version_conflict, если status ≠ needs_account (счёт уже выбран)
// MutationResult
{ "transaction": TransactionOut, "action_id": "uuid" }   // action_id → POST /actions/{id}/undo
```

### Transfers / exchanges / adjustments
| Метод | Путь | Auth | Idem | Тело → Ответ | Ошибки |
|---|---|---|---|---|---|
| POST | `/transfers` | any | req | `{ "from_account_id", "to_account_id", "amount", "currency", "description"?, "occurred_on"? }` → 201 `MutationResult` (kind=transfer) | 422 (одинаковые счета, валюта не поддерживается счётом) |
| POST | `/exchanges` | any | req | `{ "from_account_id", "to_account_id", "amount", "currency", "counter_amount", "counter_currency", … }` → 201 `MutationResult` (kind=exchange, `account_rate` = фактический курс) | 422 |
| POST | `/adjustments` | any | req | `{ "account_id", "asset", "actual_balance" }` → 201 `AdjustmentResult` | 404, 422 |

```jsonc
// AdjustmentResult
{ "kind": "adjusted" /* | "opening_set" | "matches" */, "account_id": 1, "asset": "VND",
  "previous_balance": "5320000", "balance": "5200000", "delta": "-120000",
  "transaction": TransactionOut | null, "action_id": "uuid" | null }
```

## 4. Accounts
| Метод | Путь | Auth | Тело → Ответ | Ошибки |
|---|---|---|---|---|
| GET | `/accounts?include_archived=false&include_debts=false` | any | → `AccountOut[]` (с `assets` и балансами). Служебные счета долгов (`kind="debt"`) — только с `include_debts=true` | |
| GET | `/accounts/balances?include_debts=false` | any | → `BalancesOut` (итоги — без счетов-долгов) | |
| POST | `/accounts` | any | `AccountCreate` → 201 `AccountOut` | 409 `already_exists`, 422 |
| POST | `/accounts/onboarding` | any | `{ "items": [{ "account_id", "asset", "amount" }] }` → `BalancesOut` (+ `onboarding_done=true`) | 422 |
| GET | `/accounts/{id}` | any | → `AccountOut` | 404 |
| PATCH | `/accounts/{id}` | any | `AccountPatch` (`version`) → `AccountOut` | 409, 422 (валюта уже дефолтная у другого счёта) |
| DELETE | `/accounts/{id}` | any | → ok (есть операции → архивируется) | 404 |
| PUT | `/accounts/{id}/opening` | any | `{ "asset": "VND", "amount": "5200000" | null, "opening_at"? }` → `AccountOut` | 404, 422 |
| GET | `/my-accounts` | any | → `MyAccountOut[]` `{ id, handle, label, account_id }` | |
| POST | `/my-accounts` | any | `{ "handle": "@myshop", "label"?, "account_id"? }` → 201 (без account_id создаётся кошелёк `@handle`) | 409 |
| DELETE | `/my-accounts/{id}` | any | → ok | 404 |

```jsonc
// AccountOut
{ "id": 3, "name": "Кошелёк", "kind": "crypto_wallet", "currency": null, "is_multi_asset": true,
  "aliases": ["Кошелёк", "кошелёк"], "default_for_currencies": [], "color": null, "icon": "Bot",
  "sort_order": 3, "is_archived": false, "version": 1,
  "assets": [
    { "asset": "TON", "opening_balance": "10", "opening_at": "…", "balance": "7", "approx_usd": "37.8", "approx_vnd": "988000" },
    { "asset": "USDT", "opening_balance": null, "opening_at": null, "balance": null, "approx_usd": null, "approx_vnd": null }
  ] }
// balance: null → UI пишет «остаток не задан» (не 0)
// kind "debt" — служебный счёт человека («Долг: Коля»), создаётся только сервисом долгов; в POST/PATCH /accounts,
// переводах, обменах, сверке и ручных операциях такой account_id → 422 (DEBTS.md §2)
// BalancesOut
{ "accounts": [AccountOut], "total_vnd": "48200000", "total_usd": "1844.3", "unknown_count": 2 }
// AccountCreate
{ "name": "Wise", "kind": "bank", "currency": "USD", "is_multi_asset": false, "aliases": ["вайз"],
  "default_for_currencies": [], "icon": "PiggyBank", "opening_balance": null }
```

## 5. Categories
| Метод | Путь | Auth | Тело → Ответ | Ошибки |
|---|---|---|---|---|
| GET | `/categories?kind=&include_archived=&with_counts=` | any | → `CategoryOut[]` | |
| GET | `/categories/suggest?transaction_id=&q=` | any | → `{ "similar": [{ "category": CategoryOut, "score": 0.81 }], "all": [CategoryOut] }` («В другую») | |
| POST | `/categories` | any | `{ "name", "kind", "is_business", "icon"?, "color"?, "aliases"? }` → 201 | 409 `already_exists` |
| POST | `/categories/merge` | any | `{ "source_id", "target_id" }` → `CategoryOut` (target) | 422 (разные kind) |
| PATCH | `/categories/{id}` | any | `CategoryPatch` (`version`; `is_new:false` = подтвердить 🆕) → `CategoryOut` | 409 |
| DELETE | `/categories/{id}` | any | → ok (архив; системная «Прочее» — 422) | |

```jsonc
// CategoryOut
{ "id": 15, "name": "Софт и AI", "kind": "expense", "is_business": true, "icon": "Cpu", "color": null,
  "aliases": ["софт", "ai"], "is_system": true, "is_new": false, "is_archived": false,
  "sort_order": 15, "version": 1, "tx_count": 12 }
```

## 6. Merchants
| GET | `/merchants?q=` | any | → `MerchantOut[]` |
|---|---|---|---|
| POST | `/merchants` | any | `{ "name", "aliases", "handle"?, "category_id"?, "default_account_id"? }` → 201 |
| PATCH | `/merchants/{id}` | any | частичное → `MerchantOut` |
| DELETE | `/merchants/{id}` | any | → ok |
```jsonc
// MerchantOut
{ "id": 4, "name": "vast.ai", "aliases": ["васт", "vast"], "handle": null, "category_id": 14,
  "category_name": "Серверы/инфра", "default_account_id": null, "hits": 9, "last_used_at": "…" }
```

## 7. History / undo / audit
| Метод | Путь | Auth | Idem | Ответ | Ошибки |
|---|---|---|---|---|---|
| GET | `/actions?limit=50&offset=0&recent=false` | any | — | `ActionOut[]` (`recent=true` — контекст: 10 за 30 мин) | |
| POST | `/actions/{id}/undo` | any | req | `UndoResult` | 404, 409 `undo_conflict` |
| GET | `/audit?entity_type=transaction&entity_id=123` | any | — | `AuditEntryOut[]` | |

```jsonc
// ActionOut
{ "id": "uuid", "kind": "create_transaction", "summary": "Еда 150 000 ₫", "source": "telegram",
  "input_text": "еда 150к", "entity_refs": [{ "type": "transaction", "id": 123 }],
  "is_undoable": true, "undone_at": null, "created_at": "…" }
// UndoResult — повторная отмена того же действия → status "already_undone" (200)
{ "status": "undone", "undone_action_id": "uuid", "undo_action_id": "uuid",
  "summary": "Отменено: 150 000 ₫ · Еда", "changed": ["transactions", "accounts"] }
// «Вернуть» (redo) = POST /actions/{undo_action_id}/undo
// AuditEntryOut
{ "id": 9, "entity_type": "transaction", "entity_id": "123", "operation": "update",
  "before": { "amount": "200000", … }, "after": { "amount": "250000", … },
  "action_id": "uuid", "source": "web", "created_at": "…" }
```

## 8. Export
| Метод | Путь | Auth | Ответ |
|---|---|---|---|
| GET | `/export/transactions?format=csv|xlsx&<фильтры GET /transactions>` | any | файл `pocket-transactions-YYYY-MM-DD.csv|xlsx` (`Content-Disposition: attachment`). CSV: UTF-8 BOM, `;`, точка в дробях. Колонки — `app/schemas/export.py: EXPORT_COLUMNS` |
| GET | `/export/full` | any | `FullExport` JSON (attachment `pocket-export-YYYY-MM-DD.json`): settings, accounts, categories, merchants, my_accounts, transactions (вкл. удалённые), recurring |

## 9. Assistant — единый вход для web, desktop, бота
| Метод | Путь | Auth | Idem | Тело → Ответ |
|---|---|---|---|---|
| POST | `/assistant/messages` | any | req | `AssistantMessageIn` → `AssistantResponse` |
| POST | `/assistant/previews/{token}/confirm` | any | req | — → `AssistantResponse` (`AppliedCard` или `ErrorCard` `preview_expired`/`preview_stale`) |
| POST | `/assistant/previews/{token}/cancel` | any | — | → ok |

- Всегда **200** для результатов разбора, включая ошибки проверки / «не понял» / «AI недоступен» (карточки `error`/`unclear`); 4xx — только auth/CSRF/идемпотентность/лимиты/пустой текст (422).
- `source` берётся из авторизации (web / desktop / telegram), клиент его не передаёт.
- Ничего не записывается, если хоть один интент сообщения не прошёл проверку (атомарно).

```jsonc
// AssistantMessageIn
{ "text": "еда 120к, такси 45к и кофе 30к",
  "context": { "edit_transaction_id": null } }   // после «Изменить»: id операции → «250к» правит её
// AssistantResponse
{ "request_id": 812, "route": "parser" /* parser|llm|parser_llm|none */,
  "cards": [Card, …], "action_ids": ["uuid"], "changed": ["transactions", "stats"], "ai_available": true }
```

### 9.1 Кнопки
```jsonc
// Button
{ "id": "undo", "label": "Отменить", "style": "ghost", "hotkey": "Ctrl+Z",
  "action": { "kind": "undo", "action_id": "uuid" } }
```
| `action.kind` | Что делает клиент |
|---|---|
| `undo` | `POST /actions/{action_id}/undo` (Idempotency-Key) → показать «Отменено · [Вернуть]» |
| `redo` | `POST /actions/{action_id}/undo` (это undo-действие) |
| `edit` | web/desktop: инлайн-форма правки `transaction_id` (`PATCH /transactions/{id}`) либо следующее сообщение с `context.edit_transaction_id`; бот: «Напиши новую сумму или категорию» → следующее сообщение с `context.edit_transaction_id` |
| `choose_account` | `POST /transactions/{transaction_id}/account { "account_id": <id|null> }` |
| `later` | ничего не отправлять; операция остаётся в «Разобрать: N» |
| `change_category` | открыть выбор: `GET /categories/suggest?transaction_id=` |
| `set_category` | `PATCH /transactions/{transaction_id} { "category_id", "version" }` |
| `confirm_preview` | `POST /assistant/previews/{preview_token}/confirm` (Enter) |
| `cancel_preview` | `POST /assistant/previews/{preview_token}/cancel` (Esc) |
| `send_text` | отправить `action.text` новым сообщением ассистенту |
| `manual` | открыть ручную форму, `action.prefill` — `{ "amount": "…", "currency": "…", "text": "…" }` |
| `retry` | локальные карточки клиента (сеть / 5xx / `idempotency_in_progress`) — повторить с тем же Idempotency-Key; карточку, которую ответил сервер (`error` с `ai_unavailable`/`internal`, `unclear`), повторять с НОВЫМ ключом — сохранённый ответ был бы просто переигран. Сервер ключ для `ai_unavailable`/`internal` без записанных действий не сохраняет (release), так что старые клиенты с тем же ключом тоже доходят до пайплайна |
| `open` | перейти на `action.route` (например `/analytics?period=this_month`; бот — url-кнопка `PUBLIC_URL + route`) |

### 9.2 Карточки (`Card`, поле `kind`; соответствуют DESIGN.md §9.2)
Общие поля всех карточек: `title` (строка-заголовок, готовый русский текст), `subtitle` (вторая строка или null), `buttons: Button[]`. Эмодзи в `title` нет, кроме «🆕» у новой категории; бот сам добавляет ✅/❓/⚠️ по `kind`.

| kind | вариант DESIGN | ключевые поля | кнопки |
|---|---|---|---|
| `created` | (a)/(b) | `action_id`, `transaction`, `category_is_new`, `approx` («≈ $16.20»), `note` («Запомню: @alice → Софт и AI») | `undo` «Отменить», `edit` «Изменить», при 🆕 — `change_category` «В другую» |
| `created_many` | (c) | `action_ids[]`, `transactions[]`, `total`, `total_currency` | `undo` для каждого action (клиент шлёт все) «Отменить все», `edit` |
| `needs_account` | (d) | `action_id`, `transaction`, `question` («С какого счёта?» / «На какой счёт?»), `options: [{account_id|null, label, hint, hotkey}]` | `choose_account` по каждому варианту (+ «Без счёта» `account_id:null`), `later` «Позже», `undo` |
| `updated` | — | `action_id`, `transaction`, `changes: ["45 000 ₫ → 50 000 ₫"]` | `undo`, `edit` |
| `deleted` | — | `action_id`, `transaction` | `undo` «Вернуть» |
| `preview` | (e) | `preview_token`, `operation` (`bulk_update|bulk_delete`), `change_summary`, `items: [{transaction, before, after}]` (≤ 20), `total_count`, `total` («Итого 181 000 ₫»), `expires_at` | `cancel_preview` «Отмена» (Esc), `confirm_preview` «Применить» (Enter, primary) |
| `applied` | (e→a) | `action_id`, `count` | `undo` |
| `answer` | (f) | `answer_type` (`stats|list|compare|balance|text`), `headline` («−595 000 ₫»), `headline_approx` («≈ $22.76»), `lines: [{label, value, amount, currency, share, category_id, transaction_id, icon}]`, `transactions[]` (для list), `total_count`, `filter` (SearchFilter для «Открыть в операциях»), `text` | `open` «Открыть в аналитике →» |
| `balance` | — | `action_id|null`, `account: AccountOut`, `result` (`adjusted|opening_set|matches`), `transaction` | `undo` (если action_id) |
| `category` | — | `action_id`, `category` | `undo` |
| `undone` | (i) | `undone_action_id`, `undo_action_id` | `redo` «Вернуть» |
| `unclear` | (g) | `reason` (`ambiguous_currency|ambiguous_target|missing_amount|unknown|other`), `question`, `keep_input: true` | `send_text` с готовыми вариантами, `manual` «Вручную» |
| *(долги)* | — | у `created`/`needs_account`/`updated` — необязательное `debt: DebtBrief`, у `answer` — `debts: DebtsOut` (DEBTS.md §8); `kind` и кнопки — существующие | |
| `error` | (h) | `code` (`ai_unavailable|validation_failed|not_found|conflict|preview_expired|preview_stale|nothing_to_undo|internal`), `text`, `keep_input: true` | `manual` «Вручную», `retry` «Повторить» |

Пример — «перевёл 3 тона @alice за аккаунт gpt pro»:
```json
{
  "request_id": 813, "route": "llm", "ai_available": true,
  "action_ids": ["6b0c…"], "changed": ["transactions", "accounts", "merchants", "stats"],
  "cards": [{
    "kind": "created",
    "title": "3 TON · Софт и AI",
    "subtitle": "Кошелёк · @alice · «аккаунт GPT Pro»",
    "approx": "≈ $16.20",
    "category_is_new": false,
    "note": "Запомню: @alice → Софт и AI",
    "action_id": "6b0c…",
    "transaction": { "id": 124, "kind": "expense", "amount": "3", "currency": "TON", "…": "…" },
    "buttons": [
      { "id": "change-category", "label": "В другую", "style": "outline", "action": { "kind": "change_category", "transaction_id": 124 } },
      { "id": "undo", "label": "Отменить", "style": "ghost", "hotkey": "Ctrl+Z", "action": { "kind": "undo", "action_id": "6b0c…" } },
      { "id": "edit", "label": "Изменить", "style": "ghost", "hotkey": "Ctrl+E", "action": { "kind": "edit", "transaction_id": 124 } }
    ]
  }]
}
```
Пример — «потратил 15 USDT на подписку» (крипта без счёта):
```json
{ "kind": "needs_account", "title": "15 USDT · Подписки", "subtitle": "≈ $15.00",
  "question": "С какого счёта?", "action_id": "…", "transaction": { "id": 125, "status": "needs_account", "…": "…" },
  "options": [ { "account_id": 3, "label": "Кошелёк", "hint": "есть USDT", "hotkey": "1" },
               { "account_id": 4, "label": "Копилка", "hint": null, "hotkey": "2" },
               { "account_id": null, "label": "Без счёта", "hint": null, "hotkey": "0" } ],
  "buttons": [ { "id": "acc-3", "label": "Кошелёк", "style": "outline", "hotkey": "1", "action": { "kind": "choose_account", "transaction_id": 125, "account_id": 3 } },
               { "id": "acc-4", "label": "Копилка", "style": "outline", "hotkey": "2", "action": { "kind": "choose_account", "transaction_id": 125, "account_id": 4 } },
               { "id": "acc-none", "label": "Без счёта", "style": "ghost", "hotkey": "0", "action": { "kind": "choose_account", "transaction_id": 125, "account_id": null } },
               { "id": "later", "label": "Позже", "style": "ghost", "action": { "kind": "later" } },
               { "id": "undo", "label": "Отменить", "style": "ghost", "action": { "kind": "undo", "action_id": "…" } } ] }
```

### 9.3 Что понимает ассистент (интенты, `backend/app/schemas/intents.py`)
`create_transaction` (расход/доход/возврат), `transfer` (между своими/на свой @ник; на чужой @ник → расход с получателем), `adjust_balance` («на карте 5.2м»), `update_transaction` («нет, 250к», «исправь последнюю покупку на 150к», «васт списали 21.3м»), `delete_transaction` («удали последний расход»), `undo` («отмени»), `search` («покажи траты дороже 100 долларов»), `stats` («сколько потратил сегодня», «на что ушло за месяц», «сколько чистыми за октябрь»), `create_category`, `answer` (общий вопрос), `clarify`, `debt` (долги: дал в долг / вернули / занял / вернул / простить — DEBTS.md §5), `debts_query` («кто мне должен?»).
Подтверждения: правка/удаление **одной последней** — сразу; найденное поиском или > 1 записи — `preview` → Enter.

## 10. Statistics
Период (везде): `preset` = `today|yesterday|this_week|last_week|this_month|last_month|this_year|last_year|last_7_days|last_30_days|all_time|custom` (по умолчанию `this_month`), для `custom` — `date_from`, `date_to` (включительно). Неделя — с понедельника. `currency` = `VND|USD`.
| Метод | Путь | Ответ |
|---|---|---|
| GET | `/stats/summary?currency=VND` | `SummaryOut` — плитки главной |
| GET | `/stats/by-category?<период>&kind=expense&currency=USD&is_business=` | `ByCategoryOut` |
| GET | `/stats/series?<период>&granularity=day|month&currency=USD` | `SeriesOut` |
| GET | `/stats/top?<период>&limit=10&currency=USD` | `TopOut` |
| GET | `/stats/daily-average?<период>&currency=USD` | `DailyAverageOut` |
| GET | `/stats/compare?<период>&prev_date_from=&prev_date_to=&currency=USD` | `CompareOut` (без prev_* — предыдущий равный период) |
| GET | `/stats/business?<период>&currency=USD` | `BusinessOut` |
Все — Auth `any`.

```jsonc
// SummaryOut
{ "currency": "VND",
  "today": { "date_from": "2026-10-27", "date_to": "2026-10-27", "label": "Сегодня" },
  "month": { "date_from": "2026-10-01", "date_to": "2026-10-31", "label": "Октябрь 2026" },
  "spent_today":  { "value": "420000",   "currency": "VND", "approx_usd": "16.07", "change_ratio": null,   "sparkline": [] },
  "spent_month":  { "value": "12450000", "currency": "VND", "approx_usd": "476.3", "change_ratio": "0.12", "sparkline": ["310000", "…"] },
  "income_month": { "value": "30000000", "currency": "VND", "approx_usd": "1147.6", "change_ratio": null, "sparkline": [] },
  "net_month":    { "value": "17550000", "currency": "VND", "approx_usd": "671.3", "change_ratio": null, "sparkline": [] },
  "needs_account_count": 2, "missing_rates_count": 0 }
// value: null → «—» (нет данных); "0" → реальный ноль
// ByCategoryOut
{ "range": DateRange, "kind": "expense", "currency": "USD", "total": "476.3",
  "items": [{ "category_id": 1, "name": "Еда", "icon": "Utensils", "color": null, "is_business": false, "is_new": false,
              "amount": "180.2", "share": "0.378", "count": 31 }] }
// SeriesOut
{ "range": DateRange, "granularity": "day", "currency": "USD",
  "points": [{ "date": "2026-10-01", "label": "1 окт", "expense": "12.4", "income": "0", "net": "-12.4" }] }
// TopOut
{ "range": DateRange, "currency": "USD",
  "items": [{ "transaction_id": 99, "occurred_on": "2026-10-12", "title": "Серверы/инфра · vast.ai", "category": "Серверы/инфра",
              "amount": "815", "currency": "USD", "original_amount": "21300000", "original_currency": "VND" }] }
// DailyAverageOut
{ "range": DateRange, "currency": "USD", "days": 27, "total": "476.3", "average": "17.64" }
// CompareOut
{ "currency": "USD", "current": { "range": DateRange, "expense": "476.3", "income": "1147.6", "net": "671.3" },
  "previous": { "range": DateRange, "expense": "425.1", "income": "900", "net": "474.9" },
  "expense_change_ratio": "0.12", "income_change_ratio": "0.275", "by_category": [CategorySlice] }
// BusinessOut
{ "range": DateRange, "currency": "USD", "business_income": "1100", "business_expense": "320",
  "business_net": "780", "personal_income": "47.6", "personal_expense": "156.3",
  "expense_by_category": [CategorySlice], "income_by_category": [CategorySlice] }
```

## 11. Rates, search, recurring, reports
| Метод | Путь | Auth | Idem | Тело → Ответ |
|---|---|---|---|---|
| GET | `/rates/latest` | any | — | `{ "usd_vnd": RateOut, "usd_rub": RateOut, "crypto": [RateOut] }` |
| GET | `/rates/convert?amount=15&from_=USD&to=VND&on=2026-10-27` | any | — | `{ "amount", "currency", "converted", "to", "rate": RateOut }`; 503 `rates_unavailable` |
| POST | `/rates/refresh` | human | — | ok |
| GET | `/search?q=highl&limit=20` | any | — | `{ "query", "hits": [{ "type": "transaction|category|account|merchant", "id", "title", "subtitle", "route" }] }` |
| GET | `/recurring` | any | — | `RecurringOut[]` |
| POST | `/recurring` | any | — | `RecurringCreate` → 201 `RecurringOut` |
| PATCH | `/recurring/{id}` | any | — | `RecurringPatch` (`version`) → `RecurringOut` |
| DELETE | `/recurring/{id}` | any | — | ok |
| POST | `/recurring/{id}/record` | any | req | `{ "amount"?, "amount_text"? («11.5м», разбирает парсер), "currency"?, "account_id"?, "occurred_on"? }` → 201 `MutationResult` («Записать») |
| POST | `/recurring/{id}/skip` | any | — | → `RecurringOut` (сдвиг next_due_on) |
| GET | `/reports/{daily|weekly|monthly}?on=YYYY-MM-DD` | any | — | `{ "kind", "range", "text_html", "text_plain" }` |
| POST | `/reports/{kind}/send?on=` | any | — | 202 `{ "outbox_id" }` — отправить в Telegram сейчас |

```jsonc
// RateOut
{ "base": "USD", "quote": "VND", "rate": "26140", "rate_date": "2026-10-27", "kind": "sell",
  "source": "vietcombank", "fetched_at": "…", "is_fallback": false, "label": "$1 = 26 140 ₫ (VCB, 27.10)" }
// RecurringOut
{ "id": 2, "title": "Hetzner", "kind": "expense", "amount": "45", "currency": "USD", "account_id": null,
  "category_id": 14, "merchant_id": null, "description": null, "frequency": "monthly", "interval": 1,
  "day_of_month": 5, "weekday": null, "month": null, "next_due_on": "2026-11-05", "remind_time": "10:00:00",
  "is_active": true, "last_reminded_on": "2026-10-05", "version": 4 }
```

## 12. Events — SSE
`GET /events/stream` — Auth `any`, `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
```
event: hello
data: {"server_time":"2026-10-27T07:32:00Z","version":"1.0.0"}

event: change
data: {"entities":["transactions","stats","accounts"],"action_id":"6b0c…","source":"telegram","ts":"2026-10-27T07:32:05Z"}

event: ping
data: {}
```
- `ping` каждые 20 с; клиент считает связь живой, если событие было за последние 30 с.
- `entities` ⊂ `transactions, accounts, categories, merchants, settings, recurring, stats, debts` (`debts` — с 0005; старые клиенты неизвестные сущности игнорируют). Маппинг на query-ключи — `web/src/lib/api/sse.ts` (ARCH §12).
- Клиенты подключаются через `fetch` (Electron передаёт `Authorization: Bearer`; web — куки). Переподключение с backoff 1→30 с; после переподключения — `invalidateQueries()` целиком.

## 13. Telegram-бот (bot ↔ backend)
Бот — обычный клиент API: `Authorization: Bearer <BOT_SERVICE_TOKEN>` + `X-Telegram-User-Id: <from.id>`. Чужой id → 403 `telegram_forbidden` (бот и сам игнорирует не-владельца).
- Текст → `POST /assistant/messages` (`Idempotency-Key: tg:{chat_id}:{message_id}`), ответ — карточки → сообщение + inline-клавиатура.
- Кнопки → callback_data (≤ 64 байт), бот переводит их в вызовы API (`Idempotency-Key: tgcb:{callback_query.id}`; кнопки вариантов `t:{n}` — `tgt:{chat_id}:{message_id}:{n}`):

| callback_data | API |
|---|---|
| `u:{action_uuid_hex}` | `POST /actions/{uuid}/undo` (undo/redo) |
| `e:{tx_id}` | бот запоминает `edit_transaction_id` в FSM на 10 мин, отвечает «Напиши новую сумму или категорию» → следующее сообщение с `context.edit_transaction_id` |
| `a:{tx_id}:{account_id|0}` | `POST /transactions/{tx_id}/account` (`0` → `null`, «Без счёта») |
| `l:{tx_id}` | «Позже» — убрать клавиатуру |
| `c:{tx_id}` | `GET /categories/suggest?transaction_id=` → клавиатура `s:{tx_id}:{category_id}` |
| `s:{tx_id}:{category_id}` | `GET /transactions/{tx_id}` (version) → `PATCH /transactions/{tx_id}` |
| `p:{token_hex}` / `x:{token_hex}` | `POST /assistant/previews/{token}/confirm` / `cancel` |
| `t:{n}` | `send_text`: бот хранит тексты вариантов последней карточки в FSM, `n` — индекс |
| `r:{recurring_id}` / `k:{recurring_id}` | `POST /recurring/{id}/record` / `POST /recurring/{id}/skip` |
| `o:{route_key}` | «Открыть в аналитике» → ссылка `PUBLIC_URL + route` (url-кнопка вместо callback) |
| `dr:{debt_id}` / `dra:{debt_id}` | «Записать возврат» из напоминания / карточки человека: бот ждёт сумму → `POST /assistant/messages` «возврат долга: {name} {текст}»; `dra` — «Весь долг» (DEBTS.md §9) |
| `dz:{debt_id}` | «Напомнить позже» → `POST /debts/{id}/snooze {days:7}` |
| `dv:{debt_id}` | карточка человека → `GET /debts/{id}` |

- Формат ответов (DESIGN.md §12): `✅ 150 000 ₫ · Еда` (`created`), `🕓 … С какого счёта?` (`needs_account`), `❓` (`unclear`), `⚠️` (`error`), `↩️ Отменено: …` (`undone`). Бот берёт `title`/`subtitle`/`lines` как есть, экранирует HTML.
- **Outbox** (отчёты, напоминания): бот раз в `OUTBOX_POLL_SECONDS` вызывает

| Метод | Путь | Auth | Тело → Ответ |
|---|---|---|---|
| POST | `/bot/outbox/claim?limit=20` | bot-svc | → `{ "items": [{ "id", "kind", "telegram_user_id", "payload": { "text", "parse_mode": "HTML", "buttons": [[{ "text", "callback" }]] }, "attempts" }] }` |
| POST | `/bot/outbox/{id}/ack` | bot-svc | `{ "ok": true, "error": null, "retry": true }` → ok |
Отправил → `ack ok:true`; ошибка Telegram → `ok:false, retry:true` (429/сеть) или `retry:false` (403 бот заблокирован). Бот не держит своего состояния кроме FSM (MemoryStorage).
- Команды бота (DESIGN.md §12, `setMyCommands`): `/today` (`/stats/summary` + `/stats/by-category?preset=today`), `/month` (то же за месяц), `/inbox` («Разобрать»: `GET /transactions?status=needs_account` → карточки выбора счёта), `/last` (`GET /transactions?limit=5`), `/undo` (`GET /actions?recent=true` → `POST /actions/{последний}/undo`), `/settings` (отчёты/напоминания: `GET/PATCH /settings`), `/debts` (`GET /debts`, DEBTS.md §11), `/start`, `/help`.
- Callback-формат DESIGN.md §12 (`tx:undo:…`) — пример; **обязательна таблица выше** (короткие префиксы, UUID в hex без дефисов укладывается в 64 байта).
- Эмодзи категорий в боте — таблица в `bot/` по имени категории (DESIGN.md §12.7), новые — 🏷. Backend эмодзи в `title` не кладёт (кроме 🆕).
- Напоминание «✏️ Другая сумма» (DESIGN §12.8): бот ждёт текст и шлёт `POST /recurring/{id}/record { "amount_text": "11.5м" }` — сумму разбирает backend парсером.
- Ссылки «Открыть в Pocket»: маршруты web — `/`, `/operations`, `/analytics`, `/accounts`, `/settings`, `/operations?status=needs_account` («Разобрать»); параметр периода — `period=<preset>`.

## 14. Коды ошибок (сводно)
`bad_request` 400 · `unauthorized` 401 · `wrong_password` 401 (неверный текущий пароль в формах) · `csrf_failed` 403 · `forbidden` 403 · `telegram_forbidden` 403 · `not_found` 404 · `version_conflict` 409 · `already_exists` 409 · `idempotency_in_progress` 409 · `idempotency_mismatch` 409 · `undo_conflict` 409 · `preview_stale` 409 · `preview_expired` 410 · `gone` 410 · `validation_error` 422 · `idempotency_key_required` 428 · `rate_limited` 429 · `ai_unavailable` 503 · `rates_unavailable` 503 · `internal` 500.

## 15. Debts — DEBTS.md
Знак остатка: `amount > 0` — человек должен мне (`owed_to_me`), `< 0` — я должен (`i_owe`). По валютам отдельно, без пересчёта; `approx_*` — только отображение.
| Метод | Путь | Auth | Idem | Тело → Ответ | Ошибки |
|---|---|---|---|---|---|
| GET | `/debts?include_settled=false` | any | — | → `DebtsOut` | |
| GET | `/debts/{id}?limit=50&offset=0` | any | — | → `DebtDetailOut` (история, новые сверху) | 404 |
| POST | `/debts/operations` | any | req | `DebtOpCreate` → 201 `DebtOpResult` (action → `POST /actions/{id}/undo`) | 404, 422 (нет долга, сумма больше остатка при прощении, крипта с одновалютного счёта, неоднозначное имя) |
| PATCH | `/debts/{id}` | any | — | `DebtPatch` (`version`) → `DebtOut` | 404, 409 `version_conflict` / `already_exists`, 422 (архив при ненулевом остатке) |
| DELETE | `/debts/{id}` | any | — | → ok | 404, 422 (есть операции — только архив) |
| POST | `/debts/{id}/snooze` | any | — | `{ "days": 7 }` (1..90) → `DebtOut` | 404 |

```jsonc
// DebtsOut — человек с долгами в обе стороны (разные валюты) есть в обоих списках, в каждом — только свои остатки
{ "owed_to_me": [DebtOut], "i_owe": [DebtOut], "settled": [] /* только include_settled=true */,
  "totals": { "owed_to_me_vnd": "12300000", "owed_to_me_usd": "470.5", "i_owe_vnd": "1000000", "i_owe_usd": "38.3",
              "owed_to_me_count": 3, "i_owe_count": 1, "missing_rates": false } }
// DebtOut
{ "id": 5, "name": "Коля", "handle": null, "aliases": ["коле", "коли"], "note": null, "account_id": 41,
  "is_archived": false, "status": "owed_to_me" /* | i_owe | mixed | settled */,
  "balances": [ { "currency": "VND", "amount": "300000", "direction": "owed_to_me",
                  "approx_usd": "11.48", "approx_vnd": "300000", "open_since": "2026-09-27" } ],
  "last_activity_on": "2026-10-20", "remind_snoozed_until": null, "version": 2, "created_at": "…" }
// DebtDetailOut
{ "debt": DebtOut, "total": 2, "limit": 50, "offset": 0,
  "history": [ { "transaction": TransactionOut, "op": "repay_in", "currency": "VND", "delta": "-200000", "balance_after": "300000" },
               { "transaction": TransactionOut, "op": "lend",     "currency": "VND", "delta": "500000",  "balance_after": "500000" } ],
  "last_action_id": "uuid" /* аддитивно: «Отменить последнюю» → POST /actions/{id}/undo; null — нечего отменять */ }
// DebtOpCreate — ровно одно из debt_id / person_name; amount null = весь остаток (repay_*/forgive)
{ "op": "lend" /* repay_in | borrow | repay_out | forgive */, "debt_id": null, "person_name": "Коля", "handle": null,
  "amount": "500000", "currency": "VND", "account_id": null, "account_none": false, "account_amount": null,
  "description": null, "occurred_on": null, "occurred_at": null }
// DebtOpResult
{ "transaction": TransactionOut, "action_id": "uuid", "debt": DebtOut, "balance_after": "500000",
  "excess": null /* переплата сверх остатка */, "needs_account": false }
// DebtPatch
{ "version": 2, "name": "Николай", "handle": "kolya", "aliases": ["коле"], "note": "сосед", "is_archived": false }
// DebtBrief — поле `debt` карточек ассистента
{ "debt_id": 5, "name": "Коля", "op": "repay_in", "currency": "VND", "balance_after": "-100000", "excess": "100000" }
```
Операции долга — обычные `TransactionOut` (`kind` `transfer` для займов/возвратов, `expense`/`income` для прощения) с `debt != null` и `counterparty` = имя; в доходы/расходы переводы не входят. Правка — `PATCH /transactions/{id}` (для долговых transfer `account_id` = реальная сторона), выбор счёта — `POST /transactions/{id}/account`, отмена — `POST /actions/{id}/undo`. Outbox: `kind` `debt_reminder` (кнопки `dr:`/`dz:`).
