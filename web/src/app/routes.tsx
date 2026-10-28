// All pages are mounted here up-front (lazy, default exports) so the pages
// never edits this file. Paths are part of the contract (bot «open» buttons use them).
// Desktop build (Electron, app://pocket) uses a hash router: index.html#/quick.
import { createBrowserRouter, createHashRouter, type RouteObject } from 'react-router'

const page = (load: () => Promise<{ default: React.ComponentType }>) => async () => ({
  Component: (await load()).default,
})

export const routes: RouteObject[] = [
  { path: '/login', lazy: page(() => import('@/pages/login')) },
  { path: '/quick', lazy: page(() => import('@/pages/quick')) }, // Electron Quick Capture (420×450)
  { path: '/capture', lazy: page(() => import('@/pages/quick')) }, // alias of /quick
  {
    path: '/',
    lazy: async () => ({ Component: (await import('@/features/shell/AppShell')).AppShell }),
    children: [
      { index: true, lazy: page(() => import('@/pages/home')) },
      { path: 'onboarding', lazy: page(() => import('@/pages/onboarding')) },
      { path: 'add', lazy: page(() => import('@/pages/add')) },
      { path: 'operations', lazy: page(() => import('@/pages/operations')) }, 
      { path: 'analytics', lazy: page(() => import('@/pages/analytics')) }, 
      { path: 'accounts', lazy: page(() => import('@/pages/accounts')) }, 
      { path: 'settings/*', lazy: page(() => import('@/pages/settings')) }, 
      { path: '*', lazy: page(() => import('@/pages/not-found')) },
    ],
  },
]

/** app://pocket/quick (no hash) → app://pocket/#/quick so the hash router sees the route. */
export function normalizeDesktopLocation(loc: Location = window.location, history: History = window.history) {
  if (loc.hash.startsWith('#/')) return
  const path = loc.pathname.replace(/\/index\.html$/, '/') || '/'
  if (path !== '/' || loc.search) history.replaceState(null, '', `/#${path}${loc.search}`)
}

export function createAppRouter() {
  if (import.meta.env.MODE === 'desktop') {
    normalizeDesktopLocation()
    return createHashRouter(routes)
  }
  return createBrowserRouter(routes)
}
