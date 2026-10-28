// «Динамика доходов и расходов» (DESIGN §9.6): grouped bars income/expense + net line,
// Дни / Недели / Месяцы, compact axis, tooltip, legend, «Таблица» toggle. No bar animation: the
// data refreshes on every SSE change and the page must never make the user wait (DESIGN §7). Amounts stay strings for
// text; Number() only feeds bar geometry.
import { useMemo, useState } from 'react'
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Table2 } from 'lucide-react'
import { Section } from '@/components/pf/Page'
import { SegmentedControl } from '@/components/pf/SegmentedControl'
import { useSeries } from '@/lib/api/hooks'
import type { SeriesPoint, StatsCurrency } from '@/lib/api/types'
import { Dec } from '@/lib/decimal'
import { addDays, formatAxis, formatDayMonth, formatMoney, localDateISO } from '@/lib/format'
import { readStore, STORE, writeStore } from '@/lib/storage'
import { cn } from '@/lib/utils'

type Gran = 'day' | 'week' | 'month'

export interface ChartPoint { key: string; label: string; income: string; expense: string; net: string }

/** Monday of the week of `iso`. */
export function weekStart(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  const wd = (new Date(Date.UTC(y, m - 1, d, 12)).getUTCDay() + 6) % 7
  return addDays(iso, -wd)
}

/** Sum daily points into Monday-based weeks (exact decimal sums). */
export function toWeeks(points: SeriesPoint[]): ChartPoint[] {
  const map = new Map<string, { income: Dec; expense: Dec }>()
  for (const p of points) {
    const k = weekStart(p.date)
    const cur = map.get(k) ?? { income: Dec.ZERO, expense: Dec.ZERO }
    map.set(k, { income: cur.income.add(Dec.from(p.income)), expense: cur.expense.add(Dec.from(p.expense)) })
  }
  return [...map.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => ({
    key: k, label: formatDayMonth(k), income: v.income.toString(), expense: v.expense.toString(), net: v.income.sub(v.expense).toString(),
  }))
}

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']

export function DynamicsChart({ currency, tz }: { currency: StatsCurrency; tz?: string }) {
  const [gran, setGran] = useState<Gran>(() => (readStore(STORE.dynamicsGran) as Gran) || 'day')
  const [table, setTable] = useState(false)
  const today = localDateISO(new Date(), tz)
  const params = gran === 'day'
    ? { preset: 'this_month' as const, granularity: 'day' as const, currency }
    : gran === 'week'
      ? { preset: 'custom' as const, date_from: weekStart(addDays(today, -7 * 11)), date_to: today, granularity: 'day' as const, currency }
      : { preset: 'this_year' as const, granularity: 'month' as const, currency }
  const q = useSeries(params)

  const data = useMemo<ChartPoint[]>(() => {
    const pts = q.data?.points ?? []
    if (gran === 'week') return toWeeks(pts)
    return pts.map((p) => ({
      key: p.date,
      label: gran === 'month' ? MONTHS[Number(p.date.slice(5, 7)) - 1] : String(Number(p.date.slice(8, 10))),
      income: p.income, expense: p.expense, net: p.net,
    }))
  }, [q.data, gran])

  const empty = !q.isPending && data.every((p) => Dec.from(p.income).isZero() && Dec.from(p.expense).isZero())
  const geo = data.map((p) => ({ ...p, i: Number(p.income), e: Number(p.expense), n: Number(p.net) }))

  return (
    <Section
      title="Динамика"
      refreshing={q.isFetching && !q.isPending}
      className="lg:col-span-8"
      actions={
        <div className="flex items-center gap-1.5">
          <SegmentedControl
            aria-label="Шаг"
            value={gran}
            onChange={(g) => { setGran(g); writeStore(STORE.dynamicsGran, g) }}
            options={[{ value: 'day', label: 'Дни' }, { value: 'week', label: 'Недели' }, { value: 'month', label: 'Месяцы' }]}
          />
          <button type="button" onClick={() => setTable((t) => !t)} aria-pressed={table} title="Таблица" aria-label="Таблица"
            className={cn('rounded-sm p-1.5 text-muted-foreground hover:bg-surface-2 hover:text-foreground', table && 'bg-surface-2 text-foreground')}>
            <Table2 className="size-4" />
          </button>
        </div>
      }
    >
      <div className="h-[260px]" aria-label={`Доходы и расходы, ${q.data?.range.label ?? ''}`} role="img" data-testid="dynamics-chart">
        {q.isPending ? (
          <div className="pf-pulse h-full rounded-md bg-muted/60" />
        ) : q.isError && !q.data ? (
          <div className="flex h-full items-center justify-center text-small text-muted-foreground">Не удалось загрузить график</div>
        ) : empty ? (
          <div className="flex h-full items-center justify-center rounded-md border border-dashed text-small text-muted-foreground">Нет данных за период</div>
        ) : table ? (
          <div className="h-full overflow-auto">
            <table className="w-full text-small">
              <thead className="text-caption text-muted-foreground"><tr><th className="text-left font-medium">Период</th><th className="text-right font-medium">Доходы</th><th className="text-right font-medium">Расходы</th><th className="text-right font-medium">Поток</th></tr></thead>
              <tbody className="num">
                {data.map((p) => (
                  <tr key={p.key} className="h-7 border-t">
                    <td>{gran === 'month' ? p.label : formatDayMonth(p.key)}</td>
                    <td className="text-right">{formatMoney(p.income, currency)}</td>
                    <td className="text-right">{formatMoney(p.expense, currency)}</td>
                    <td className="text-right">{formatMoney(p.net, currency, { sign: 'always' })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={geo} margin={{ top: 8, right: 4, bottom: 0, left: 0 }} barGap={2} barCategoryGap="20%">
              <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: 'var(--muted-foreground)' }} interval="preserveStartEnd" minTickGap={8} />
              <YAxis tickLine={false} axisLine={false} width={48} tick={{ fontSize: 12, fill: 'var(--muted-foreground)' }} tickFormatter={(v: number) => formatAxis(v, currency)} />
              <Tooltip cursor={{ fill: 'var(--surface-2)' }} content={<ChartTip currency={currency} gran={gran} />} />
              <Bar dataKey="i" name="Доходы" fill="var(--income)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
              <Bar dataKey="e" name="Расходы" fill="var(--expense)" fillOpacity={0.85} radius={[4, 4, 0, 0]} isAnimationActive={false} />
              <Line dataKey="n" name="Поток" type="monotone" stroke="var(--foreground)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
      <div className="mt-2 flex items-center gap-4 text-caption text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-income" /> Доходы</span>
        <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-expense" /> Расходы</span>
        <span className="flex items-center gap-1.5"><span className="h-0.5 w-3 rounded-full bg-foreground" /> Поток</span>
      </div>
    </Section>
  )
}

interface TipProps { active?: boolean; payload?: { payload: ChartPoint }[]; currency: StatsCurrency; gran: Gran }

function ChartTip({ active, payload, currency, gran }: TipProps) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return (
    <div className="rounded-md border bg-popover p-2 text-small shadow-md">
      <div className="mb-1 text-caption text-muted-foreground">{gran === 'month' ? p.label : gran === 'week' ? `неделя с ${formatDayMonth(p.key)}` : formatDayMonth(p.key)}</div>
      <div className="num flex items-center gap-1.5"><span className="size-2 rounded-full bg-income" /> Доходы {formatMoney(p.income, currency, { sign: 'always' })}</div>
      <div className="num flex items-center gap-1.5"><span className="size-2 rounded-full bg-expense" /> Расходы {formatMoney(Dec.from(p.expense).isZero() ? '0' : `-${p.expense}`, currency)}</div>
      <div className="num flex items-center gap-1.5"><span className="h-0.5 w-2 bg-foreground" /> Поток {formatMoney(p.net, currency, { sign: 'always' })}</div>
    </div>
  )
}
