// Typed endpoint functions for every route in docs/API.md.
// Mutations that require Idempotency-Key (API.md «Idem: req») take a `key` argument — callers
// generate it once per user attempt (newIdempotencyKey) and reuse it on retry.
import { api, apiBaseUrl } from './client'
import type * as T from './types'

type Q = Record<string, string | number | boolean | (string | number)[] | null | undefined>
export interface OkOut { ok: true }

export interface StatsPeriodParams { preset?: T.PeriodPreset; date_from?: T.ISODate; date_to?: T.ISODate }

export const authApi = {
  login: (body: T.LoginIn) => api.post<T.MeOut>('/auth/login', { username: 'owner', ...body }),
  logout: () => api.post<OkOut>('/auth/logout'),
  /** «Выйти со всех устройств»: keeps this session; passkeys are removed unless removePasskeys=false */
  logoutAll: (o: { keepCurrent?: boolean; removePasskeys?: boolean } = {}) =>
    api.post<T.LogoutAllOut>('/auth/logout-all', {}, {
      query: { keep_current: o.keepCurrent ?? true, remove_passkeys: o.removePasskeys ?? true },
    }),
  me: () => api.get<T.MeOut>('/auth/me'),
  deviceLogin: (body: T.DeviceLoginIn) => api.post<T.DeviceLoginOut>('/auth/device-login', { username: 'owner', ...body }),
  sessions: () => api.get<T.SessionOut[]>('/auth/sessions'),
  revokeSession: (id: T.UUID) => api.delete<OkOut>(`/auth/sessions/${id}`),
}

export const passkeysApi = {
  /** password: null first — a fresh session (≤ 5 min after login) passes, otherwise 403 reauth_required */
  registerOptions: (password: string | null = null) =>
    api.post<T.WebAuthnOptionsOut>('/passkeys/register/options', { password } satisfies T.PasskeyRegisterOptionsIn),
  registerVerify: (body: { challenge_id: T.UUID; credential: unknown; name: string }) =>
    api.post<T.PasskeyOut>('/passkeys/register/verify', body),
  loginOptions: (username = 'owner') => api.post<T.WebAuthnOptionsOut>('/passkeys/login/options', { username }),
  loginVerify: (body: { challenge_id: T.UUID; credential: unknown }) => api.post<T.MeOut>('/passkeys/login/verify', body),
  list: () => api.get<T.PasskeyOut[]>('/passkeys'),
  rename: (id: T.UUID, name: string) => api.patch<T.PasskeyOut>(`/passkeys/${id}`, { name }),
  remove: (id: T.UUID) => api.delete<OkOut>(`/passkeys/${id}`),
}

export const devicesApi = {
  list: () => api.get<T.DeviceOut[]>('/devices'),
  revoke: (id: T.UUID) => api.delete<OkOut>(`/devices/${id}`),
}

export const usersApi = {
  me: () => api.get<T.UserOut>('/users/me'),
  changePassword: (body: T.PasswordChangeIn) => api.post<OkOut>('/users/me/password', body),
  wipe: (body: T.WipeDataIn) => api.post<OkOut>('/users/me/wipe', body),
}

export const settingsApi = {
  get: () => api.get<T.SettingsOut>('/settings'),
  patch: (body: T.SettingsPatch) => api.patch<T.SettingsOut>('/settings', body),
  server: () => api.get<T.ServerInfoOut>('/settings/server'),
}

const txQuery = (f: T.TransactionFilter = {}): Q => ({ ...f } as Q)

export const transactionsApi = {
  list: (f: T.TransactionFilter = {}, signal?: AbortSignal) => api.get<T.TransactionPage>('/transactions', { query: txQuery(f), signal }),
  needsAccountCount: () => api.get<{ count: number }>('/transactions/needs-account/count'),
  get: (id: number) => api.get<T.TransactionOut>(`/transactions/${id}`),
  create: (body: T.TransactionCreate, key: string) => api.post<T.MutationResult>('/transactions', body, { idempotencyKey: key }),
  patch: (id: number, body: T.TransactionPatch, key?: string) => api.patch<T.MutationResult>(`/transactions/${id}`, body, { idempotencyKey: key }),
  remove: (id: number, version: number, key?: string) =>
    api.delete<T.MutationResult>(`/transactions/${id}`, { query: { version }, idempotencyKey: key }),
  restore: (id: number, key?: string) => api.post<T.MutationResult>(`/transactions/${id}/restore`, {}, { idempotencyKey: key }),
  /** version (optional): 409 version_conflict when the operation changed / the account is already chosen */
  assignAccount: (id: number, accountId: number | null, key?: string, version?: number) =>
    api.post<T.MutationResult>(
      `/transactions/${id}/account`,
      version === undefined ? { account_id: accountId } : { account_id: accountId, version },
      { idempotencyKey: key },
    ),
  history: (id: number) => api.get<T.AuditEntryOut[]>(`/transactions/${id}/history`),
}

export const transfersApi = {
  transfer: (body: T.TransferCreate, key: string) => api.post<T.MutationResult>('/transfers', body, { idempotencyKey: key }),
  exchange: (body: T.ExchangeCreate, key: string) => api.post<T.MutationResult>('/exchanges', body, { idempotencyKey: key }),
  adjust: (body: T.AdjustmentCreate, key: string) => api.post<T.AdjustmentResult>('/adjustments', body, { idempotencyKey: key }),
}

export const accountsApi = {
  list: (includeArchived = false) => api.get<T.AccountOut[]>('/accounts', { query: { include_archived: includeArchived } }),
  balances: () => api.get<T.BalancesOut>('/accounts/balances'),
  create: (body: T.AccountCreate) => api.post<T.AccountOut>('/accounts', body),
  onboarding: (body: T.OnboardingBalancesIn) => api.post<T.BalancesOut>('/accounts/onboarding', body),
  get: (id: number) => api.get<T.AccountOut>(`/accounts/${id}`),
  patch: (id: number, body: T.AccountPatch) => api.patch<T.AccountOut>(`/accounts/${id}`, body),
  remove: (id: number) => api.delete<OkOut>(`/accounts/${id}`),
  setOpening: (id: number, body: T.OpeningBalanceIn) => api.put<T.AccountOut>(`/accounts/${id}/opening`, body),
  myAccounts: () => api.get<T.MyAccountOut[]>('/my-accounts'),
  addMyAccount: (body: T.MyAccountCreate) => api.post<T.MyAccountOut>('/my-accounts', body),
  removeMyAccount: (id: number) => api.delete<OkOut>(`/my-accounts/${id}`),
}

export const categoriesApi = {
  list: (q: { kind?: 'expense' | 'income'; include_archived?: boolean; with_counts?: boolean } = {}) =>
    api.get<T.CategoryOut[]>('/categories', { query: q }),
  suggest: (q: { transaction_id?: number; q?: string }) => api.get<T.CategorySuggestions>('/categories/suggest', { query: q }),
  create: (body: T.CategoryCreate) => api.post<T.CategoryOut>('/categories', body),
  merge: (sourceId: number, targetId: number) => api.post<T.CategoryOut>('/categories/merge', { source_id: sourceId, target_id: targetId }),
  patch: (id: number, body: T.CategoryPatch) => api.patch<T.CategoryOut>(`/categories/${id}`, body),
  remove: (id: number) => api.delete<OkOut>(`/categories/${id}`),
}

export const merchantsApi = {
  list: (q?: string) => api.get<T.MerchantOut[]>('/merchants', { query: { q } }),
  create: (body: T.MerchantCreate) => api.post<T.MerchantOut>('/merchants', body),
  patch: (id: number, body: T.MerchantPatch) => api.patch<T.MerchantOut>(`/merchants/${id}`, body),
  remove: (id: number) => api.delete<OkOut>(`/merchants/${id}`),
}

export const actionsApi = {
  list: (q: { limit?: number; offset?: number; recent?: boolean } = {}) => api.get<T.ActionOut[]>('/actions', { query: q }),
  undo: (actionId: T.UUID, key: string) => api.post<T.UndoResult>(`/actions/${actionId}/undo`, {}, { idempotencyKey: key }),
  audit: (entityType: string, entityId: string | number) =>
    api.get<T.AuditEntryOut[]>('/audit', { query: { entity_type: entityType, entity_id: String(entityId) } }),
}

export const exportApi = {
  /** URL for a download link (cookies are sent by the browser). */
  transactionsUrl: (format: 'csv' | 'xlsx', f: T.TransactionFilter = {}) => api.url('/export/transactions', { format, ...txQuery(f) }),
  fullUrl: () => `${apiBaseUrl()}/export/full`,
}

export const assistantApi = {
  send: (body: T.AssistantMessageIn, key: string, signal?: AbortSignal) =>
    api.post<T.AssistantResponse>('/assistant/messages', body, { idempotencyKey: key, signal, timeoutMs: 45_000 }),
  confirmPreview: (token: T.UUID, key: string) =>
    api.post<T.AssistantResponse>(`/assistant/previews/${token}/confirm`, {}, { idempotencyKey: key, timeoutMs: 45_000 }),
  cancelPreview: (token: T.UUID) => api.post<OkOut>(`/assistant/previews/${token}/cancel`),
}

const period = (p: StatsPeriodParams = {}): Q => ({ preset: p.preset, date_from: p.date_from, date_to: p.date_to })

export const statsApi = {
  summary: (currency: T.StatsCurrency = 'VND') => api.get<T.SummaryOut>('/stats/summary', { query: { currency } }),
  byCategory: (p: StatsPeriodParams & { kind?: 'expense' | 'income'; currency?: T.StatsCurrency; is_business?: boolean } = {}) =>
    api.get<T.ByCategoryOut>('/stats/by-category', { query: { ...period(p), kind: p.kind ?? 'expense', currency: p.currency, is_business: p.is_business } }),
  series: (p: StatsPeriodParams & { granularity?: 'day' | 'month'; currency?: T.StatsCurrency } = {}) =>
    api.get<T.SeriesOut>('/stats/series', { query: { ...period(p), granularity: p.granularity ?? 'day', currency: p.currency } }),
  top: (p: StatsPeriodParams & { limit?: number; currency?: T.StatsCurrency } = {}) =>
    api.get<T.TopOut>('/stats/top', { query: { ...period(p), limit: p.limit, currency: p.currency } }),
  dailyAverage: (p: StatsPeriodParams & { currency?: T.StatsCurrency } = {}) =>
    api.get<T.DailyAverageOut>('/stats/daily-average', { query: { ...period(p), currency: p.currency } }),
  compare: (p: StatsPeriodParams & { prev_date_from?: T.ISODate; prev_date_to?: T.ISODate; currency?: T.StatsCurrency } = {}) =>
    api.get<T.CompareOut>('/stats/compare', { query: { ...period(p), prev_date_from: p.prev_date_from, prev_date_to: p.prev_date_to, currency: p.currency } }),
  business: (p: StatsPeriodParams & { currency?: T.StatsCurrency } = {}) =>
    api.get<T.BusinessOut>('/stats/business', { query: { ...period(p), currency: p.currency } }),
}

export const ratesApi = {
  latest: () => api.get<T.RatesLatestOut>('/rates/latest'),
  convert: (q: { amount: T.Money; from_: string; to: string; on?: T.ISODate }) => api.get<T.ConvertOut>('/rates/convert', { query: q }),
  refresh: () => api.post<OkOut>('/rates/refresh'),
}

export const searchApi = {
  search: (q: string, limit = 20) => api.get<T.SearchOut>('/search', { query: { q, limit } }),
}

export const recurringApi = {
  list: () => api.get<T.RecurringOut[]>('/recurring'),
  create: (body: T.RecurringCreate) => api.post<T.RecurringOut>('/recurring', body),
  patch: (id: number, body: T.RecurringPatch) => api.patch<T.RecurringOut>(`/recurring/${id}`, body),
  remove: (id: number) => api.delete<OkOut>(`/recurring/${id}`),
  record: (id: number, body: T.RecurringRecordIn, key: string) =>
    api.post<T.MutationResult>(`/recurring/${id}/record`, body, { idempotencyKey: key }),
  skip: (id: number) => api.post<T.RecurringOut>(`/recurring/${id}/skip`),
}

export const reportsApi = {
  get: (kind: 'daily' | 'weekly' | 'monthly', on?: T.ISODate) => api.get<T.ReportOut>(`/reports/${kind}`, { query: { on } }),
  send: (kind: 'daily' | 'weekly' | 'monthly', on?: T.ISODate) => api.post<{ outbox_id: number }>(`/reports/${kind}/send`, {}, { query: { on } }),
}

export const debtsApi = {
  list: (includeSettled = false) => api.get<T.DebtsOut>('/debts', { query: { include_settled: includeSettled } }),
  get: (id: number, q: { limit?: number; offset?: number } = {}) => api.get<T.DebtDetailOut>(`/debts/${id}`, { query: q }),
  /** Idempotency-Key required (API.md §15) */
  operation: (body: T.DebtOpCreate, key: string) => api.post<T.DebtOpResult>('/debts/operations', body, { idempotencyKey: key }),
  patch: (id: number, body: T.DebtPatch) => api.patch<T.DebtOut>(`/debts/${id}`, body),
  remove: (id: number) => api.delete<OkOut>(`/debts/${id}`),
  snooze: (id: number, days = 7) => api.post<T.DebtOut>(`/debts/${id}/snooze`, { days }),
}

export const healthApi = {
  get: (signal?: AbortSignal) => api.get<T.HealthOut>('/health', { signal, timeoutMs: 8_000 }),
}
