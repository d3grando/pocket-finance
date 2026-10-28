// sidebar (desktop) / bottom nav (PWA), offline banner, command palette, manual form,
// global hotkeys, SSE subscription. DESIGN.md §5, §10, §11. Pages of the pages render inside <Outlet/>.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router'
import { ErrorBoundary } from '@/components/pf/ErrorBoundary'
import { OfflineBanner } from '@/components/pf/Connection'
import { AssistantProvider } from '@/features/assistant/AssistantProvider'
import { AuthGate } from '@/features/auth/AuthGate'
import type { ManualPrefill } from '@/features/manual/ManualTxForm'
import { useIsMobile } from '@/hooks/useMediaQuery'
import { useMe, useSettings } from '@/lib/api/hooks'
import { readStore, STORE, writeStore } from '@/lib/storage'
import { getThemeChoice, setThemeChoice } from '@/lib/theme'
import { BottomNav } from './BottomNav'
import { CommandPalette } from './CommandPalette'
import { ManualSheet, type AddMode } from './ManualSheet'
import { MobileTopBar } from './MobileTopBar'
import { ShellContext, type ShellActions } from './ShellContext'
import { ShortcutsDialog } from './ShortcutsDialog'
import { Sidebar } from './Sidebar'
import { useEventStream } from './useEventStream'
import { useGlobalHotkeys } from './useGlobalHotkeys'

/** settings.theme (server) wins over the local choice when it changes (another device switched it). */
function useThemeFromSettings(theme: 'light' | 'dark' | 'system' | undefined) {
  useEffect(() => {
    if (theme && theme !== getThemeChoice()) setThemeChoice(theme)
  }, [theme])
}

function ShellLayout() {
  const loc = useLocation()
  const nav = useNavigate()
  const me = useMe()
  const mobile = useIsMobile()
  const settings = useSettings({ enabled: !!me.data })
  useEventStream(!!me.data)
  useThemeFromSettings(settings.data?.theme)

  const [collapsed, setCollapsed] = useState(() => readStore(STORE.sidebar) === '1')
  const [palette, setPalette] = useState(false)
  const [shortcuts, setShortcuts] = useState(false)
  const [manual, setManual] = useState<{ open: boolean; prefill?: ManualPrefill; mode?: AddMode }>({ open: false })
  // onboarding has its own sticky «Готово» — the bottom nav would cover it and is not needed there
  const showBottomNav = mobile && loc.pathname !== '/onboarding'

  const toggleSidebar = useCallback(() => setCollapsed((c) => { writeStore(STORE.sidebar, c ? null : '1'); return !c }), [])
  const actions = useMemo<ShellActions>(() => ({
    openManual: (prefill) => setManual({ open: true, prefill, mode: 'manual' }),
    openAdd: () => setManual({ open: true, mode: 'ai' }),
    openPalette: () => setPalette(true),
    openShortcuts: () => setShortcuts(true),
  }), [])

  useGlobalHotkeys({
    onPalette: () => setPalette(true),
    onManual: () => setManual({ open: true, mode: 'manual' }),
    onToggleSidebar: toggleSidebar,
    onShortcuts: () => setShortcuts(true),
  })

  const onAuthExpired = useCallback((text: string) => {
    writeStore(STORE.draft, text, 'session')
    nav('/login', { replace: true, state: { from: `${loc.pathname}${loc.search}`, expired: true } })
  }, [nav, loc.pathname, loc.search])

  return (
    <ShellContext.Provider value={actions}>
      <AssistantProvider
        tz={settings.data?.timezone}
        onManual={(p) => setManual({ open: true, mode: 'manual', prefill: { amount: p.amount, currency: p.currency, text: p.text } })}
        onOpen={(route) => { setPalette(false); nav(route) }}
        onAuthExpired={onAuthExpired}
      >
        <div className="flex min-h-dvh bg-background text-foreground">
          <Sidebar collapsed={collapsed} onToggle={toggleSidebar} />
          <div className="flex min-w-0 flex-1 flex-col">
            {mobile && <MobileTopBar />}
            {/* mobile: sticks right under the top bar (both are sticky — never on top of each other) */}
            <OfflineBanner
              className={mobile ? 'sticky top-[calc(52px+env(safe-area-inset-top))]' : 'sticky top-0'}
              onCheck={() => window.location.reload()}
            />
            <main className={showBottomNav ? 'flex-1 pb-[calc(80px+env(safe-area-inset-bottom))] md:pb-8' : 'flex-1 pb-8'} id="main">
              <ErrorBoundary resetKey={loc.pathname}>
                <div key={loc.pathname} className="pf-fade-in">
                  <Outlet />
                </div>
              </ErrorBoundary>
            </main>
          </div>
          {showBottomNav && <BottomNav />}
        </div>
        <CommandPalette open={palette} onOpenChange={setPalette} onManual={() => setManual({ open: true, mode: 'manual' })} />
        <ShortcutsDialog open={shortcuts} onOpenChange={setShortcuts} />
        <ManualSheet
          open={manual.open}
          prefill={manual.prefill}
          mode={manual.mode}
          onModeChange={(mode) => setManual((m) => ({ ...m, mode }))}
          onOpenChange={(open) => setManual((m) => ({ ...m, open }))}
        />
      </AssistantProvider>
    </ShellContext.Provider>
  )
}

export function AppShell() {
  return (
    <AuthGate>
      <ShellLayout />
    </AuthGate>
  )
}
