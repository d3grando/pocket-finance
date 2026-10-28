# pocket-bot — Telegram-бот Pocket Finance

aiogram 3.31, long polling, отдельный сервис. Ходит в тот же backend `/api/v1`
(`Authorization: Bearer BOT_SERVICE_TOKEN` + `X-Telegram-User-Id`, source = telegram).
Контракт — `docs/API.md` §9 и §13, тексты — `docs/DESIGN.md` §12.

## Запуск

```sh
cd bot
uv sync
TELEGRAM_BOT_TOKEN=... BOT_SERVICE_TOKEN=... API_BASE_URL=http://api:8000/api/v1 \
  uv run python -m pocket_bot
```

| env | по умолчанию | |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — (обязателен) | токен @your_pocket_bot |
| `BOT_SERVICE_TOKEN` | — (обязателен) | общий с backend |
| `API_BASE_URL` | `http://api:8000/api/v1` | |
| `TELEGRAM_ALLOWED_IDS` | пусто (никому) | ваш id, например `123456789`; через запятую; остальным — молчание |
| `PUBLIC_URL` | пусто (без кнопок-ссылок) | адрес сайта, например `https://pocket.example.com`, для URL-кнопок «Открыть в Pocket» |
| `TIMEZONE` | `Asia/Ho_Chi_Minh` | «сегодня/вчера», даты `/report`; в docker compose берётся из `DEFAULT_TIMEZONE` |
| `OUTBOX_POLL_SECONDS` | `5` | опрос `/bot/outbox/claim` |
| `OUTBOX_BATCH` | `20` | |
| `API_TIMEOUT` | `30` | секунд на запрос к backend |
| `LOG_LEVEL` | `INFO` | |

На один токен — один процесс: перед стартом бот снимает webhook (необработанные апдейты остаются),
а если тот же токен опрашивает другой экземпляр (Telegram отвечает 409 Conflict несколько раз
подряд), бот пишет CRITICAL и выходит с кодом 1. Пустой `TELEGRAM_ALLOWED_IDS` — бот никому не
отвечает (WARNING при старте). В логах нет текстов сообщений и Telegram ID: httpx и `aiogram.event` —
только WARNING и выше, разрешённые ID — только их количество.

## Что умеет

- Любой текст → `POST /assistant/messages` (`Idempotency-Key: tg:{chat}:{message_id}`), карточки →
  сообщения: `✅ 150 000 ₫ · Еда` + `[↩️ Отменить] [✏️ Изменить]`, 🆕 + `[🗂 В другую категорию]`,
  «С какого счёта?» кнопками счетов + `[Без счёта] [⏳ Позже]`, превью массовых изменений
  `[✅ Применить] [✖️ Отмена]`, ответы статистики, «Не понял» с вариантами, ошибки.
- «Изменить» → «Напишите новую сумму или категорию» → следующее сообщение уходит с
  `context.edit_transaction_id` (10 минут, `[✖️ Отмена]`, любая команда сбрасывает).
- `/undo` смотрит только самое последнее действие (`GET /actions?recent=true`, первое в списке):
  операцию или «Вернуть» отменяет (как кнопка ↩️), если последнее — уже отмена, отвечает
  «Последнее действие уже отменено» с `[↪️ Вернуть]`; к более старым действиям не переходит.
  «Отменить все» под несколькими операциями берёт `card.action_ids` (все id сообщения).
- Команды: `/start /help /today /month /report [прошлый|неделя|день] /inbox /last /debts /undo
  /settings` (меню — `setMyCommands` при старте).
- **Долги** (docs/DEBTS.md §9, §11): `/debts` → `GET /debts`: «Мне должны ≈ … / Я должен ≈ …»,
  по человеку остатки по валютам (≈ $ рядом с не-₫), возраст «30 дн.»; кнопки `👤 Имя` `dv:{id}`
  (до 8) + «Открыть в Pocket» `/accounts?debt=all`; пусто — «Долгов нет 👌». `dv:{id}` →
  карточка человека (`GET /debts/{id}?limit=10`: остатки + история) с `[💰 Записать возврат]`
  `dr:{id}` и `[🤝 Списать остаток]` (send_text «списать долг: Имя» через ассистента, сразу +
  «Отменить»). `dr:{id}` → «Сколько вернул Имя?» (имя — из `GET /debts/{id}`), ответ «200к» /
  «200к налом» уходит ассистенту как «возврат долга: Имя 200к» (`tg:{chat}:{msg}`), «весь»/«всё»
  или кнопка `[✅ Весь долг]` `dra:{id}` → «возврат долга: Имя» (ключ `tgdra:{chat}:{msg}` —
  двойное нажатие не создаст второй возврат); текст, не похожий на сумму («кофе 85к»), — обычное
  сообщение. После записи у напоминания и подсказки убираются кнопки. `dz:{id}` →
  `POST /debts/{id}/snooze {days:7}` → «⏰ Напомню через 7 дней» (кнопка «Записать возврат»
  остаётся). Карточки ассистента по долгам — обычный рендер (`title`/`subtitle`/`note`); бот
  подписывает долговые операции «Дал в долг: Имя», у притока (`repay_in`/`borrow`) реальный счёт —
  `counter_account`, у «С какого?» — «Долг записал. …»; под ответом «кто мне должен» — кнопки
  `dv:`. `/settings` — тумблер «Напоминать о долгах» (`g:b`, поле `debt_reminders_enabled`).
- send_text-варианты (`t:{n}`) запоминаются по сообщению: кнопка под старой карточкой (карточка
  человека, уточнение) шлёт свой текст, даже если после неё пришла другая карточка с вариантами.
  Ключ идемпотентности такой кнопки — `tgt:{chat}:{message_id}:{n}`: двойной тап не создаёт дубль.
- Исходящие: раз в `OUTBOX_POLL_SECONDS` — claim → отправка → ack (отчёты, напоминания).
- Доступ: только `TELEGRAM_ALLOWED_IDS` и только личный чат; чужим — ничего (даже callback не
  подтверждается). Backend проверяет то же самое ещё раз.
- Все строки от пользователя/backend экранируются (`html.escape`). Исключения — готовый
  Telegram-HTML из backend: `ReportOut.text_html` и `payload.text` outbox (backend экранирует сам);
  если Telegram отверг разметку — бот шлёт тот же текст без тегов.
- Ошибки backend — короткие честные ответы (`⚠️ Сервер недоступен. Ничего не записано.`,
  при таймауте — «если запись прошла, повтор не создаст дубль», `⏳ Слишком часто. Подождите 7 с.`)
  с `[🔁 Повторить]` там, где повтор безопасен (тот же ключ идемпотентности).

## callback_data (≤ 64 байт)

Из API.md §13 (их же может присылать backend в outbox):
`u:{action_hex}` undo/redo · `e:{tx}` изменить · `a:{tx}:{acc|0}` счёт · `l:{tx}` позже ·
`c:{tx}` в другую категорию · `s:{tx}:{cat}` выбрать категорию · `p:{hex}`/`x:{hex}` превью ·
`t:{n}` вариант ответа · `r:{rid}`/`k:{rid}` записать/пропустить напоминание ·
`dr:{debt}`/`dra:{debt}` записать возврат / весь долг · `dz:{debt}` напомнить о долге позже ·
`dv:{debt}` карточка человека ·
`o:{route_key}` → URL-кнопка (`/analytics?period=…` как есть, `home|ops|inbox|accounts|settings|
analytics`, `analytics_<preset>`).

Локальные для бота (backend может использовать `m:` и `i` в outbox):
`m:{rid}` «✏️ Другая сумма» → ждёт ответ («11.5м» → `POST /recurring/{id}/record {amount_text}`) ·
`i` показать «Разобрать» · `n:{tx}` «🔍 Другая…» категория по названию · `b:{tx}` «‹ Назад» ·
`y:{msg}:{s|n}` повтор (s — тот же ключ) · `U:{key}` «Отменить все» · `M:{key}` выбор операции
для правки · `g:{m|d|w|b}` переключатели отчётов и напоминаний о долгах в `/settings` · `z` отмена
ожидания ввода.
`U:/M:` хранят данные в памяти процесса (LRU, 3 суток); после рестарта кнопка ответит
«Кнопка устарела — повторите запрос».

## Outbox: что бот ждёт от backend

`POST /bot/outbox/claim?limit=20` → `{"items":[{"id","kind","telegram_user_id","payload":{"text",
"parse_mode":"HTML","buttons":[[{"text","callback"}]]},"attempts"}]}`. Бот шлёт только в
`TELEGRAM_ALLOWED_IDS` (иначе `ack ok:false retry:false`). Ack: успех → `{"ok":true}`;
429/сеть/5xx Telegram → `retry:true`; бот заблокирован / chat not found / bad request →
`retry:false`. Пример напоминания (DESIGN §12.8):
`[[{"text":"✅ Записать","callback":"r:2"},{"text":"⏭ Пропустить","callback":"k:2"}],
[{"text":"✏️ Другая сумма","callback":"m:2"}]]`; месячный отчёт:
`[[{"text":"📊 Подробно","callback":"o:analytics_last_month"},{"text":"📥 Разобрать","callback":"i"}]]`;
напоминание о долге (`kind=debt_reminder`, DEBTS.md §9):
`[[{"text":"Записать возврат","callback":"dr:5"},{"text":"Напомнить позже","callback":"dz:5"}]]`.

## Тесты

```sh
cd bot && uv run pytest -q tests && uv run ruff check pocket_bot tests
```

- Апдейты подаются через `Dispatcher.feed_update`, Bot API — `MockedSession` (запись всех методов),
  backend — `httpx.MockTransport` (`tests/conftest.py`). Polling с боевым токеном не запускается.
- Фикстуры карточек/ответов `tests/fixtures/contract.json` собраны **настоящими** Pydantic-схемами
  backend: `cd bot && PYTHONPATH=../backend uv run --no-project --with pydantic==2.14.1 python
  tests/contract/gen_fixtures.py`.
- Таблица маршрутов backend `tests/fixtures/openapi_routes.json` (тест проверяет, что каждый вызов
  клиента попадает в существующий маршрут): `cd backend && APP_ENV=test uv run python
  ../bot/tests/contract/gen_openapi.py`.
- Живой прогон против поднятого backend:
  `PF_BACKEND_URL=http://127.0.0.1:8000/api/v1 PF_BOT_SERVICE_TOKEN=… uv run pytest tests/test_integration.py`.
  Долги: кнопки `dv/dr/dra/dz` на несуществующего человека должны честно ответить (⚠️ тостом или
  сообщением); полный сценарий (дал в долг → `/debts` → карточка → `dz` → «Весь долг» → отмена
  всего) запускается сам, когда `GET /debts` перестаёт отвечать 501 (debts service выкачен).
