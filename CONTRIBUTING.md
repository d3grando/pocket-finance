# Как помочь проекту

Спасибо за интерес к Pocket Finance! Issues и pull request'ы приветствуются.
Уязвимости — только приватно, см. [`SECURITY.md`](SECURITY.md).

## Окружение

- Python 3.12 и [`uv`](https://docs.astral.sh/uv/) — `backend/`, `bot/`
- Node 22 и pnpm 12 — `web/`, `desktop/`
- PostgreSQL 16 — для тестов backend
- Docker с `docker compose` — для проверки всего стека

Локальный запуск (нужны `DATABASE_URL` и `APP_ENV=dev`):

```bash
cd backend && uv sync
uv run alembic upgrade head                 # схема БД
uv run python -m app.cli create-owner       # владелец, стартовые счета и категории; пароль печатается один раз
uv run uvicorn app.main:app --reload        # API на http://127.0.0.1:8000
cd ../web && pnpm install && pnpm dev       # веб, прокси /api на http://127.0.0.1:8000
```
 Без AI-ключа задайте `AI_ENABLED=false`; для ручной проверки AI-сценариев
есть фейковый сервер `backend/tests/ai/fake_llm_serve.py`.

## Тесты

```bash
# backend: БД из TEST_DATABASE_URL (по умолчанию pf_main_test) создаётся сама,
# если роль может создавать базы
export TEST_DATABASE_URL=postgresql+asyncpg://pf:pf@127.0.0.1:5432/pf_main_test
cd backend && uv run ruff check . && uv run pytest

cd bot && uv run ruff check pocket_bot tests && uv run pytest
cd web && pnpm lint && pnpm test && pnpm build      # e2e: pnpm e2e (Playwright)
cd desktop && pnpm typecheck && pnpm test && pnpm build
scripts/test-stack.sh                                # весь стек в Docker
```

Тесты не ходят в интернет: LLM — локальный фейковый сервер, Telegram и HTTP — моки. Тесты против
живого API включаются только явно (`PF_LIVE=1`).

## Правила

- **Деньги — только `Decimal` / `NUMERIC`**, никакого float ни в Python, ни во фронте
  (на фронте суммы приходят строками). Время — с часовым поясом.
- Код и комментарии — на английском; **тексты интерфейса и бота — на русском**.
- Стиль: `ruff` (backend, bot; строка 100), `eslint` + `tsc` (web, desktop).
- Контракты (`backend/app/schemas/`, `docs/API.md`) меняются только аддитивно — старые клиенты
  (бот, Electron) должны продолжать работать.
- Каждое исправление — с регрессионным тестом, каждая фича — с тестами. CI
  (`.github/workflows/ci.yml`) должен быть зелёным.
- Не коммитьте `.env`, ключи, личные данные; в примерах — `example.com`, вымышленные имена и
  `@alice`/`@bob`.

## Pull request

1. Форк → ветка от `main`.
2. Небольшие, сфокусированные изменения; описание «что и зачем», скриншот для UI.
3. Отправляя PR, вы соглашаетесь, что ваш вклад распространяется под лицензией [MIT](LICENSE).
