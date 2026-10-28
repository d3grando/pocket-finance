// «Операции» (DESIGN §10.4): filters in URL, grouped list (virtualised), inline edit, delete with undo,
// manual add (N), export CSV/XLSX, keyboard (/, N, ↑↓/J K, Enter, E, Delete).
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { Inbox, ListOrdered, Plus, SearchX, WifiOff } from 'lucide-react'
import type { TransactionOut } from '@/lib/api'
import { errorMessage, isOfflineEmpty, loadErrorMessage, useAccounts, useCategories, useDeleteTransaction, useNeedsAccountCount, useToday, useTransaction, useTransactionPages } from './api'
import { ActiveChips, TxFilters } from './components/TxFilters'
import { ExportMenu } from './components/ExportMenu'
import { ManualTxSheet, type ManualPrefill } from './components/ManualTxForm'
import { TxEditForm } from './components/TxEditForm'
import { TxList } from './components/TxList'
import { TxRow, type RowActions } from './components/TxRow'
import { useUndoToast } from './components/useUndoToast'
import { DEFAULT_FILTER, filterToParams, parseFilter, toApiFilter, type OpsFilter } from './lib/filters'
import { countLabel, formatMoney } from './lib/format'
import { formatTime } from './lib/dates'
import { totalsLine } from './lib/tx'
import { useDebounced, useIsMobile, useLocalPref, useOnline, isTypingTarget } from './ui/hooks'
import { Button, Card, EmptyState, ErrorNote, Kbd, Loading, OfflineState, Page, PageHeader, Skeleton } from './ui/kit'
import { notify, PageToaster, Sheet } from './ui/overlays'

function RowsSkeleton() {
  return (
    <div className="grid gap-1 py-2">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="flex h-11 items-center gap-3 px-3">
          <Skeleton className="size-7" />
          <div className="grid flex-1 gap-1.5">
            <Skeleton className="h-3 w-1/3" />
            <Skeleton className="h-2.5 w-1/5" />
          </div>
          <Skeleton className="h-3 w-[72px]" />
        </div>
      ))}
    </div>
  )
}

export function OperationsView() {
  const [sp, setSp] = useSearchParams()
  const f = useMemo(() => parseFilter(sp), [sp])
  const { tz, today } = useToday()
  const mobile = useIsMobile()
  const online = useOnline()
  const offline = !online
  const [density] = useLocalPref<'normal' | 'compact'>('pf.density', 'normal')

  const setFilter = useCallback(
    (patch: Partial<OpsFilter>) => {
      setSp((prev) => filterToParams({ ...parseFilter(prev), ...patch }, prev), { replace: true })
    },
    [setSp],
  )
  const resetFilters = () => {
    setQ('')
    setSp(filterToParams({ ...DEFAULT_FILTER, period: f.status ? 'all_time' : DEFAULT_FILTER.period, status: '' }), { replace: true })
  }

  // search box → debounced URL param
  const [q, setQ] = useState(f.q)
  const dq = useDebounced(q, 300)
  useEffect(() => {
    if (dq !== f.q) setFilter({ q: dq })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dq])
  useEffect(() => {
    setQ((cur) => (cur === f.q ? cur : f.q))
  }, [f.q])

  const apiFilter = useMemo(() => toApiFilter(f, today), [f, today])
  const pages = useTransactionPages(apiFilter)
  const items = useMemo(() => pages.data?.pages.flatMap((p) => p.items) ?? [], [pages.data])
  const totals = pages.data?.pages[0]?.totals
  const total = pages.data?.pages[0]?.total ?? 0
  const cats = useCategories({ withCounts: true })
  const accs = useAccounts(true)
  const needs = useNeedsAccountCount()
  const categories = useMemo(() => cats.data ?? [], [cats.data])
  const accounts = useMemo(() => accs.data ?? [], [accs.data])

  const [editId, setEditId] = useState<number | null>(null)
  const [editFocus, setEditFocus] = useState<'amount' | 'category'>('amount')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [manual, setManual] = useState<{ open: boolean; prefill?: ManualPrefill }>({ open: false })
  const searchRef = useRef<HTMLInputElement>(null)
  const undoToast = useUndoToast()
  const del = useDeleteTransaction()

  // ?tx=<id> — open that operation (inline if loaded, else pinned on top)
  const txParam = Number(sp.get('tx')) || null
  const linked = useTransaction(txParam && !items.some((t) => t.id === txParam) ? txParam : null)
  const [seenTx, setSeenTx] = useState<number | null>(null)
  if (txParam !== seenTx) {
    setSeenTx(txParam)
    if (txParam) {
      setSelectedId(txParam)
      setEditId(txParam)
    }
  }
  const closeEdit = useCallback(() => {
    setEditId(null)
    if (sp.get('tx')) setSp((prev) => { const n = new URLSearchParams(prev); n.delete('tx'); return n }, { replace: true })
  }, [sp, setSp])

  const actions: RowActions = useMemo(
    () => ({
      onOpen: (tx, focus = 'amount') => {
        setSelectedId(tx.id)
        setEditFocus(focus)
        setEditId((cur) => (cur === tx.id && focus === 'amount' && !mobile ? null : tx.id))
      },
      onDelete: (tx: TransactionOut) =>
        del.mutate(tx, {
          onSuccess: (r) => {
            if (editId === tx.id) setEditId(null)
            undoToast(`Удалено: ${formatMoney(tx.amount, tx.currency)}${tx.category ? ` · ${tx.category.name}` : ''}`, r.action_id, 'Вернуть')
          },
          onError: (e) => notify.error(errorMessage(e, 'Не удалось удалить. Ничего не изменено')),
        }),
      onDuplicate: (tx) =>
        setManual({
          open: true,
          prefill: { kind: tx.kind === 'income' ? 'income' : 'expense', amount: tx.amount, currency: tx.currency, category_id: tx.category?.id ?? null, account_id: tx.account?.id ?? null, description: tx.description, counterparty: tx.counterparty },
        }),
    }),
    [del, editId, mobile, undoToast],
  )

  // keyboard (DESIGN §11)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // defaultPrevented: the shell's global hotkeys (shell) already handled it (e.g. N) — never open two forms
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || isTypingTarget(e.target) || document.querySelector('[role="dialog"]')) return
      const idx = items.findIndex((t) => t.id === selectedId)
      if (e.key === '/') {
        e.preventDefault()
        searchRef.current?.focus()
      } else if (e.key === 'n' || e.key === 'N' || e.key === 'т' || e.key === 'Т') {
        e.preventDefault()
        setManual({ open: true })
      } else if ((e.key === 'ArrowDown' || e.key === 'j' || e.key === 'о') && items.length) {
        e.preventDefault()
        setSelectedId(items[Math.min(items.length - 1, idx + 1)].id)
      } else if ((e.key === 'ArrowUp' || e.key === 'k' || e.key === 'л') && items.length) {
        e.preventDefault()
        setSelectedId(items[Math.max(0, idx - 1)].id)
      } else if (idx >= 0 && !offline) {
        const tx = items[idx]
        if (e.key === 'Enter') {
          e.preventDefault()
          actions.onOpen(tx)
        } else if (e.key === 'e' || e.key === 'E' || e.key === 'у') {
          e.preventDefault()
          actions.onOpen(tx, 'category')
        } else if (e.key === 'Delete') {
          e.preventDefault()
          actions.onDelete(tx)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [items, selectedId, actions, offline])
  // /operations?focus=search (the shell's global «/» from other pages)
  useEffect(() => {
    if (sp.get('focus') !== 'search') return
    searchRef.current?.focus()
    setSp((prev) => { const n = new URLSearchParams(prev); n.delete('focus'); return n }, { replace: true })
  }, [sp, setSp])
  useEffect(() => {
    if (selectedId == null) return
    document.querySelector(`[data-tx-id="${selectedId}"]`)?.scrollIntoView?.({ block: 'nearest' })
  }, [selectedId])

  // sticky filter bar height → day headers stick below it
  const barRef = useRef<HTMLDivElement>(null)
  const [barH, setBarH] = useState(0)
  useLayoutEffect(() => {
    const el = barRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setBarH(el.getBoundingClientRect().height))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const editingTx = editId ? (items.find((t) => t.id === editId) ?? (linked.data?.id === editId ? linked.data : null)) : null
  const pinned = linked.data && !items.some((t) => t.id === linked.data!.id) ? linked.data : null
  const filtered = f.q || f.cat.length || f.acc.length || f.cur || f.kind.length || f.biz !== 'all' || f.min || f.max || f.period !== DEFAULT_FILTER.period

  return (
    <Page>
      <PageToaster />
      <PageHeader
        title="Операции"
        actions={
          <>
            {(needs.data?.count ?? 0) > 0 && !f.status && (
              <Button variant="warning" size="sm" className="rounded-full" onClick={() => setFilter({ status: 'needs_account', period: 'all_time' })}>
                <Inbox /> Разобрать: {needs.data!.count}
              </Button>
            )}
            {mobile ? (
              <Button variant="primary" size="icon" aria-label="Новая операция" onClick={() => setManual({ open: true })} disabled={offline}>
                <Plus />
              </Button>
            ) : (
              <Button variant="primary" onClick={() => setManual({ open: true })} disabled={offline}>
                <Plus /> Новая <Kbd className="border-transparent bg-primary-foreground/20 text-primary-foreground">N</Kbd>
              </Button>
            )}
            {!mobile && <ExportMenu filter={apiFilter} />}
          </>
        }
      />

      <div ref={barRef} className="sticky top-0 z-10 -mx-4 mt-3 grid gap-2 border-b border-transparent bg-background/85 px-4 py-2 backdrop-blur-xl backdrop-saturate-150 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <TxFilters f={f} set={setFilter} categories={categories} accounts={accounts} today={today} mobile={mobile} searchRef={searchRef} q={q} setQ={setQ} onReset={resetFilters} right={mobile ? <ExportMenu filter={apiFilter} compact /> : undefined} />
        <ActiveChips f={f} set={setFilter} categories={categories} accounts={accounts} today={today} onReset={resetFilters} />
        {totals && (
          <p className="num flex flex-wrap gap-x-1.5 text-[13px] text-muted-foreground" aria-live="polite">
            <span className="font-medium text-foreground">{countLabel(totals.count ?? total, ['операция', 'операции', 'операций'])}</span>
            {totalsLine(totals).map((s) => (
              <span key={s}>· {s}</span>
            ))}
            {pages.isFetching && !pages.isFetchingNextPage && <span className="sr-only">Обновляю</span>}
          </p>
        )}
      </div>

      {offline && (
        <p className="mt-2 flex items-center gap-2 rounded-md bg-warning-soft px-3 py-2 text-sm text-warning" role="status">
          <WifiOff className="size-4" /> Нет связи — правка и удаление недоступны{pages.data && pages.dataUpdatedAt ? ` · данные на ${formatTime(new Date(pages.dataUpdatedAt).toISOString(), tz)}` : ''}
        </p>
      )}

      {pinned && !mobile && (
        <Card title="Операция по ссылке" className="mt-3" actions={<Button size="sm" variant="ghost" onClick={closeEdit}>Закрыть</Button>}>
          <TxRow tx={pinned} tz={tz} accounts={accounts} actions={actions} offline={offline} mobile={false} selected />
          <TxEditForm key={`p-${pinned.id}-${pinned.version}`} tx={pinned} categories={categories} accounts={accounts} onDone={closeEdit} onCancel={closeEdit} className="mt-2" />
        </Card>
      )}

      <div className="mt-1">
        {isOfflineEmpty(pages) ? (
          // Paused offline query: no data yet — never the «Пока ни одной операции» empty state (DESIGN §8.4).
          <OfflineState what="Операции" onRetry={() => void pages.refetch()} />
        ) : pages.error && !pages.data ? (
          <ErrorNote error={new Error(loadErrorMessage(pages.error, 'Не удалось загрузить операции'))} onRetry={() => void pages.refetch()} className="mt-3" />
        ) : (
          <Loading loading={pages.isLoading} fallback={<RowsSkeleton />} className="min-h-80">
            {items.length === 0 ? (
              f.status ? (
                <EmptyState icon={<Inbox />} title="Всё разобрано" text="Операции без счёта появятся здесь." />
              ) : filtered ? (
                <EmptyState icon={<SearchX />} title="Ничего не нашлось" text="Попробуйте другой период или сбросьте фильтры." action={<Button onClick={resetFilters}>Сбросить фильтры</Button>} />
              ) : (
                <EmptyState icon={<ListOrdered />} title="Пока ни одной операции" text="Напишите, как другу: сумма и на что. Или добавьте вручную." action={<Button variant="primary" onClick={() => setManual({ open: true })}><Plus /> Добавить вручную</Button>} />
              )
            ) : (
              <TxList
                items={items}
                tz={tz}
                today={today}
                categories={categories}
                accounts={accounts}
                actions={actions}
                selectedId={selectedId}
                editId={mobile ? null : editId}
                editFocus={editFocus}
                onCloseEdit={closeEdit}
                hasNextPage={!!pages.hasNextPage}
                isFetchingNextPage={pages.isFetchingNextPage}
                fetchNextPage={() => void pages.fetchNextPage()}
                mobile={mobile}
                offline={offline}
                compact={density === 'compact'}
                stickyTop={barH}
              />
            )}
          </Loading>
        )}
      </div>

      {mobile && (
        <>
          <Sheet open={!!editingTx} onOpenChange={(v) => !v && closeEdit()} title="Изменить операцию">
            {editingTx && <TxEditForm key={`m-${editingTx.id}-${editingTx.version}`} tx={editingTx} categories={categories} accounts={accounts} focus={editFocus} onDone={closeEdit} onCancel={closeEdit} className="border-0 bg-transparent p-0" />}
          </Sheet>
        </>
      )}
      <ManualTxSheet open={manual.open} onOpenChange={(v) => setManual((m) => ({ ...m, open: v }))} categories={categories} accounts={accounts} prefill={manual.prefill} />
    </Page>
  )
}
