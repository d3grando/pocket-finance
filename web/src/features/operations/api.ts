// Data hooks shared by the pages (operations / analytics / accounts / settings).
// Query keys follow ARCH §12 (`qk` from the shell); mutations invalidate by SSE entity mapping.
import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import {
  api,
  ApiError,
  apiBaseUrl,
  authHeaders,
  entityKeys,
  NetworkError,
  newIdempotencyKey,
  qk,
  type AccountOut,
  type BalancesOut,
  type CategoryOut,
  type Entity,
  type MerchantOut,
  type MutationResult,
  type SettingsOut,
  type TransactionFilter,
  type TransactionOut,
  type TransactionPage,
  type TransactionPatch,
  type UndoResult,
} from '@/lib/api'
import { apiQuery } from './lib/filters'
import { DEFAULT_TZ, todayInTz } from './lib/dates'

export const PAGE_SIZE = 100

export function invalidateEntities(qc: QueryClient, entities: Entity[]) {
  const seen = new Set<string>()
  for (const e of entities) {
    for (const key of entityKeys[e] ?? []) {
      const s = JSON.stringify(key)
      if (seen.has(s)) continue
      seen.add(s)
      void qc.invalidateQueries({ queryKey: key as unknown[] })
    }
  }
}

/** Human message for any API failure (DESIGN §8.3 / §14). */
export function errorMessage(e: unknown, fallback = 'Не удалось сохранить. Ничего не записано'): string {
  if (e instanceof NetworkError) {
    return typeof navigator !== 'undefined' && navigator.onLine === false
      ? 'Не сохранено — нет связи'
      : 'Нет ответа сервера — не знаю, сохранилось ли'
  }
  if (e instanceof ApiError) {
    if (e.code === 'version_conflict') return 'Запись изменилась на другом устройстве — данные обновлены, повторите'
    if (e.code === 'rate_limited') return 'Слишком много попыток. Подождите 1 мин'
    if (e.status >= 500) return fallback
    return e.message || fallback
  }
  return fallback
}

/** After this error the same attempt may still have reached the server (lost answer, 5xx, still
 *  running) — a retry must reuse its Idempotency-Key. Definite 4xx rejections start a new attempt. */
export function keepAttemptKey(e: unknown): boolean {
  if (e instanceof NetworkError) return true
  if (e instanceof ApiError) return e.status >= 500 || e.code === 'idempotency_in_progress'
  return true
}

/** Load failures (GET): no «не сохранено» wording — nothing was being saved. */
export function loadErrorMessage(e: unknown, fallback = 'Не удалось загрузить данные'): string {
  if (e instanceof NetworkError) return 'Нет связи — данные не загружены'
  return errorMessage(e, fallback)
}

/** Query that cannot run because the device is offline and has nothing cached (TanStack «paused»). */
export function isOfflineEmpty(q: { data?: unknown; fetchStatus: string; error?: unknown }): boolean {
  return q.data === undefined && (q.fetchStatus === 'paused' || q.error instanceof NetworkError)
}

// ---------------------------------------------------------------- reference data
export function useSettings() {
  return useQuery({ queryKey: qk.settings, queryFn: () => api.get<SettingsOut>('/settings'), staleTime: 60_000 })
}

/** User timezone + today's local date (refreshes with settings). */
export function useToday(): { tz: string; today: string } {
  const { data } = useSettings()
  const tz = data?.timezone || DEFAULT_TZ
  return { tz, today: todayInTz(tz) }
}

export function useCategories(opts: { includeArchived?: boolean; withCounts?: boolean } = {}) {
  return useQuery({
    queryKey: [...qk.categories, { archived: !!opts.includeArchived, counts: !!opts.withCounts }],
    queryFn: () => api.get<CategoryOut[]>('/categories', { query: { include_archived: opts.includeArchived || undefined, with_counts: opts.withCounts || undefined } }),
    staleTime: 60_000,
  })
}

export function useAccounts(includeArchived = false) {
  return useQuery({
    queryKey: [...qk.accounts, { archived: includeArchived }],
    queryFn: () => api.get<AccountOut[]>('/accounts', { query: { include_archived: includeArchived || undefined } }),
    staleTime: 30_000,
  })
}

export function useBalances() {
  return useQuery({ queryKey: qk.balances, queryFn: () => api.get<BalancesOut>('/accounts/balances') })
}

export function useMerchants(q = '') {
  return useQuery({ queryKey: [...qk.merchants, q], queryFn: () => api.get<MerchantOut[]>('/merchants', { query: { q: q || undefined } }) })
}

export function useNeedsAccountCount() {
  return useQuery({ queryKey: qk.needsAccount, queryFn: () => api.get<{ count: number }>('/transactions/needs-account/count') })
}

// ---------------------------------------------------------------- transactions
export function useTransactionPages(filter: TransactionFilter) {
  return useInfiniteQuery({
    queryKey: qk.transactions(filter),
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      api.get<TransactionPage>('/transactions', { query: apiQuery({ ...filter, limit: PAGE_SIZE, offset: pageParam }), signal }),
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.items.length : undefined),
    placeholderData: keepPreviousData,
  })
}

export function useTransactionList(filter: TransactionFilter, enabled = true) {
  return useQuery({
    queryKey: qk.transactions(filter),
    queryFn: ({ signal }) => api.get<TransactionPage>('/transactions', { query: apiQuery(filter), signal }),
    enabled,
  })
}

export function useTransaction(id: number | null) {
  return useQuery({ queryKey: qk.transaction(id ?? 0), queryFn: () => api.get<TransactionOut>(`/transactions/${id}`), enabled: !!id })
}

const TX_CHANGED: Entity[] = ['transactions', 'accounts', 'stats']

export function usePatchTransaction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: TransactionPatch }) =>
      api.patch<MutationResult>(`/transactions/${id}`, patch, { idempotencyKey: newIdempotencyKey() }),
    onSuccess: () => invalidateEntities(qc, [...TX_CHANGED, 'merchants']),
    onError: (e) => {
      if (e instanceof ApiError && e.code === 'version_conflict') invalidateEntities(qc, ['transactions'])
    },
  })
}

export function useDeleteTransaction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (tx: Pick<TransactionOut, 'id' | 'version'>) =>
      api.delete<MutationResult>(`/transactions/${tx.id}`, { query: { version: tx.version }, idempotencyKey: newIdempotencyKey() }),
    onSuccess: () => invalidateEntities(qc, TX_CHANGED),
    onError: (e) => {
      if (e instanceof ApiError && e.code === 'version_conflict') invalidateEntities(qc, ['transactions'])
    },
  })
}

export function useAssignAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, accountId, version }: { id: number; accountId: number | null; version?: number }) =>
      api.post<MutationResult>(`/transactions/${id}/account`, { account_id: accountId, version }, { idempotencyKey: newIdempotencyKey() }),
    onSuccess: () => invalidateEntities(qc, TX_CHANGED),
  })
}

/** Undo an action (Idempotency-Key required; same key on retry). */
export function useUndo() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ actionId, key }: { actionId: string; key?: string }) =>
      api.post<UndoResult>(`/actions/${actionId}/undo`, {}, { idempotencyKey: key ?? newIdempotencyKey() }),
    onSuccess: (r) => invalidateEntities(qc, ((r?.changed as Entity[]) ?? []).concat(TX_CHANGED)),
  })
}

// ---------------------------------------------------------------- files (export)
/** Download a file from the API with auth headers (works for cookie sessions and Electron Bearer). */
export async function downloadFile(path: string, query: Record<string, unknown> = {}, fallbackName = 'pocket-export'): Promise<void> {
  const url = api.url(path, query as never)
  let res: Response
  try {
    res = await fetch(url, { headers: await authHeaders('GET'), credentials: 'include' })
  } catch {
    throw new NetworkError('Нет связи с сервером')
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const err = body?.error
    throw new ApiError(res.status, err?.code ?? 'internal', err?.message ?? `HTTP ${res.status}`, err?.details ?? {}, err?.request_id ?? null)
  }
  const blob = await res.blob()
  const cd = res.headers.get('Content-Disposition') ?? ''
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd)
  const name = m ? decodeURIComponent(m[1]) : fallbackName
  const href = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = href
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(href), 10_000)
}

export { apiBaseUrl }
