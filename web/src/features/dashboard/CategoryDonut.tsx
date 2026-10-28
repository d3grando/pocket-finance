// «Категории · октябрь» donut (DESIGN §1.5, §9.6): top-7 → chart-1…7 by position,
// the rest folds into «Прочее» (chart-8); 2 px card-colour gaps; centre total; legend-table with
// amount and %; hover highlights (others 35 %); click → «Операции» with the category filter.
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { Cell, Pie, PieChart, ResponsiveContainer } from 'recharts'
import { Section } from '@/components/pf/Page'
import { useByCategory } from '@/lib/api/hooks'
import type { CategorySlice, StatsCurrency } from '@/lib/api/types'
import { Dec } from '@/lib/decimal'
import { formatCompact, formatMoney, formatPercent } from '@/lib/format'
import { cn } from '@/lib/utils'

export interface Slice { id: number | null; name: string; amount: string; share: string; color: string; isBusiness: boolean }

const MONTH_GEN = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']

/** Top-7 by amount keep their positional colour; the tail becomes «ещё N» in chart-8. */
export function toSlices(items: CategorySlice[], max = 7): Slice[] {
  const sorted = [...items].filter((i) => Dec.from(i.amount).isPos()).sort((a, b) => Dec.from(b.amount).cmp(Dec.from(a.amount)))
  const head: Slice[] = sorted.slice(0, max).map((c, i) => ({
    id: c.category_id, name: c.name, amount: c.amount, share: c.share, color: `var(--chart-${i + 1})`, isBusiness: c.is_business,
  }))
  const tail = sorted.slice(max)
  if (tail.length) {
    head.push({
      id: null,
      name: `ещё ${tail.length}`,
      amount: tail.reduce((s, c) => s.add(Dec.from(c.amount)), Dec.ZERO).toString(),
      share: tail.reduce((s, c) => s.add(Dec.from(c.share)), Dec.ZERO).toString(),
      color: 'var(--chart-8)',
      isBusiness: false,
    })
  }
  return head
}

export function CategoryDonut({ currency }: { currency: StatsCurrency }) {
  const nav = useNavigate()
  const q = useByCategory({ preset: 'this_month', kind: 'expense', currency })
  const [hover, setHover] = useState<number | null>(null)
  const slices = useMemo(() => toSlices(q.data?.items ?? []), [q.data])
  const month = q.data?.range.date_from ? MONTH_GEN[Number(q.data.range.date_from.slice(5, 7)) - 1] : ''
  const top = slices[0]
  const aria = top ? `Больше всего ушло на ${top.name}: ${formatPercent(top.share)}` : 'Нет данных за период'
  const open = (s: Slice) => nav(s.id ? `/operations?period=this_month&kind=expense&category_id=${s.id}` : '/operations?period=this_month&kind=expense')

  return (
    <Section title={`Категории${month ? ` · ${month}` : ''}`} refreshing={q.isFetching && !q.isPending} className="lg:col-span-4">
      {q.isPending ? (
        <div className="flex items-center gap-4"><div className="pf-pulse size-40 rounded-full bg-muted/60" /><div className="flex flex-1 flex-col gap-2">{[0, 1, 2, 3].map((i) => <span key={i} className="pf-pulse h-3 rounded-xs bg-muted" />)}</div></div>
      ) : !slices.length ? (
        <div className="flex h-40 items-center justify-center rounded-md border border-dashed text-small text-muted-foreground">Нет данных за период</div>
      ) : (
        <div className="flex flex-col items-center gap-4 sm:flex-row lg:flex-col" data-testid="category-donut">
          <div className="relative size-44 shrink-0" role="img" aria-label={aria}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={slices.map((s) => ({ ...s, v: Number(s.amount) }))}
                  dataKey="v"
                  nameKey="name"
                  innerRadius="68%"
                  outerRadius="100%"
                  paddingAngle={1}
                  cornerRadius={3}
                  stroke="var(--card)"
                  strokeWidth={2}
                  isAnimationActive={false}
                  onMouseLeave={() => setHover(null)}
                >
                  {slices.map((s, i) => (
                    <Cell
                      key={s.name}
                      fill={s.color}
                      fillOpacity={hover === null || hover === i ? 1 : 0.35}
                      onMouseEnter={() => setHover(i)}
                      onClick={() => open(s)}
                      style={{ cursor: 'pointer', outline: 'none' }}
                    />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
              <span className="num text-title-2" translate="no">{formatCompact(q.data?.total ?? '0', currency)}</span>
              {month && <span className="text-caption text-muted-foreground">за {month}</span>}
            </div>
          </div>
          <ul className="flex w-full min-w-0 flex-col" onMouseLeave={() => setHover(null)}>
            {slices.map((s, i) => (
              <li key={s.name}>
                <button
                  type="button"
                  onMouseEnter={() => setHover(i)}
                  onFocus={() => setHover(i)}
                  onClick={() => open(s)}
                  className={cn('flex h-7 w-full items-center gap-2 rounded-xs px-1 text-left text-small transition-opacity hover:bg-surface-2', hover !== null && hover !== i && 'opacity-50')}
                >
                  <span className="size-2 shrink-0 rounded-full" style={{ background: s.color }} />
                  <span className="min-w-0 flex-1 truncate">{s.name}</span>
                  <span className="num text-muted-foreground">{formatMoney(s.amount, currency)}</span>
                  <span className="num w-10 text-right">{formatPercent(s.share)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  )
}
