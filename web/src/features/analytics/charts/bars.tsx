// Bar charts: expenses by day (with average line), by month (current month hatched + forecast),
// business vs personal stacked by month. Recharts, tokens from DESIGN §1.5/§9.6.
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { SeriesPoint } from '@/lib/api'
import { decSign, decToNumber } from '@/features/operations/lib/decimal'
import { MONTHS_SHORT_NOM, daysBetween, endOfMonth, parseISO, startOfMonth } from '@/features/operations/lib/dates'
import { formatAxis, formatCompact, formatMoney } from '@/features/operations/lib/format'
import { axisProps, ChartCard, ChartTooltip, DataTable, useFirstAnimation } from './common'

const fmtDay = (d: string) => `${parseISO(d).getUTCDate()} ${['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'][parseISO(d).getUTCMonth()]}`
const fmtMonth = (d: string) => `${MONTHS_SHORT_NOM[parseISO(d).getUTCMonth()]} ${d.slice(0, 4)}`

/** Expenses per day (with the daily-average line); long periods (all time, > MAX_DAILY_SPAN days) per month. */
export function DailyChart({ points, granularity = 'day', average, currency, today, loading, busy, onPick }: { points: SeriesPoint[]; granularity?: 'day' | 'month'; average: string | null; currency: string; today: string; loading?: boolean; busy?: boolean; onPick: (date: string) => void }) {
  const byMonth = granularity === 'month'
  const fmt = byMonth ? fmtMonth : fmtDay
  const data = points.map((p) => ({ ...p, v: decToNumber(p.expense) }))
  const animate = useFirstAnimation(!loading)
  const empty = !loading && !points.some((p) => decSign(p.expense) > 0)
  const max = data.reduce((m, p) => (p.v > m.v ? p : m), data[0] ?? { v: 0, date: '', expense: '0' })
  const showAvg = !byMonth && !!average && decSign(average) > 0
  const summary = empty || !data.length ? 'Нет расходов за период' : `Больше всего потрачено ${byMonth ? 'в ' : ''}${fmt(max.date)}: ${formatMoney(max.expense, currency)}${average ? `; в среднем ${formatMoney(average, currency)} в день` : ''}`
  return (
    <ChartCard
      title={byMonth ? 'Расходы по месяцам за период' : 'Расходы по дням'}
      summary={summary}
      loading={loading}
      busy={busy}
      empty={empty}
      table={<DataTable head={[byMonth ? 'Месяц' : 'День', 'Расход', 'Доход']} rows={points.map((p) => [fmt(p.date), formatMoney(p.expense, currency), formatMoney(p.income, currency)])} />}
    >
      {showAvg && (
        // Legend above the plot (not a label on the line): the line label used to cover the bars.
        <p className="mb-1 flex items-center justify-end gap-1.5 text-xs text-muted-foreground">
          <span className="w-4 border-t border-dashed border-muted-foreground" aria-hidden />
          <span className="num">в среднем {formatCompact(average, currency)} в день</span>
        </p>
      )}
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 0 }} onClick={(s) => { const i = Number(s?.activeTooltipIndex); if (Number.isFinite(i) && data[i]) onPick(data[i].date) }}>
          <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis dataKey="date" {...axisProps} tickFormatter={(d: string) => (byMonth ? `${MONTHS_SHORT_NOM[parseISO(d).getUTCMonth()]} ${d.slice(2, 4)}` : String(parseISO(d).getUTCDate()))} interval="preserveStartEnd" minTickGap={12} />
          <YAxis {...axisProps} width={48} tickFormatter={(v: number) => formatAxis(v, currency)} />
          <Tooltip cursor={{ fill: 'var(--surface-2)' }} content={({ active, payload }) => { const p = payload?.[0]?.payload as SeriesPoint | undefined; return p ? <ChartTooltip active={active} title={fmt(p.date)} rows={[{ color: 'var(--chart-1)', label: 'Расходы', value: formatMoney(p.expense, currency) }]} /> : null }} />
          {showAvg && <ReferenceLine y={decToNumber(average)} stroke="var(--muted-foreground)" strokeDasharray="4 4" />}
          <Bar dataKey="v" radius={[4, 4, 0, 0]} isAnimationActive={animate} animationDuration={320} cursor="pointer" maxBarSize={28}>
            {data.map((p) => (
              <Cell key={p.date} fill="var(--chart-1)" fillOpacity={(byMonth ? p.date.slice(0, 7) === today.slice(0, 7) : p.date === today) ? 1 : 0.7} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}

/** Linear forecast for the running month (display only). */
export function monthForecast(spent: string, month: string, today: string): number | null {
  if (startOfMonth(today) !== startOfMonth(month)) return null
  const elapsed = daysBetween(startOfMonth(today), today) + 1
  const total = daysBetween(startOfMonth(today), endOfMonth(today)) + 1
  const v = decToNumber(spent)
  return elapsed > 0 && v > 0 ? (v / elapsed) * total : null
}

export function MonthlyChart({ points, currency, today, loading, busy, onPick }: { points: SeriesPoint[]; currency: string; today: string; loading?: boolean; busy?: boolean; onPick: (month: string) => void }) {
  const data = points.map((p) => ({ ...p, v: decToNumber(p.expense), current: startOfMonth(p.date) === startOfMonth(today) }))
  const animate = useFirstAnimation(!loading)
  const empty = !loading && !points.some((p) => decSign(p.expense) > 0)
  const cur = data.find((d) => d.current)
  const forecast = cur ? monthForecast(cur.expense, cur.date, today) : null
  const summary = empty ? 'Нет расходов за эти месяцы' : `Расходы по месяцам, ${points.length} мес.${forecast ? `; прогноз текущего месяца ${formatAxis(forecast, currency)}` : ''}`
  return (
    <ChartCard
      title="Расходы по месяцам"
      summary={summary}
      loading={loading}
      busy={busy}
      empty={empty}
      table={<DataTable head={['Месяц', 'Расход', 'Доход', 'Поток']} rows={points.map((p) => [fmtMonth(p.date), formatMoney(p.expense, currency), formatMoney(p.income, currency), formatMoney(p.net, currency, { sign: 'always' })])} />}
    >
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={data} margin={{ top: 16, right: 4, bottom: 0, left: 0 }} onClick={(s) => { const i = Number(s?.activeTooltipIndex); if (Number.isFinite(i) && data[i]) onPick(data[i].date.slice(0, 7)) }}>
          <defs>
            <pattern id="pf-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="6" height="6" fill="var(--chart-1)" fillOpacity={0.35} />
              <line x1="0" y1="0" x2="0" y2="6" stroke="var(--chart-1)" strokeWidth={3} />
            </pattern>
          </defs>
          <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis dataKey="date" {...axisProps} tickFormatter={(d: string) => MONTHS_SHORT_NOM[parseISO(d).getUTCMonth()]} />
          <YAxis {...axisProps} width={48} tickFormatter={(v: number) => formatAxis(v, currency)} />
          <Tooltip
            cursor={{ fill: 'var(--surface-2)' }}
            content={({ active, payload }) => {
              const p = payload?.[0]?.payload as (SeriesPoint & { current: boolean }) | undefined
              if (!p) return null
              const rows: { color?: string; label: string; value: string }[] = [{ color: 'var(--chart-1)', label: 'Расходы', value: formatMoney(p.expense, currency) }]
              if (p.current && forecast) rows.push({ label: 'прогноз', value: `≈ ${formatAxis(forecast, currency)}` })
              return <ChartTooltip active={active} title={fmtMonth(p.date)} rows={rows} />
            }}
          />
          <Bar dataKey="v" radius={[4, 4, 0, 0]} isAnimationActive={animate} animationDuration={320} cursor="pointer" maxBarSize={40}>
            {data.map((p) => (
              <Cell key={p.date} fill={p.current ? 'url(#pf-hatch)' : 'var(--chart-1)'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}

export interface BizMonth {
  month: string
  business: string
  personal: string
}

export function BizStackChart({ months, currency, loading }: { months: BizMonth[]; currency: string; loading?: boolean }) {
  const data = months.map((m) => ({ ...m, b: decToNumber(m.business), p: decToNumber(m.personal) }))
  const animate = useFirstAnimation(!loading)
  if (!loading && !data.some((d) => d.b || d.p)) return <div className="grid h-40 place-items-center rounded-md border border-dashed border-border text-sm text-muted-foreground">Нет расходов за эти месяцы</div>
  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
        <XAxis dataKey="month" {...axisProps} tickFormatter={(d: string) => MONTHS_SHORT_NOM[parseISO(d).getUTCMonth()]} />
        <YAxis {...axisProps} width={48} tickFormatter={(v: number) => formatAxis(v, currency)} />
        <Tooltip
          cursor={{ fill: 'var(--surface-2)' }}
          content={({ active, payload }) => {
            const m = payload?.[0]?.payload as BizMonth | undefined
            return m ? <ChartTooltip active={active} title={fmtMonth(m.month)} rows={[{ color: 'var(--chart-1)', label: 'Бизнес', value: formatMoney(m.business, currency) }, { color: 'var(--chart-2)', label: 'Личное', value: formatMoney(m.personal, currency) }]} /> : null
          }}
        />
        <Bar dataKey="b" stackId="s" fill="var(--chart-1)" isAnimationActive={animate} maxBarSize={36} />
        <Bar dataKey="p" stackId="s" fill="var(--chart-2)" radius={[4, 4, 0, 0]} isAnimationActive={animate} maxBarSize={36} />
      </BarChart>
    </ResponsiveContainer>
  )
}
