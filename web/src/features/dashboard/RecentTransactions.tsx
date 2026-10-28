// «Последние операции» (DESIGN §9.4, §10.1): grouped by day, today's header carries the
// exact total from /stats/summary; new rows (arrived via SSE/refetch) get the fading highlight.
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { MessageSquareText } from 'lucide-react'
import { EmptyState } from '@/components/pf/EmptyState'
import { Section } from '@/components/pf/Page'
import { TxRow, TxRowSkeleton } from '@/components/pf/TxRow'
import { useDelayed } from '@/components/pf/DelayedSkeleton'
import { focusAiInput } from '@/features/assistant/aiFocus'
import { useTransactions } from '@/lib/api/hooks'
import type { KpiOut, TransactionOut } from '@/lib/api/types'
import { formatDayHeader } from '@/lib/format'
import { kpiValueText } from './KpiTiles'

export const EMPTY_CHIPS = ['кофе 85к', 'такси 70к вчера', 'получил 300 USDT от клиента']

export function groupByDay(items: TransactionOut[]): [string, TransactionOut[]][] {
  const out: [string, TransactionOut[]][] = []
  for (const t of items) {
    const last = out[out.length - 1]
    if (last && last[0] === t.occurred_on) last[1].push(t)
    else out.push([t.occurred_on, [t]])
  }
  return out
}

/** Track ids seen before; ids that appear later are «new» for 1.2 s. */
function useNewIds(items: TransactionOut[] | undefined): Set<number> {
  const seen = useRef<Set<number> | null>(null)
  const [fresh, setFresh] = useState<Set<number>>(new Set())
  useEffect(() => {
    if (!items) return
    if (seen.current === null) { seen.current = new Set(items.map((t) => t.id)); return }
    const added = items.filter((t) => !seen.current!.has(t.id)).map((t) => t.id)
    items.forEach((t) => seen.current!.add(t.id))
    if (!added.length) return
    setFresh(new Set(added))
    const tm = setTimeout(() => setFresh(new Set()), 1300)
    return () => clearTimeout(tm)
  }, [items])
  return fresh
}

interface Props { limit?: number; tz?: string; spentToday?: KpiOut; className?: string; title?: string }

export function RecentTransactions({ limit = 8, tz, spentToday, className, title = 'Последние операции' }: Props) {
  const nav = useNavigate()
  const q = useTransactions({ limit, sort: 'date_desc' })
  const fresh = useNewIds(q.data?.items)
  const showSkeleton = useDelayed(q.isPending)
  const items = q.data?.items ?? []
  return (
    <Section
      title={title}
      refreshing={q.isFetching && !q.isPending}
      className={className}
      bodyClassName="px-1 md:px-2"
      actions={<Link to="/operations" className="text-small text-muted-foreground hover:text-foreground">Все →</Link>}
    >
      <div data-testid="recent-list">
        {q.isPending ? (
          // reserve the height of the loaded list (rows + a day header) — no layout shift below (CLS)
          <div className="h-[calc(var(--rows)*56px+28px)] md:h-[calc(var(--rows)*44px+28px)]" style={{ '--rows': limit } as React.CSSProperties}>
            {showSkeleton && Array.from({ length: limit }, (_, i) => <TxRowSkeleton key={i} />)}
          </div>
        ) : q.isError && !q.data ? (
          <p className="px-3 py-6 text-center text-small text-muted-foreground">Не удалось загрузить операции</p>
        ) : !items.length ? (
          <EmptyState
            icon={MessageSquareText}
            title="Пока ни одной операции"
            text="Напишите, как другу: сумма и на что."
            chips={EMPTY_CHIPS}
            onChip={(t) => focusAiInput(t)}
          />
        ) : (
          groupByDay(items).map(([day, txs]) => (
            <div key={day}>
              <div className="flex items-center px-3 pt-2 pb-1 text-caption text-muted-foreground">
                <span className="flex-1">{formatDayHeader(day, new Date(), tz)}</span>
                {formatDayHeader(day, new Date(), tz) === 'Сегодня' && spentToday && spentToday.value != null && (
                  <span className="num">{kpiValueText(spentToday, 'expense')}</span>
                )}
              </div>
              {txs.map((t) => (
                <TxRow key={t.id} tx={t} tz={tz} when="time" highlight={fresh.has(t.id)} onClick={() => nav(`/operations?tx=${t.id}`)} />
              ))}
            </div>
          ))
        )}
      </div>
      {q.data && q.data.total > items.length && (
        <p className="px-3 pt-1 text-caption text-muted-foreground">
          и ещё {q.data.total - items.length}
        </p>
      )}
    </Section>
  )
}
