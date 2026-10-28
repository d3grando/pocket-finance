// Mobile «Неделя −3.1м ₫» + 7-day bar sparkline (DESIGN §10.2, §9.6): chart-1, today 100 %, others 70 %.
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis } from 'recharts'
import { useSeries } from '@/lib/api/hooks'
import type { StatsCurrency } from '@/lib/api/types'
import { Dec } from '@/lib/decimal'
import { formatCompact, formatDayMonth, formatMoney } from '@/lib/format'

const WD = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб']

export function WeekBars({ currency }: { currency: StatsCurrency }) {
  const q = useSeries({ preset: 'last_7_days', granularity: 'day', currency })
  const pts = q.data?.points ?? []
  const total = pts.reduce((s, p) => s.add(Dec.from(p.expense)), Dec.ZERO)
  const data = pts.map((p) => {
    const [y, m, d] = p.date.split('-').map(Number)
    return { ...p, v: Number(p.expense), wd: WD[new Date(Date.UTC(y, m - 1, d, 12)).getUTCDay()] }
  })
  return (
    <section className="rounded-lg border bg-card p-4" data-testid="week-bars">
      <div className="flex items-center">
        <span className="flex-1 text-title-3">Неделя</span>
        <span className="num text-body font-medium">{q.data ? formatCompact(total.isZero() ? '0' : total.neg().toString(), currency) : '—'}</span>
      </div>
      <div className="mt-2 h-20" role="img" aria-label={`Расходы за 7 дней: ${formatMoney(total.toString(), currency)}`}>
        {q.isPending ? <div className="pf-pulse h-full rounded-md bg-muted/60" /> : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
              <XAxis dataKey="wd" tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }} height={18} interval={0} />
              <Tooltip cursor={false} content={({ active, payload }) => active && payload?.length ? (
                <div className="num rounded-md border bg-popover px-2 py-1 text-small shadow-md">
                  {formatDayMonth((payload[0].payload as { date: string }).date)} · {formatMoney((payload[0].payload as { expense: string }).expense, currency)}
                </div>
              ) : null} />
              <Bar dataKey="v" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                {data.map((p, i) => <Cell key={p.date} fill="var(--chart-1)" fillOpacity={i === data.length - 1 ? 1 : 0.7} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  )
}
