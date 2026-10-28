// TanStack Query hooks over endpoints.ts with the shared keys (ARCH §12).
import { keepPreviousData, useQuery, type UseQueryOptions } from '@tanstack/react-query'
import {
  accountsApi, authApi, categoriesApi, debtsApi, settingsApi, statsApi, transactionsApi, ratesApi,
  type StatsPeriodParams,
} from './endpoints'
import { ApiError, isGatewayDown, isNetworkError, isUnauthorized } from './client'
import { qk } from './keys'
import type * as T from './types'

type Opts<D> = Omit<UseQueryOptions<D, Error, D, readonly unknown[]>, 'queryKey' | 'queryFn'>

/** /auth/me options shared by every observer (AuthGate, shell, Quick Capture). */
export const meQueryOptions = {
  queryKey: qk.me,
  queryFn: authApi.me,
  staleTime: 5 * 60_000,
  // offline → fail at once (an honest «нет связи» screen), never a paused «pending» blank page
  networkMode: 'always' as const,
  // finite: no retry loop on 5xx / network; a new observer (shell mount) does not refetch a failed
  // query — that refetch flipped it back to «pending» and unmounted the shell in a loop
  // a proxy 502/503/504 (API stopped / deploying) is not retried either: the shell with the banner
  // shows at once and the connection recovery re-checks the session
  retry: (n: number, e: Error) =>
    n < 1 && !isUnauthorized(e) && !isNetworkError(e) && !(e instanceof ApiError && isGatewayDown(e.status, e.code)),
  retryOnMount: false,
}

export function useMe(opts: Opts<T.MeOut> = {}) {
  return useQuery({ ...meQueryOptions, ...opts })
}

export function useSettings(opts: Opts<T.SettingsOut> = {}) {
  return useQuery({ queryKey: qk.settings, queryFn: settingsApi.get, staleTime: 5 * 60_000, ...opts })
}

export function useSummary(currency: T.StatsCurrency = 'VND', opts: Opts<T.SummaryOut> = {}) {
  return useQuery({ queryKey: qk.stats('summary', { currency }), queryFn: () => statsApi.summary(currency), ...opts })
}

export function useByCategory(p: Parameters<typeof statsApi.byCategory>[0] = {}, opts: Opts<T.ByCategoryOut> = {}) {
  return useQuery({
    queryKey: qk.stats('by-category', p),
    queryFn: () => statsApi.byCategory(p),
    placeholderData: keepPreviousData,
    ...opts,
  })
}

export function useSeries(p: StatsPeriodParams & { granularity?: 'day' | 'month'; currency?: T.StatsCurrency } = {}, opts: Opts<T.SeriesOut> = {}) {
  return useQuery({
    queryKey: qk.stats('series', p),
    queryFn: () => statsApi.series(p),
    placeholderData: keepPreviousData,
    ...opts,
  })
}

export function useTransactions(filter: T.TransactionFilter = {}, opts: Opts<T.TransactionPage> = {}) {
  return useQuery({
    queryKey: qk.transactions(filter),
    queryFn: ({ signal }) => transactionsApi.list(filter, signal),
    placeholderData: keepPreviousData,
    ...opts,
  })
}

export function useNeedsAccountCount(opts: Opts<{ count: number }> = {}) {
  return useQuery({ queryKey: qk.needsAccount, queryFn: transactionsApi.needsAccountCount, ...opts })
}

export function useAccounts(opts: Opts<T.AccountOut[]> = {}) {
  return useQuery({ queryKey: qk.accounts, queryFn: () => accountsApi.list(false), staleTime: 60_000, ...opts })
}

export function useBalances(opts: Opts<T.BalancesOut> = {}) {
  return useQuery({ queryKey: qk.balances, queryFn: accountsApi.balances, ...opts })
}

export function useCategories(opts: Opts<T.CategoryOut[]> = {}) {
  return useQuery({ queryKey: qk.categories, queryFn: () => categoriesApi.list({}), staleTime: 60_000, ...opts })
}

export function useRates(opts: Opts<T.RatesLatestOut> = {}) {
  return useQuery({ queryKey: qk.rates, queryFn: ratesApi.latest, staleTime: 10 * 60_000, ...opts })
}

/** GET /debts — «Мне должны / Я должен» (DEBTS.md §10). A server without debts (404/501) is an error. */
export function useDebts(includeSettled = false, opts: Opts<T.DebtsOut> = {}) {
  return useQuery({ queryKey: qk.debts(includeSettled), queryFn: () => debtsApi.list(includeSettled), ...opts })
}

export function useDebt(id: number | null, opts: Opts<T.DebtDetailOut> = {}) {
  return useQuery({ queryKey: qk.debt(id ?? 0), queryFn: () => debtsApi.get(id!, { limit: 50 }), enabled: !!id, ...opts })
}
