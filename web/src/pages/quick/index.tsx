// Quick Capture (DESIGN §10.10): compact 420×450 for the Electron tray window.
// Field focused on show, Enter sends, Shift+Enter newline, Esc hides (via preload), 3 KPIs, last 4
// operations, connection dot, «Полная панель». Auth: Electron device token (preload) or the cookie.
// Window height follows the content both ways: 450 by default — a result card takes the place of
// some «Последние» rows — and only what still does not fit (preview, account choice) grows it up to
// 640; when the card goes away the window shrinks back.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Inbox, LogOut, Moon, MoreHorizontal, PanelsTopLeft, Settings2, X } from 'lucide-react'
import { ConnectionDot, OfflineBanner, syncedLabel } from '@/components/pf/Connection'
import { Kbd } from '@/components/pf/Kbd'
import { LogoMark } from '@/components/pf/Logo'
import { TxRow, TxRowSkeleton } from '@/components/pf/TxRow'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { AiCommandBar } from '@/features/assistant/AiCommandBar'
import { AssistantProvider, useAssistant } from '@/features/assistant/AssistantProvider'
import { CardStack } from '@/features/assistant/CardStack'
import { LoginForm } from '@/features/auth/LoginForm'
import { logout } from '@/features/auth/session'
import { kpiTone, kpiValueText } from '@/features/dashboard/KpiTiles'
import { useEventStream } from '@/features/shell/useEventStream'
import { isUnauthorized, onApiEvent } from '@/lib/api/client'
import { useMe, useSettings, useSummary, useTransactions } from '@/lib/api/hooks'
import type { KpiOut } from '@/lib/api/types'
import { checkHealth, connection, useConnection } from '@/lib/connection'
import { desktop } from '@/lib/desktop'
import { useTheme } from '@/lib/theme'
import { cn } from '@/lib/utils'
import { FOOTER_HEIGHT, MAX_RECENT, quickWindowHeight, recentRowsThatFit } from './sizing'

function Kpi({ label, k, meaning }: { label: string; k: KpiOut | undefined; meaning: 'expense' | 'income' }) {
  return (
    <div className="flex min-w-0 flex-col">
      <span className="text-caption text-muted-foreground">{label}</span>
      {k ? (
        <span className={cn('num truncate text-title-3', kpiTone(k, meaning))} translate="no">{kpiValueText(k, meaning, label !== 'Сегодня')}</span>
      ) : (
        <span className="pf-pulse mt-1 h-4 w-20 rounded-xs bg-muted" />
      )}
    </div>
  )
}

function QuickBody({ recentRows }: { recentRows: number }) {
  const settings = useSettings()
  const tz = settings.data?.timezone
  const summary = useSummary('VND')
  const recent = useTransactions({ limit: 4, sort: 'date_desc' })
  const { entries, act } = useAssistant()
  const conn = useConnection()
  const visible = entries.filter((e) => !e.dismissed)
  const top = visible[0]
  const focusMode = top && (top.card.kind === 'preview' || top.editing)
  const needsPin = !!visible.find((e) => e.card.kind === 'preview' || e.card.kind === 'needs_account' || e.editing)

  useEffect(() => { desktop.setPinned(needsPin) }, [needsPin])

  return (
    <>
      <div className="px-3 pt-1">
        <AiCommandBar
          variant="quick"
          autoFocus
          onEscapeEmpty={() => desktop.hide()}
          onKeyDownExtra={(e, value) => {
            // Esc closes an open preview / account choice first (DESIGN §11), not the window
            if (e.key !== 'Escape' || value.trim() || !top) return false
            const b = top.card.buttons.find((x) => x.action.kind === (top.card.kind === 'preview' ? 'cancel_preview' : 'later'))
            if (top.card.kind !== 'preview' && top.card.kind !== 'needs_account') return false
            e.preventDefault()
            e.stopPropagation()
            if (b) void act(top.uid, b)
            else if (top.card.kind === 'preview') void act(top.uid, { id: 'cancel', label: 'Отмена', style: 'ghost', hotkey: 'Esc', action: { kind: 'cancel_preview', preview_token: top.card.preview_token } })
            return true
          }}
        />
      </div>
      <CardStack max={1} compact className="px-3 pt-2 empty:hidden" />
      {!focusMode && (
        <>
          <div className="grid grid-cols-3 gap-2 px-4 pt-3" data-testid="quick-kpis">
            <Kpi label="Сегодня" k={summary.data?.spent_today} meaning="expense" />
            <Kpi label="Месяц" k={summary.data?.spent_month} meaning="expense" />
            <Kpi label="Доходы" k={summary.data?.income_month} meaning="income" />
          </div>
          {conn.status === 'offline' && (
            <p className="num px-4 pt-1 text-caption text-warning">данные на {syncedLabel(conn.lastOkAt)}</p>
          )}
          {recentRows > 0 && (
            <div className="px-1 pt-3" data-testid="quick-recent-block">
              <p className="px-3 pb-1 text-caption text-muted-foreground" data-quick-label>Последние</p>
              <div data-testid="quick-recent">
                {recent.isPending ? [0, 1, 2].slice(0, recentRows).map((i) => <TxRowSkeleton key={i} dense />) :
                  recent.data?.items.length ? recent.data.items.slice(0, recentRows).map((t) => (
                    <TxRow key={t.id} tx={t} tz={tz} dense onClick={() => desktop.openFull(`/operations?tx=${t.id}`)} />
                  )) : <p className="px-3 py-2 text-small text-muted-foreground">Пока ни одной операции. Напишите, как другу: сумма и на что.</p>}
              </div>
            </div>
          )}
        </>
      )}
    </>
  )
}

function QuickFooter() {
  const summary = useSummary('VND')
  const count = summary.data?.needs_account_count ?? 0
  return (
    <footer className="mt-auto flex h-8 shrink-0 items-center gap-2 px-3 text-caption text-muted-foreground">
      {count > 0 && (
        <button type="button" onClick={() => desktop.openFull('/operations?status=needs_account')} className="inline-flex items-center gap-1 rounded-full bg-warning-soft px-2 py-0.5 text-warning [-webkit-app-region:no-drag]">
          <Inbox className="size-3" /> Разобрать <span className="num">{count}</span>
        </button>
      )}
      <span className="flex-1" />
      <button type="button" onClick={() => desktop.openFull('/')} className="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 hover:bg-surface-2 hover:text-foreground" data-testid="quick-full-panel">
        <PanelsTopLeft className="size-3.5" /> Полная панель <Kbd>Ctrl ⇧ F</Kbd>
      </button>
    </footer>
  )
}

export default function QuickPage() {
  const qc = useQueryClient()
  const me = useMe({ retry: false })
  const authed = !!me.data
  const needLogin = !me.isPending && !me.data && (isUnauthorized(me.error) || !me.error)
  const { toggle } = useTheme()
  const rootRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const [recentRows, setRecentRows] = useState(MAX_RECENT)
  const lastHeight = useRef(0)
  const labelH = useRef(28)
  const rowH = useRef(36)
  const authedRef = useRef(authed)
  useLayoutEffect(() => { authedRef.current = authed }, [authed])
  useEventStream(authed)

  useEffect(() => { document.title = 'Pocket' }, [])
  const refetchMe = me.refetch
  useEffect(() => onApiEvent((e) => {
    if (e.type !== 'unauthorized' || e.path.startsWith('/auth/')) return
    void desktop.clearToken().finally(() => { qc.removeQueries({ queryKey: ['me'] }); void refetchMe() })
  }), [qc, refetchMe])
  useEffect(() => { if (needLogin) desktop.setPinned(true) }, [needLogin])
  // the server is back after a failed start → check the session again (no manual «Повторить» needed)
  useEffect(() => connection.onRecovered(() => { if (qc.getQueryData(['me']) === undefined) void refetchMe() }), [qc, refetchMe])

  // every time the tray window is shown: focus the field, then animate from the tray (DESIGN §7.2.5:
  // focus before animation), then re-fetch — an SSE «change» may have been missed while hidden
  useEffect(() => {
    const focus = () => document.querySelector<HTMLTextAreaElement>('[data-testid="ai-input"]')?.focus()
    const off = desktop.onShow(({ edge }) => {
      focus()
      const el = rootRef.current
      if (el && typeof el.animate === 'function') {
        const reduce = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
        const dy = edge === 'top' ? -8 : 8
        el.animate(
          reduce ? [{ opacity: 0 }, { opacity: 1 }] : [{ opacity: 0, transform: `translateY(${dy}px)` }, { opacity: 1, transform: 'none' }],
          { duration: reduce ? 120 : 180, easing: reduce ? 'linear' : 'ease-out' },
        )
      }
      void qc.invalidateQueries()
    })
    window.addEventListener('focus', focus)
    return () => { off(); window.removeEventListener('focus', focus) }
  }, [qc])

  // Ctrl+Shift+F → full panel; Esc anywhere outside the field hides the window
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'F' || e.key === 'f' || e.key === 'А' || e.key === 'а')) {
        e.preventDefault()
        desktop.openFull('/')
      } else if (e.key === 'Escape' && !e.defaultPrevented && authedRef.current && !document.querySelector('[role="dialog"],[role="menu"]')) {
        desktop.hide()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Measure the content block (no min-height, unlike the root) so the window follows it both ways:
  // first fit the «Последние» rows into 450, then grow only by what still does not fit (up to 640).
  useLayoutEffect(() => {
    const el = contentRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const measure = () => {
      const content = el.getBoundingClientRect().height
      const block = el.querySelector<HTMLElement>('[data-testid="quick-recent-block"]')
      const label = block?.querySelector<HTMLElement>('[data-quick-label]')
      const row = block?.querySelector<HTMLElement>('[data-testid="quick-recent"] > *')
      if (block && label) labelH.current = block.getBoundingClientRect().height - (block.querySelector<HTMLElement>('[data-testid="quick-recent"]')?.getBoundingClientRect().height ?? 0)
      if (row && row.getBoundingClientRect().height > 0) rowH.current = row.getBoundingClientRect().height
      const blockH = block?.getBoundingClientRect().height ?? 0
      const fixed = content - blockH
      if (el.querySelector('[data-testid="quick-kpis"]')) {
        setRecentRows(recentRowsThatFit(fixed, labelH.current, rowH.current))
      }
      const h = quickWindowHeight(content, authedRef.current ? FOOTER_HEIGHT : 0)
      if (h !== lastHeight.current) { lastHeight.current = h; desktop.resize(h) }
    }
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const onOpen = useCallback((route: string) => desktop.openFull(route), [])

  return (
    <div ref={rootRef} className="flex min-h-dvh flex-col overflow-hidden bg-card text-foreground" data-testid="quick">
      <div ref={contentRef} className="flex shrink-0 flex-col" data-testid="quick-content">
      <header className="flex h-9 shrink-0 items-center gap-2 px-3 [-webkit-app-region:drag]">
        <LogoMark size={18} />
        <span className="text-small font-semibold tracking-[-0.02em]">Pocket</span>
        <span className="flex-1" />
        <span className="[-webkit-app-region:no-drag]"><ConnectionDot withLabel /></span>
        {authed && (
          <>
            <button type="button" title="Полная панель (Ctrl+Shift+F)" aria-label="Полная панель" onClick={() => desktop.openFull('/')}
              className="rounded-sm p-1 text-muted-foreground hover:bg-surface-2 hover:text-foreground [-webkit-app-region:no-drag]" data-testid="quick-full">
              <PanelsTopLeft className="size-4" />
            </button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button type="button" aria-label="Меню" className="rounded-sm p-1 text-muted-foreground hover:bg-surface-2 hover:text-foreground [-webkit-app-region:no-drag]">
                  <MoreHorizontal className="size-4" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-52">
                <DropdownMenuItem onSelect={() => desktop.openFull('/')}><PanelsTopLeft /> Полная панель</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => desktop.openFull('/settings')}><Settings2 /> Настройки</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => toggle()}><Moon /> Сменить тему</DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => void logout(qc).then(() => qc.invalidateQueries())}><LogOut /> Выйти</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        )}
        {!authed && (
          <button type="button" aria-label="Скрыть" onClick={() => desktop.hide()} className="rounded-sm p-1 text-muted-foreground hover:text-foreground [-webkit-app-region:no-drag]">
            <X className="size-4" />
          </button>
        )}
      </header>
      <OfflineBanner className="mx-3 mb-1 rounded-md" onCheck={() => { void checkHealth(); void qc.invalidateQueries() }} />
      {authed && (
        <AssistantProvider onOpen={onOpen} onManual={(p) => desktop.openFull(`/add?${new URLSearchParams(p).toString()}`)}
          onAuthExpired={() => { qc.removeQueries({ queryKey: ['me'] }); void me.refetch() }}>
          <QuickBody recentRows={recentRows} />
        </AssistantProvider>
      )}
      </div>
      {me.isPending && !me.isFetched ? null : authed ? (
        <QuickFooter />
      ) : needLogin ? (
        <div className="flex flex-1 items-center justify-center px-6 pb-4">
          <LoginForm variant="quick" onDone={() => { desktop.setPinned(false); void qc.invalidateQueries() }} />
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center text-small text-muted-foreground">
          <p>Не удалось связаться с сервером.</p>
          <button type="button" className="text-primary hover:underline" onClick={() => void me.refetch()}>Повторить</button>
        </div>
      )}
    </div>
  )
}
