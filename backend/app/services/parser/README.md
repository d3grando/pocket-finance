# Parser (parser)

Deterministic rule-based parser: pure functions, no DB, no network. Dictionaries (accounts,
categories, merchants, own @handles, known assets, recent actions) come from `ParseContext`.

## Public API
| function | notes |
|---|---|
| `parse(text, ctx) -> ParseResult` | never raises (internal error → `needs_llm`, reason `parser_error`); ~0.2 ms/phrase |
| `amounts.parse_amount(raw, currency, small_thousands=True, *, allow_zero=False) -> Decimal` | for the validator: recompute `amount` from `amount_raw`; `currency=None` → currency written in `raw`, else VND rule; `ValueError` if not a positive amount (`allow_zero` for balances) |
| `dates.resolve_date(token, today) -> date \| None` | past/today dates only; weekend / vague period / future → None (a day without a year ≤ 60 days ahead is future, further ahead — last year: «31.12» in October) |
| `currencies.detect_currency(token) -> str \| None`, `coin_candidate(token)` | USD ≠ USDT; unknown latin ticker → candidate code (analytics verifies) |
| `periods.find_periods(text, today)` | stats periods (`PeriodSpec`) |

## ParseResult semantics
- `needs_llm=False` → intents are complete; the LLM is not needed (`confidence` ≥ 0.6).
- `needs_llm=True, partial_ok=True` → call the LLM; if AI is unavailable the parser intents are
  safe to execute. Typical: unknown words without a category (intent has `category_ref="Прочее"`
  and the words in `description`), learned merchant without category, bare amount
  (`reason="amount_only"`: «250к» may be a correction), or a single `ClarifyIntent`
  (ambiguous currency «в кошельке 120», «на выходных»).
- `needs_llm=True, partial_ok=False` → do not execute parser intents without the LLM.
- `reason` codes: `empty, too_long, vietnamese, unknown, no_amount, unknown_words, complex,
  ambiguous_category, ambiguous_currency, unknown_currency, ambiguous_amount, ambiguous_date,
  future_date, multiple_dates, multiple_amounts, multiple_accounts, per_unit, negation, debt,
  future, clause_without_amount, transfer_target, merchant_without_category, amount_only,
  question, question_amount, general_question, bulk_delete, bulk_delete_unfiltered, no_changes,
  correction, edit, exchange_amounts, suspicious_amount, parser_error`.
- Clarify without the LLM (`needs_llm=False`, one `ClarifyIntent`): a near-future date without a
  year («1 ноября аренда 12м» on 27.10 → «01.11.2025 …» / «сегодня …»), a period instead of a
  day («на прошлой неделе такси 50к»), and a suspicious correction (`reason=suspicious_amount`):
  «нет, 250к» / «исправь последнюю покупку на 150к» on a $ / crypto operation (к/м are ₫ by
  habit → «нет, 250к ₫» / «нет, 250к $» [/ «по факту списали 250к» for a ₫ account]), or a
  bare number > 100× / < 1/100 of the old $ / crypto amount. ₫ operations are not guarded.

## Output conventions (for the validator / dispatcher)
- `*_ref` are canonical names from the context (`"Карта"`, `"Кошелёк"`, `"Такси/транспорт"`,
  `"vast.ai"`); with an empty context — default names from the seeds.
- Brands (`lexicon.BRANDS`, a subset of the category keywords: «васт», hetzner, grab,
  highlands, netflix…) give `merchant_ref` = canonical name (`"vast.ai"`) so the dispatcher
  creates/learns the rule. A learned merchant with that canonical name (or alias) wins: its
  category (after a correction) replaces the keyword's. Searches/deletes filter by merchant only
  for learned rules (a built-in brand → its category filter). Two brands in one operation → no
  merchant. Income operations never get a brand merchant.
- Account matching is longest-alias-first over the whole phrase («с карты руб» → Карта RUB, not
  the generic «с карты»). An account written before everything else of the first operation
  («налом: кофе 40к, такси 50к», «с кошелька: 10 usdt впн, 20 usdt gpt») applies to all operations
  without their own account. «без счёта» → `account_none=True`.
- Own handles (`ctx.my_handles`) are never account mentions even if the my-accounts wallet has
  them as aliases: «перевёл 3 тона @myshop с кошелька» → `TransferIntent(from=Кошелёк,
  to_handle=myshop)` in any word order.
- «продал наушники за 500к» → income «Продажи» (description = the item); «продал 100 usdt»
  without the received amount → LLM (`exchange_amounts`).
- `occurred_on=None` = today. `currency` is always set on operations (never guessed: a
  multi-asset account without a currency → ClarifyIntent).
- Transfer to a foreign `@handle` is already an expense (`counterparty="@carol"`, category from the
  learned merchant handle → keywords → «Переводы людям»); own handle (`ctx.my_handles`) →
  `TransferIntent(to_handle=…)`. «для магазина/на продажу» → `for_resale=True`, «Закупка товара».
- Exchange = `TransferIntent` with `counter_amount/counter_currency`; an account written as
  «на бирже» is `from_account_ref` (both legs in one account), `to_account_ref=None`.
- «снял 2м» → `TransferIntent(from=Карта (default for ₫) or written, to=<cash account>)`.
- «васт списали 21.3м» → `UpdateTransactionIntent(changes.account_amount)`; target
  `search{merchant_ref, limit=1, sort=date_desc}` (the latest operation of that merchant; the
  canonical name when the rule is learned → the validator applies it at once via
  `last_estimated`, else the typed word → text search + preview), «по факту списали …» →
  `last`, after «Изменить» → `context`; bare «списали 1.31м» → `id` of the newest operation in
  the dialogue context charged in another currency than its single-currency account.
- «удали кофе» → `search{category_refs, limit=1}`; «удали все такси за вчера» → `limit=100`
  (both go through preview per ARCH §5.1). «удали всё» → needs_llm. Service nouns
  («расходы», «траты», «покупки», «доходы», «операции») are kinds, never search text.
- Stats: a day beats its month («сколько потратил 26 октября» → custom 26.10–26.10); «топ 3
  покупки», «самые дорогие покупки» → `StatsIntent(metric="top", limit)` (income / cheapest /
  merchant / text filters → `SearchIntent`).
- Corrections «нет, 20» use the currency of the target from `recent_actions` (no ×1000 for USD).

## Debts (debts assistant, docs/DEBTS.md §5.2)
`debts.try_debts` runs after the commands and before questions/operations (`debt_words.py` —
vocabulary, the questions detector, matching against `ctx.debts`). Debt words it cannot read
→ `needs_llm` with reason `debt` (never a guess); a message without debt meaning → `None`.
- Phrases → `DebtIntent`: `lend` («дал в долг Коле», «одолжил Коле», «Коля взял в долг», «lent
  Bob»), `repay_in` («Коля вернул долг», «Коля рассчитался», «Bob paid me back»), `borrow`
  («занял у Пети», «Петя дал мне в долг», «borrowed … from Alice»), `repay_out` («вернул Пете
  долг», «рассчитался с Петей», «repaid Alice»), `forgive` («простить долг Коле», «Коля не
  вернёт» → `owed_to_me`; «Петя простил мне долг», «списать мой долг Пете» → `i_owe`). Account,
  date, currency as for operations; «весь долг» / no amount → `amount=None` (currency = the only
  open one of that direction in `ctx.debts`, else `None`).
- docs/DEBTS.md §0 п.5: «X вернул N», «X отдал N», «X перевёл мне N», «отдал X N», «вернул X N» without
  «долг» → `fallback` (`income` / `expense`) when `ctx.debts` has an open balance of that sign in
  that currency, else the ordinary operation at once (income «Прочее» / expense «Переводы людям»,
  `counterparty` = X). X must be a known person, an @handle or a capitalised non-vocabulary word;
  «вернули 200к за наушники», «Магазин вернул 200к» stay refunds.
- Names: `person` as written (with «у»/«с» for gen/ins), `person_name` = known name or
  `person_names.nominative_guess`; a NEW person with an uncertain nominative («Лене») →
  `needs_llm=True, partial_ok=True`, reason `debt_name`. Generic nouns («другу») → LLM.
- Buttons (nominative after the colon): «дал в долг: X N», «занял: X N», «возврат долга: X [N]»
  (`op="repay"`), «списать долг: X [N] [ВАЛЮТА]» (`forgive`, direction by the balance).
- Questions without amounts → `DebtsQueryIntent`: «кто мне должен?» (`owed_to_me`), «кому я
  должен» (`i_owe`), «мои долги», «долги», «/debts», «сколько должен Коля?» (`person`).
- Several debts in one message («дал в долг Коле 500к и Пете 200к») → several intents; debts
  mixed with other operations → LLM (`debt`).
