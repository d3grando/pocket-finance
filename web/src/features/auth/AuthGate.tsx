// Session gate for the app shell: no session → /login (the AI-line draft is kept in
// sessionStorage); first login → /onboarding. Without a network / with the API down there is no
// blank screen: the first check renders a boot screen with the offline banner, and once /auth/me has
// answered at least once (even with an error) the shell stays mounted — its banner and pages tell
// the truth («Нет связи с сервером»), nothing pretends to be saved (offline-first PWA).
import { useEffect } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { OfflineBanner } from '@/components/pf/Connection'
import { isUnauthorized, onApiEvent, setCsrfToken } from '@/lib/api/client'
import { useMe } from '@/lib/api/hooks'
import { qk } from '@/lib/api/keys'
import { connection } from '@/lib/connection'
import { STORE, writeStore } from '@/lib/storage'
import { prefetchDashboard } from './prefetch'

/** First /auth/me still running: page background + the offline banner (never an empty white page). */
export function BootScreen() {
  return (
    <div className="min-h-dvh bg-background" data-testid="boot-screen" aria-busy="true">
      <OfflineBanner className="sticky top-0" onCheck={() => window.location.reload()} />
    </div>
  )
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const me = useMe()
  const loc = useLocation()
  const nav = useNavigate()
  const qc = useQueryClient()

  useEffect(() => { if (me.data) setCsrfToken(me.data.csrf_token) }, [me.data])

  // a likely session (pf_csrf cookie): load the dashboard data in parallel with /auth/me (one RTT less)
  useEffect(() => { prefetchDashboard(qc, loc.pathname) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // the server is back after a failed start → check the session again and reload the data
  useEffect(() => connection.onRecovered(() => {
    if (qc.getQueryData(qk.me) === undefined) void qc.invalidateQueries()
  }), [qc])

  // any 401 from a data request → back to login (keeps where we were)
  useEffect(() => onApiEvent((e) => {
    if (e.type !== 'unauthorized' || e.path.startsWith('/auth/') || e.path.startsWith('/passkeys/login')) return
    const draft = document.querySelector<HTMLTextAreaElement>('[data-testid="ai-input"]')?.value
    if (draft?.trim()) writeStore(STORE.draft, draft, 'session')
    qc.removeQueries({ queryKey: qk.me })
    nav('/login', { replace: true, state: { from: `${loc.pathname}${loc.search}`, expired: true } })
  }), [nav, qc, loc.pathname, loc.search])

  // only the very first check shows the boot screen; a later refetch of a failed /auth/me goes back
  // to «pending» (TanStack v5, no data) — keep the shell mounted instead of blanking it
  if (me.isPending && !me.isFetched) return <BootScreen />
  if (me.error && isUnauthorized(me.error)) {
    return <Navigate to="/login" replace state={{ from: `${loc.pathname}${loc.search}` }} />
  }
  if (me.data && !me.data.onboarding_done && loc.pathname !== '/onboarding') {
    return <Navigate to="/onboarding" replace />
  }
  // network/server error without a cached session: show the shell — the offline banner and the
  // pages tell the truth; nothing pretends to be saved.
  return <>{children}</>
}
