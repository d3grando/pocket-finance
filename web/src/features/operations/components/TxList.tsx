// Operations list grouped by day with sticky day headers; virtualised (window scroll) after 200 rows
// (DESIGN §9.4). Infinite loading by offset pages.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useWindowVirtualizer } from '@tanstack/react-virtual'
import type { AccountOut, CategoryOut, TransactionOut } from '@/lib/api'
import { cn } from '@/lib/utils'
import { dayGroupLabel } from '../lib/dates'
import { flattenGroups, groupByDay, type FlatRow } from '../lib/tx'
import { Spinner } from '../ui/kit'
import { TxEditForm } from './TxEditForm'
import { TxRow, type RowActions } from './TxRow'

export const VIRTUALIZE_AFTER = 200

export interface TxListProps {
  items: TransactionOut[]
  tz: string
  today: string
  categories: CategoryOut[]
  accounts: AccountOut[]
  actions: RowActions
  selectedId: number | null
  editId: number | null
  editFocus: 'amount' | 'category'
  onCloseEdit: () => void
  hasNextPage: boolean
  isFetchingNextPage: boolean
  fetchNextPage: () => void
  mobile: boolean
  offline: boolean
  compact?: boolean
  stickyTop?: number
}

/** Day header; on desktop the total sits in the amount column (the rows have a 64 px actions column + gap after it). */
function DayHeader({ date, total, today, sticky, top, mobile }: { date: string; total: string | null; today: string; sticky: boolean; top: number; mobile: boolean }) {
  return (
    <div
      className={cn('z-[5] flex h-9 items-center justify-between gap-2 border-b border-border bg-background/90 text-[13px] font-medium text-muted-foreground backdrop-blur-xl', mobile ? 'px-1' : 'pr-[88px] pl-3', sticky && 'sticky')}
      style={sticky ? { top } : undefined}
    >
      <span className="truncate">{dayGroupLabel(date, today)}</span>
      {total && (
        <span className="num shrink-0 text-foreground" translate="no">
          {total}
        </span>
      )}
    </div>
  )
}

export function TxList(p: TxListProps) {
  const groups = useMemo(() => groupByDay(p.items), [p.items])
  const virtual = p.items.length > VIRTUALIZE_AFTER
  const sentinel = useRef<HTMLDivElement>(null)
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = p

  useEffect(() => {
    if (virtual || !hasNextPage || !sentinel.current || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver((es) => {
      if (es.some((e) => e.isIntersecting) && !isFetchingNextPage) fetchNextPage()
    }, { rootMargin: '600px' })
    io.observe(sentinel.current)
    return () => io.disconnect()
  }, [virtual, hasNextPage, isFetchingNextPage, fetchNextPage])

  const renderTx = (tx: TransactionOut) => (
    <TxRow
      key={tx.id}
      tx={tx}
      tz={p.tz}
      accounts={p.accounts}
      selected={p.selectedId === tx.id}
      actions={p.actions}
      offline={p.offline}
      mobile={p.mobile}
      compact={p.compact}
      below={
        !p.mobile && p.editId === tx.id ? (
          <TxEditForm key={`e-${tx.id}-${tx.version}`} tx={tx} categories={p.categories} accounts={p.accounts} focus={p.editFocus} onDone={p.onCloseEdit} onCancel={p.onCloseEdit} className="mx-1 my-2" />
        ) : null
      }
    />
  )

  return (
    <div role="list" aria-label="Операции" data-virtual={virtual || undefined}>
      {virtual ? (
        <VirtualRows {...p} rows={flattenGroups(groups)} renderTx={renderTx} />
      ) : (
        groups.map((g) => (
          <section key={g.date} aria-label={dayGroupLabel(g.date, p.today)} role="listitem">
            <DayHeader date={g.date} total={g.total} today={p.today} sticky top={p.stickyTop ?? 0} mobile={p.mobile} />
            <div className="py-1">{g.items.map(renderTx)}</div>
          </section>
        ))
      )}
      <div ref={sentinel} className="flex h-12 items-center justify-center text-sm text-muted-foreground">
        {isFetchingNextPage ? <Spinner label="Загружаю ещё" /> : hasNextPage ? null : p.items.length > 20 ? 'Это все операции' : null}
      </div>
    </div>
  )
}

function VirtualRows({ rows, renderTx, today, mobile, hasNextPage, isFetchingNextPage, fetchNextPage, editId, selectedId }: TxListProps & { rows: FlatRow[]; renderTx: (tx: TransactionOut) => React.ReactNode }) {
  const listRef = useRef<HTMLDivElement>(null)
  const [margin, setMargin] = useState(0)
  useLayoutEffect(() => {
    setMargin(listRef.current ? listRef.current.getBoundingClientRect().top + window.scrollY : 0)
  }, [])
  const v = useWindowVirtualizer({
    count: rows.length,
    estimateSize: (i) => (rows[i].type === 'day' ? 36 : mobile ? 60 : 48),
    overscan: 12,
    scrollMargin: margin,
    initialRect: { width: typeof window === 'undefined' ? 1024 : window.innerWidth, height: typeof window === 'undefined' ? 800 : window.innerHeight },
    getItemKey: (i) => rows[i].key,
  })
  const items = v.getVirtualItems()
  const last = items[items.length - 1]
  useEffect(() => {
    if (last && last.index >= rows.length - 30 && hasNextPage && !isFetchingNextPage) fetchNextPage()
  }, [last, rows.length, hasNextPage, isFetchingNextPage, fetchNextPage])
  useEffect(() => {
    const id = editId ?? selectedId
    if (id == null) return
    const idx = rows.findIndex((r) => r.type === 'tx' && r.tx.id === id)
    if (idx >= 0) v.scrollToIndex(idx, { align: 'auto' })
  }, [editId, selectedId, rows, v])
  return (
    <div ref={listRef} style={{ height: v.getTotalSize(), position: 'relative' }}>
      {items.map((it) => {
        const r = rows[it.index]
        return (
          <div key={it.key} data-index={it.index} ref={v.measureElement} role="listitem" style={{ position: 'absolute', top: 0, left: 0, width: '100%', transform: `translateY(${it.start - v.options.scrollMargin}px)` }}>
            {r.type === 'day' ? <DayHeader date={r.date} total={r.total} today={today} sticky={false} top={0} mobile={mobile} /> : renderTx(r.tx)}
          </div>
        )
      })}
    </div>
  )
}
