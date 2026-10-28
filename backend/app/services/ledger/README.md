# ledger: ledger / accounts / categories / merchants — how to call it

All functions are async, take `session` first, **flush but never commit** (the route / pipeline
commits), raise `AppError` subclasses (`app/core/errors.py`).

## Actions (undo unit + dialogue context)
```python
ctx = await actions.begin_action(session, principal, ActionKind.create_transaction,
                                 input_text=text, ai_request_id=req_id)   # inserts the row
tx  = await transactions.create_transaction(session, principal, data, raw_text=text,
                                            ai_request_id=req_id, action=ctx)
row = await actions.finish_action(session, ctx)        # summary auto: «150 000 ₫ · Еда»
```
* Passing `action=` → the service records into it, **caller finishes**. Without `action` the
  service starts and finishes its own action.
* One action may hold several operations (all undone together); `ctx.transaction_ids`.
* `actions.undo_action(session, principal, action_id)` → `UndoResult` (`already_undone` on
  repeat, 409 `undo_conflict` if a record changed after the action). Redo = undo of the
  returned `undo_action_id`.
* `actions.recent_actions(session, user_id)` → `ActionBrief[]` for `ParseContext`.
* `adjust_balance(..., action=ctx)` may switch `ctx.kind` to `set_opening`; on result
  `matches` call `actions.discard_action(session, ctx)`.

## Money rules implemented (ARCH §4)
* account choice: explicit → merchant `default_account_id` → `default_for_currencies` →
  `needs_account` (no postings, counted in P&L). `account_none=True` = «без счёта».
* multi-asset wallet: coin row created on first use (`accounts.ensure_asset`).
* single-currency account, other currency → `account_amount ≈ amount × rate`,
  `is_estimated=True`; `TransactionPatch(account_amount=…)` = actual charge (only the charge
  changes). No rate at all → 503 `rates_unavailable` unless `account_amount` is given.
* rates come from `app.services.rates.service.get_rate` (USD pivot); `amount_usd/vnd` NULL when
  no rate yet (worker backfill).
* refund = `kind=refund` with an **expense** category; transfers / exchanges / adjustments
  never count in totals. `@handle` counterparty from `my_accounts` → 422 (use a transfer to
  `MyAccount.account_id`).
* `transactions.version` is bumped once per logical change (`version_id_generator=False`).

## Helpers for assistant
* `accounts.resolve_account(ref)` (name / alias / own @handle / unique prefix),
  `accounts.default_account_for(currency)`, `accounts.my_account_by_handle(handle)`.
* `categories.resolve_category(ref, kind)` → `CategoryMatch(category, exact, candidates)`;
  `create_category(..., is_new=True, action=ctx)` for 🆕; `categories.suggest(...)`.
* `merchants.match_merchant(name=|handle=)`, `merchants.learn(...)`; operations with a
  `@handle` counterparty learn the rule automatically.
* `query.resolve_target(target, ctx)`, `query.search_filter_to_filter(sf, ctx)`,
  `query.resolve_period(period, today)`, `query.last_estimated(merchant_id=…)`
  («васт списали 21.3м»).
* `bulk.create_preview(intent, matches, patch={"category_id": 7})` — refs in
  `intent.changes` must be resolved into `patch`; `bulk.confirm_preview(token)` →
  `(action_id, count)`; 409 `preview_stale`, 410 `preview_expired`.
* `summaries.to_out / to_out_many / tx_summary` for cards.

## Idempotency
Routes use `ledger.idempotency.run_idempotent`: the action row claims the `Idempotency-Key`
(`action_log.idempotency_key`, unique per user) inside the business transaction, so concurrent
duplicates create one operation and replay the stored response; another body → 409
`idempotency_mismatch`. This sits under the auth middleware (its replay wins).

## Data removal
`wipe.wipe_financial_data(session, user_id)` (re-seeds defaults, resets onboarding) and
`wipe.delete_user(session, user_id)`; `export.export_all_json`, `export.export_transactions`.

## Guarantees — rules other modules can rely on
* `TransactionOut.title` (DESIGN §9.4) = description and/or merchant/@recipient, else the
  category («обед Highlands», «аккаунт GPT Pro · @alice», «vast.ai», «Еда»); clients show the
  category as a second line when it differs.
* Undo texts toggle along the chain: «Отменено: X» → (Вернуть) «Возвращено: X» → «Отменено: X»;
  edits: «Отменено изменение: 300 000 ₫ → 250 000 ₫ · Еда». `actions.last_undoable_action`
  (text «отмени») skips `undo` actions — redo is only the «Вернуть» button.
* Money input: > 8 fractional digits → 422 «Не больше 8 знаков после точки»; derived ≈USD/≈VND or
  account charge ≥ 1e19 → 422 «Слишком большая сумма» (`common.check_amount/check_derived`).
* Same-currency account: `account_amount` must equal `amount` (422). A coin (non-fiat) cannot be
  charged from a single-currency account (422 «На счёте Карта нет TON…»), as in the validator.
* Confirmed charge from a ₫ account (`is_estimated=false`) → `amount_vnd = account_amount`.
  Income/refund credited to a ₫ account converts at the VCB **buy** rate.
* `PATCH /transactions/{id}` of an exchange accepts `counter_amount` (rate = counter/amount).
* `POST /transactions/{id}/account` without `version` only answers «С какого?»: the operation
  must still be `needs_account`, else 409 `version_conflict`. With `version` it moves a posted
  operation (web «Сменить счёт»).
* Writer lock: every ledger writer takes `pg_advisory_xact_lock_shared(PF|user_id)` before its
  first row lock (`begin_action`, `get_transaction(for_update)`, create/transfer/exchange/
  adjust/undo/merge/opening); wipe takes it exclusive, so a wipe never races an operation.
* Merchant rules never learn a fallback category («Переводы людям», «Прочее») automatically;
  a user's correction is learned whatever it is (`merchants.learn(auto=…)`).
* Deleting a «мой аккаунт» removes the @handle aliases from its wallet; the wallet created for
  it is deleted when unused, else archived.
* XLSX: text cells are literal strings (no CSV apostrophe guard), date/time are date cells,
  amounts with > 15 significant digits are exact text. Export file building runs in a thread.
