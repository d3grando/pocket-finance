// Start the dashboard requests together with /auth/me instead of after it (one RTT
// less on mobile). Only when a session is likely — the readable pf_csrf cookie (web) or a device
// token (Electron) — so a first visit never fires data requests that would 401.
import type { QueryClient } from '@tanstack/react-query'
import { hasDesktopAuth } from '@/lib/desktop'
import { accountsApi, settingsApi, statsApi, transactionsApi } from '@/lib/api/endpoints'
import { qk } from '@/lib/api/keys'

export function sessionLikely(): boolean {
  if (hasDesktopAuth()) return true
  try { return /(?:^|;\s*)pf_csrf=/.test(document.cookie) } catch { return false }
}

const MOBILE_QUERY = '(max-width: 767px)'

/** Same keys and params as the Home widgets (useSummary, RecentTransactions, CategoryDonut…). */
export function prefetchDashboard(qc: QueryClient, pathname: string) {
  if (pathname !== '/' || !sessionLikely()) return
  const mobile = typeof window !== 'undefined' && !!window.matchMedia?.(MOBILE_QUERY).matches
  const tx = { limit: mobile ? 5 : 8, sort: 'date_desc' as const }
  const pre = (queryKey: readonly unknown[], queryFn: () => Promise<unknown>) =>
    void qc.prefetchQuery({ queryKey, queryFn, staleTime: 10_000, retry: false })
  pre(qk.settings, settingsApi.get)
  pre(qk.stats('summary', { currency: 'VND' }), () => statsApi.summary('VND'))
  pre(qk.transactions(tx), () => transactionsApi.list(tx))
  pre(qk.balances, accountsApi.balances)
  pre(qk.stats('by-category', { preset: 'this_month', kind: 'expense', currency: 'VND' }),
    () => statsApi.byCategory({ preset: 'this_month', kind: 'expense', currency: 'VND' }))
}
