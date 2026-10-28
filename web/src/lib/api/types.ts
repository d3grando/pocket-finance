// Contract types mirroring backend/app/schemas (docs/API.md). Architect-written;
// Money/Rate are Decimal strings. Dates 'YYYY-MM-DD', datetimes ISO with offset.

export type Money = string
export type Rate = string
export type CurrencyCode = string
export type ISODate = string
export type ISODateTime = string
export type UUID = string
export type Source = 'web' | 'desktop' | 'telegram' | 'system'

export interface ApiErrorBody {
  error: { code: string; message: string; details: Record<string, unknown>; request_id: string | null }
}

export interface Page<T> { items: T[]; total: number; limit: number; offset: number }
export interface Ok { ok: true }
export interface MoneyOut { amount: Money; currency: CurrencyCode }

export type PeriodPreset =
  | 'today' | 'yesterday' | 'this_week' | 'last_week' | 'this_month' | 'last_month'
  | 'this_year' | 'last_year' | 'last_7_days' | 'last_30_days' | 'all_time' | 'custom'
export interface PeriodSpec { preset: PeriodPreset; date_from?: ISODate | null; date_to?: ISODate | null }
export interface DateRange { date_from: ISODate; date_to: ISODate; label: string }

// ---------------------------------------------------------------- auth / settings
export interface LoginIn { username?: string; password: string }
export interface MeOut {
  user_id: number; username: string; auth: 'session' | 'device' | 'bot'
  source: 'web' | 'desktop' | 'telegram'; onboarding_done: boolean; has_passkeys: boolean
  csrf_token: string | null
}
export interface DeviceLoginIn { username?: string; password: string; device_name: string; platform: 'windows' | 'macos' | 'linux' }
export interface DeviceLoginOut { token: string; device_id: UUID }
export interface DeviceOut { id: UUID; name: string; platform: string; created_at: ISODateTime; last_used_at: ISODateTime | null; is_current: boolean }
export interface SessionOut { id: UUID; user_agent: string | null; ip: string | null; created_at: ISODateTime; last_seen_at: ISODateTime; expires_at: ISODateTime; is_current: boolean }
export interface PasskeyOut { id: UUID; name: string; created_at: ISODateTime; last_used_at: ISODateTime | null }
export interface WebAuthnOptionsOut { challenge_id: UUID; options: Record<string, unknown> }
export interface UserOut { id: number; username: string; created_at: ISODateTime }
export interface PasswordChangeIn {
  current_password: string; new_password: string; revoke_other_sessions?: boolean
  /** also delete every passkey (default true on the server): a key added from a stolen session must not survive */
  remove_passkeys?: boolean
}
/** POST /passkeys/register/options — the password is required unless the cookie session is ≤ 5 min old */
export interface PasskeyRegisterOptionsIn { password: string | null }
/** POST /auth/logout-all?keep_current=…&remove_passkeys=… */
export interface LogoutAllOut { ok: boolean; sessions_revoked: number; devices_revoked: number; passkeys_removed?: number }
export interface WipeDataIn { password: string; confirm: 'УДАЛИТЬ' }

export interface SettingsOut {
  timezone: string; default_currency: CurrencyCode; analytics_currency: CurrencyCode; summary_currency: CurrencyCode
  small_amount_thousands: boolean; theme: 'light' | 'dark' | 'system'; desktop_hotkey: string
  telegram_user_id: number | null; report_monthly: boolean; report_daily: boolean; report_weekly: boolean
  report_time: string; onboarding_done: boolean; version: number
  /** daily report time (default 22:00), weekly day 0=Mon…6=Sun (default 6), weekly time (21:00) */
  report_daily_time?: string; report_weekly_day?: number; report_weekly_time?: string; reminders_enabled?: boolean
  /** debt reminders (DEBTS.md §9, migration 0005): off by default, «старше N дней» 1..365 (30) */
  debt_reminders_enabled?: boolean; debt_reminder_days?: number
}
export type SettingsPatch = Partial<Omit<SettingsOut, 'version'>> & { version: number }
export interface ServerInfoOut {
  ai_base_url: string; ai_model: string; ai_configured: boolean; telegram_bot_configured: boolean
  /** bot username without «@» (TELEGRAM_BOT_USERNAME); null/empty = not configured on the server */
  telegram_bot_username?: string | null
  version: string; public_url: string
}

// ---------------------------------------------------------------- accounts
export type AccountKind = 'bank' | 'cash' | 'card' | 'crypto_wallet' | 'exchange' | 'other'
/** output only: «debt» is the service account of a person (DEBTS.md §1), listed only with include_debts=true */
export type AccountKindOut = AccountKind | 'debt'
export interface AssetBalanceOut {
  asset: CurrencyCode; opening_balance: Money | null; opening_at: ISODateTime | null
  /** null = «остаток не задан» */
  balance: Money | null; approx_usd: Money | null; approx_vnd: Money | null
}
export interface AccountOut {
  id: number; name: string; kind: AccountKindOut; currency: CurrencyCode | null; is_multi_asset: boolean
  aliases: string[]; default_for_currencies: string[]; color: string | null; icon: string | null
  sort_order: number; is_archived: boolean; version: number; assets: AssetBalanceOut[]
}
export interface AccountCreate {
  name: string; kind: AccountKind; currency?: CurrencyCode | null; is_multi_asset?: boolean; aliases?: string[]
  default_for_currencies?: CurrencyCode[]; color?: string | null; icon?: string | null; opening_balance?: Money | null
}
export type AccountPatch = Partial<Pick<AccountOut, 'name' | 'aliases' | 'default_for_currencies' | 'color' | 'icon' | 'sort_order' | 'is_archived'>> & { kind?: AccountKind; version: number }
export interface OpeningBalanceIn { asset: CurrencyCode; amount: Money | null; opening_at?: ISODateTime | null }
export interface OnboardingBalancesIn { items: { account_id: number; asset: CurrencyCode; amount: Money }[] }
export interface BalancesOut { accounts: AccountOut[]; total_vnd: Money | null; total_usd: Money | null; unknown_count: number }
export interface MyAccountOut { id: number; handle: string; label: string; account_id: number }
export interface MyAccountCreate { handle: string; label?: string | null; account_id?: number | null }

// ---------------------------------------------------------------- categories / merchants
export interface CategoryOut {
  id: number; name: string; kind: 'expense' | 'income'; is_business: boolean; icon: string | null; color: string | null
  aliases: string[]; is_system: boolean; is_new: boolean; is_archived: boolean; sort_order: number; version: number
  tx_count: number | null
}
export interface CategoryCreate { name: string; kind: 'expense' | 'income'; is_business?: boolean; icon?: string | null; color?: string | null; aliases?: string[] }
export type CategoryPatch = Partial<Pick<CategoryOut, 'name' | 'is_business' | 'icon' | 'color' | 'aliases' | 'sort_order' | 'is_archived' | 'is_new'>> & { version: number }
export interface CategorySuggestions { similar: { category: CategoryOut; score: number }[]; all: CategoryOut[] }
export interface MerchantOut {
  id: number; name: string; aliases: string[]; handle: string | null; category_id: number | null
  category_name: string | null; default_account_id: number | null; hits: number; last_used_at: ISODateTime | null
}
export interface MerchantCreate { name: string; aliases?: string[]; handle?: string | null; category_id?: number | null; default_account_id?: number | null }
export type MerchantPatch = Partial<MerchantCreate>

// ---------------------------------------------------------------- transactions
export type TxKind = 'expense' | 'income' | 'refund' | 'transfer' | 'exchange' | 'adjustment'
export type TxStatus = 'posted' | 'needs_account'
export interface AccountRef { id: number; name: string; icon: string | null }
export interface CategoryRef { id: number; name: string; kind: 'expense' | 'income'; icon: string | null; color: string | null; is_business: boolean; is_new: boolean }
export interface MerchantRef { id: number; name: string }
export interface TransactionOut {
  id: number; kind: TxKind; status: TxStatus; occurred_at: ISODateTime; occurred_on: ISODate
  amount: Money; currency: CurrencyCode
  account: AccountRef | null; account_amount: Money | null; account_asset: CurrencyCode | null; account_rate: Rate | null; is_estimated: boolean
  counter_account: AccountRef | null; counter_amount: Money | null; counter_asset: CurrencyCode | null
  category: CategoryRef | null; is_business: boolean; merchant: MerchantRef | null
  counterparty: string | null; description: string | null
  amount_usd: Money | null; amount_vnd: Money | null; usd_rate: Rate | null; rate_date: ISODate | null; rate_source: string | null
  refund_of_id: number | null; recurring_id: number | null; source: Source; raw_text: string | null
  created_action_id: UUID | null; version: number; created_at: ISODateTime; updated_at: ISODateTime
  title: string
  /** debt operation (DEBTS.md §7, additive): null / absent for ordinary operations and old servers */
  debt?: TxDebtRef | null
}
export interface TransactionCreate {
  kind: 'expense' | 'income' | 'refund'; amount: Money; currency: CurrencyCode; category_id: number
  account_id?: number | null; account_none?: boolean; account_amount?: Money | null; merchant_id?: number | null
  counterparty?: string | null; description?: string | null; occurred_on?: ISODate | null; occurred_at?: ISODateTime | null
  refund_of_id?: number | null; recurring_id?: number | null
}
export type TransactionPatch = Partial<Omit<TransactionCreate, 'refund_of_id' | 'recurring_id'>> & { version: number }
export interface TransactionFilter {
  date_from?: ISODate; date_to?: ISODate; kind?: TxKind[]; category_id?: number[]; account_id?: number[]
  currency?: CurrencyCode; min_amount?: Money; max_amount?: Money; amount_currency?: CurrencyCode
  q?: string; is_business?: boolean; status?: TxStatus; merchant_id?: number
  sort?: 'date_desc' | 'date_asc' | 'amount_desc' | 'amount_asc'; limit?: number; offset?: number
}
export interface TransactionTotals { expense_vnd: Money; income_vnd: Money; expense_usd: Money; income_usd: Money; count: number }
export interface TransactionPage extends Page<TransactionOut> { totals: TransactionTotals }
export interface MutationResult { transaction: TransactionOut; action_id: UUID }
export interface TransferCreate { from_account_id: number; to_account_id: number; amount: Money; currency: CurrencyCode; description?: string | null; occurred_on?: ISODate | null }
export interface ExchangeCreate extends TransferCreate { counter_amount: Money; counter_currency: CurrencyCode }
export interface AdjustmentCreate { account_id: number; asset: CurrencyCode; actual_balance: Money }
export interface AdjustmentResult {
  kind: 'adjusted' | 'opening_set' | 'matches'; account_id: number; asset: CurrencyCode
  previous_balance: Money | null; balance: Money; delta: Money | null; transaction: TransactionOut | null; action_id: UUID | null
}

// ---------------------------------------------------------------- history
export interface ActionOut {
  id: UUID; kind: string; summary: string; source: Source; input_text: string | null
  entity_refs: { type: string; id: number | string }[]; is_undoable: boolean; undone_at: ISODateTime | null; created_at: ISODateTime
}
export interface UndoResult { status: 'undone' | 'already_undone'; undone_action_id: UUID; undo_action_id: UUID | null; summary: string; changed: string[] }
export interface AuditEntryOut {
  id: number; entity_type: string; entity_id: string; operation: 'create' | 'update' | 'delete' | 'restore' | 'merge'
  before: Record<string, unknown> | null; after: Record<string, unknown> | null; action_id: UUID | null; source: Source; created_at: ISODateTime
}

// ---------------------------------------------------------------- assistant (cards: DESIGN.md §9.2)
export interface SearchFilter {
  period?: PeriodSpec | null; kinds?: TxKind[] | null; category_refs?: string[]; category_ids?: number[]
  account_refs?: string[]; account_ids?: number[]; currency?: CurrencyCode | null; min_amount?: Money | null
  max_amount?: Money | null; amount_currency?: CurrencyCode | null; text?: string | null; counterparty?: string | null
  merchant_ref?: string | null; is_business?: boolean | null; needs_account?: boolean | null
  sort?: 'date_desc' | 'date_asc' | 'amount_desc' | 'amount_asc'; limit?: number
}
export interface AssistantMessageIn { text: string; context?: { edit_transaction_id?: number | null } | null }

export type ButtonActionKind =
  | 'undo' | 'edit' | 'choose_account' | 'later' | 'change_category' | 'set_category'
  | 'confirm_preview' | 'cancel_preview' | 'send_text' | 'manual' | 'retry' | 'open' | 'redo'
export interface ButtonAction {
  kind: ButtonActionKind; action_id?: UUID | null; transaction_id?: number | null; account_id?: number | null
  category_id?: number | null; preview_token?: UUID | null; text?: string | null; route?: string | null
  prefill?: Record<string, string> | null
}
export interface Button {
  id: string; label: string; style: 'primary' | 'secondary' | 'outline' | 'ghost' | 'destructive'
  hotkey: string | null; action: ButtonAction
}
interface CardBase { title: string; subtitle: string | null; buttons: Button[] }
export interface CreatedCard extends CardBase {
  kind: 'created'; action_id: UUID; transaction: TransactionOut; category_is_new: boolean; approx: string | null; note: string | null
  /** debt operation (DEBTS.md §8), additive — old servers / ordinary operations: absent */
  debt?: DebtBrief | null
}
export interface CreatedManyCard extends CardBase {
  kind: 'created_many'; action_ids: UUID[]; transactions: TransactionOut[]; total: Money | null; total_currency: CurrencyCode | null
}
export interface AccountOption { account_id: number | null; label: string; hint: string | null; hotkey: string | null }
export interface NeedsAccountCard extends CardBase {
  kind: 'needs_account'; action_id: UUID; transaction: TransactionOut; question: string; options: AccountOption[]
  debt?: DebtBrief | null
}
export interface UpdatedCard extends CardBase { kind: 'updated'; action_id: UUID; transaction: TransactionOut; changes: string[]; debt?: DebtBrief | null }
export interface DeletedCard extends CardBase { kind: 'deleted'; action_id: UUID; transaction: TransactionOut }
export interface PreviewItem { transaction: TransactionOut; before: string | null; after: string | null }
export interface PreviewCard extends CardBase {
  kind: 'preview'; preview_token: UUID; operation: 'bulk_update' | 'bulk_delete'; change_summary: string
  items: PreviewItem[]; total_count: number; total: string | null; expires_at: ISODateTime
}
export interface AppliedCard extends CardBase { kind: 'applied'; action_id: UUID; count: number }
export interface AnswerLine {
  label: string; value: string; amount: Money | null; currency: CurrencyCode | null; share: number | null
  category_id: number | null; transaction_id: number | null; icon: string | null
}
export interface AnswerCard extends CardBase {
  kind: 'answer'; answer_type: 'stats' | 'list' | 'compare' | 'balance' | 'text'; headline: string | null
  headline_approx: string | null; lines: AnswerLine[]; transactions: TransactionOut[]; total_count: number | null
  filter: SearchFilter | null; text: string | null
  /** «кто мне должен?» (answer_type "balance"): structured lists for new clients (DEBTS.md §8) */
  debts?: DebtsOut | null
}
export interface BalanceCard extends CardBase {
  kind: 'balance'; action_id: UUID | null; account: AccountOut; result: 'adjusted' | 'opening_set' | 'matches'; transaction: TransactionOut | null
}
export interface CategoryCard extends CardBase { kind: 'category'; action_id: UUID; category: CategoryOut }
export interface UndoneCard extends CardBase { kind: 'undone'; undone_action_id: UUID; undo_action_id: UUID | null }
export interface UnclearCard extends CardBase {
  kind: 'unclear'; reason: 'ambiguous_currency' | 'ambiguous_target' | 'missing_amount' | 'unknown' | 'other'
  question: string; keep_input: boolean
}
export interface ErrorCard extends CardBase {
  kind: 'error'
  code: 'ai_unavailable' | 'validation_failed' | 'not_found' | 'conflict' | 'preview_expired' | 'preview_stale' | 'nothing_to_undo' | 'internal'
  text: string | null; keep_input: boolean
}
export type Card =
  | CreatedCard | CreatedManyCard | NeedsAccountCard | UpdatedCard | DeletedCard | PreviewCard | AppliedCard
  | AnswerCard | BalanceCard | CategoryCard | UndoneCard | UnclearCard | ErrorCard
export interface AssistantResponse {
  request_id: number | null; route: 'parser' | 'llm' | 'parser_llm' | 'none'; cards: Card[]
  action_ids: UUID[]; changed: Entity[]; ai_available: boolean
}

// ---------------------------------------------------------------- stats
export type StatsCurrency = 'VND' | 'USD'
export interface KpiOut { value: Money | null; currency: CurrencyCode; approx_usd: Money | null; change_ratio: Money | null; sparkline: Money[] }
export interface SummaryOut {
  currency: CurrencyCode; today: DateRange; month: DateRange; spent_today: KpiOut; spent_month: KpiOut
  income_month: KpiOut; net_month: KpiOut; needs_account_count: number; missing_rates_count: number
}
export interface CategorySlice {
  category_id: number; name: string; icon: string | null; color: string | null; is_business: boolean; is_new: boolean
  amount: Money; share: Money; count: number
}
export interface ByCategoryOut { range: DateRange; kind: 'expense' | 'income'; currency: CurrencyCode; total: Money; items: CategorySlice[] }
export interface SeriesPoint { date: ISODate; label: string; expense: Money; income: Money; net: Money }
export interface SeriesOut { range: DateRange; granularity: 'day' | 'month'; currency: CurrencyCode; points: SeriesPoint[] }
export interface TopItem {
  transaction_id: number; occurred_on: ISODate; title: string; category: string | null; amount: Money
  currency: CurrencyCode; original_amount: Money; original_currency: CurrencyCode
}
export interface TopOut { range: DateRange; currency: CurrencyCode; items: TopItem[] }
export interface DailyAverageOut { range: DateRange; currency: CurrencyCode; days: number; total: Money; average: Money }
export interface CompareSide { range: DateRange; expense: Money; income: Money; net: Money }
export interface CompareOut {
  currency: CurrencyCode; current: CompareSide; previous: CompareSide; expense_change_ratio: Money | null
  income_change_ratio: Money | null; by_category: CategorySlice[]
}
export interface BusinessOut {
  range: DateRange; currency: CurrencyCode; business_income: Money; business_expense: Money; business_net: Money
  personal_income: Money; personal_expense: Money; expense_by_category: CategorySlice[]; income_by_category: CategorySlice[]
}

// ---------------------------------------------------------------- rates / search / recurring / reports / events
export interface RateOut {
  base: CurrencyCode; quote: CurrencyCode; rate: Rate; rate_date: ISODate; kind: string; source: string
  fetched_at: ISODateTime | null; is_fallback: boolean; label: string
}
export interface ConvertOut { amount: Money; currency: CurrencyCode; converted: Money; to: CurrencyCode; rate: RateOut }
export interface RatesLatestOut { usd_vnd: RateOut | null; usd_rub: RateOut | null; crypto: RateOut[] }
export interface SearchHit { type: 'transaction' | 'category' | 'account' | 'merchant'; id: number; title: string; subtitle: string | null; route: string }
export interface SearchOut { query: string; hits: SearchHit[] }
export type Frequency = 'daily' | 'weekly' | 'monthly' | 'yearly'
export interface RecurringOut {
  id: number; title: string; kind: 'expense' | 'income'; amount: Money | null; currency: CurrencyCode
  account_id: number | null; category_id: number | null; merchant_id: number | null; description: string | null
  frequency: Frequency; interval: number; day_of_month: number | null; weekday: number | null; month: number | null
  next_due_on: ISODate; remind_time: string; is_active: boolean; last_reminded_on: ISODate | null; version: number
}
export type RecurringCreate = Omit<RecurringOut, 'id' | 'is_active' | 'last_reminded_on' | 'version' | 'remind_time' | 'interval' | 'day_of_month' | 'weekday' | 'month' | 'account_id' | 'category_id' | 'merchant_id' | 'description' | 'amount'> &
  Partial<Pick<RecurringOut, 'remind_time' | 'interval' | 'day_of_month' | 'weekday' | 'month' | 'account_id' | 'category_id' | 'merchant_id' | 'description' | 'amount'>>
export type RecurringPatch = Partial<Omit<RecurringOut, 'id' | 'kind' | 'last_reminded_on' | 'version'>> & { version: number }
export interface RecurringRecordIn { amount?: Money | null; amount_text?: string | null; currency?: CurrencyCode | null; account_id?: number | null; occurred_on?: ISODate | null }
export interface ReportOut { kind: 'daily' | 'weekly' | 'monthly'; range: DateRange; text_html: string; text_plain: string }

export type Entity = 'transactions' | 'accounts' | 'categories' | 'merchants' | 'settings' | 'recurring' | 'stats' | 'debts'
export interface ChangeEvent { entities: Entity[]; action_id: UUID | null; source: string | null; ts: ISODateTime }
export interface HelloEvent { server_time: ISODateTime; version: string }
export interface HealthOut { status: 'ok' | 'degraded'; db: boolean; version: string; server_time: ISODateTime }

// ---------------------------------------------------------------- debts (DEBTS.md, API.md §15)
// Sign: amount > 0 — the person owes me («owed_to_me»), < 0 — I owe the person («i_owe»). Per currency,
// never converted; approx_* are display only.
export type DebtOpKind = 'lend' | 'repay_in' | 'borrow' | 'repay_out' | 'forgive'
export type DebtDirection = 'owed_to_me' | 'i_owe'
export type DebtStatus = 'owed_to_me' | 'i_owe' | 'mixed' | 'settled'
/** TransactionOut.debt */
export interface TxDebtRef { id: number; name: string; op: DebtOpKind }
export interface DebtBalanceOut {
  currency: CurrencyCode; amount: Money; direction: DebtDirection
  approx_usd: Money | null; approx_vnd: Money | null
  /** local date the current non-zero balance started (age) */
  open_since: ISODate | null
}
export interface DebtOut {
  id: number; name: string; handle: string | null; aliases: string[]; note: string | null; account_id: number
  is_archived: boolean; status: DebtStatus; balances: DebtBalanceOut[]; last_activity_on: ISODate | null
  remind_snoozed_until: ISODate | null; version: number; created_at: ISODateTime
}
export interface DebtTotalsOut {
  owed_to_me_vnd: Money | null; owed_to_me_usd: Money | null; i_owe_vnd: Money | null; i_owe_usd: Money | null
  owed_to_me_count: number; i_owe_count: number; missing_rates: boolean
}
export interface DebtsOut { owed_to_me: DebtOut[]; i_owe: DebtOut[]; settled: DebtOut[]; totals: DebtTotalsOut }
export interface DebtHistoryItem { transaction: TransactionOut; op: DebtOpKind; currency: CurrencyCode; delta: Money; balance_after: Money }
/** `last_action_id` (additive): the action «Отменить последнюю» undoes — absent on older servers */
export interface DebtDetailOut { debt: DebtOut; history: DebtHistoryItem[]; total: number; limit: number; offset: number; last_action_id?: UUID | null }
/** exactly one of debt_id / person_name; amount null = the whole remaining balance (repay_* / forgive) */
export interface DebtOpCreate {
  op: DebtOpKind; debt_id?: number | null; person_name?: string | null; handle?: string | null
  amount?: Money | null; currency: CurrencyCode; account_id?: number | null; account_none?: boolean
  account_amount?: Money | null; description?: string | null; occurred_on?: ISODate | null; occurred_at?: ISODateTime | null
}
export interface DebtOpResult {
  transaction: TransactionOut; action_id: UUID; debt: DebtOut; balance_after: Money; excess: Money | null; needs_account: boolean
}
export interface DebtPatch {
  version: number; name?: string | null; handle?: string | null; aliases?: string[] | null; note?: string | null; is_archived?: boolean | null
}
/** `debt` of assistant cards (created / needs_account / updated) */
export interface DebtBrief { debt_id: number; name: string; op: DebtOpKind; currency: CurrencyCode; balance_after: Money; excess: Money | null }
