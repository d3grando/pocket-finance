// Home KPI tiles (DESIGN §9.3): Сегодня, Месяц (расходы), Доходы, Поток — in ₫ with ≈ $.
// Clicking a tile opens «Операции» with the matching filter. value null → «—», "0" → real zero.
// Desktop: a sparkline on every tile (days of the month; «Сегодня» — the last 7 days) from the same
// series the Dynamics chart loads. Mobile: «≈ $» and «+39% к сент.» on separate lines (no truncation).
import { useNavigate } from 'react-router'
import { useSeries } from '@/lib/api/hooks'
import { kpiSparks } from './sparks'
import { Area, AreaChart, ResponsiveContainer } from 'recharts'
import type { KpiOut, SummaryOut } from '@/lib/api/types'
import { Dec } from '@/lib/decimal'
import { formatCompact, formatMoney, formatPercent } from '@/lib/format'
import { cn } from '@/lib/utils'

type Meaning = 'expense' | 'income' | 'net'

const MON_ABBR = ['янв.', 'фев.', 'мар.', 'апр.', 'мая', 'июн.', 'июл.', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.']

/** «к сент.» for the month before `monthFrom` (YYYY-MM-DD). */
export function prevMonthSuffix(monthFrom: string | undefined): string {
  if (!monthFrom) return ''
  const m = Number(monthFrom.slice(5, 7))
  return ` к ${MON_ABBR[(m + 10) % 12]}`
}

export function kpiValueText(k: KpiOut, meaning: Meaning, compact = false): string {
  if (k.value == null) return '—'
  const v = Dec.tryFrom(k.value)
  if (!v) return '—'
  const fmt = compact ? formatCompact : formatMoney
  if (meaning === 'expense') return fmt(v.isZero() ? '0' : v.abs().neg().toString(), k.currency)
  return fmt(k.value, k.currency, { sign: 'always' })
}

export function kpiTone(k: KpiOut, meaning: Meaning): string {
  if (meaning !== 'net' || k.value == null) return 'text-foreground'
  const v = Dec.tryFrom(k.value)
  return v && v.isNeg() ? 'text-expense' : 'text-income'
}

function changeText(k: KpiOut, meaning: Meaning, suffix: string): React.ReactNode {
  if (k.change_ratio == null) return null
  const r = Dec.tryFrom(k.change_ratio)
  if (!r) return null
  const up = r.isPos()
  // rising expenses = expense colour, rising income = income colour (DESIGN §1.3)
  const tone = r.isZero() ? '' : meaning === 'expense' ? (up ? 'text-expense' : 'text-income') : up ? 'text-income' : 'text-expense'
  return <span className={tone}>{formatPercent(k.change_ratio, { sign: 'always' })}{suffix}</span>
}

const DOT: Record<Meaning, string> = { expense: 'bg-expense', income: 'bg-income', net: 'bg-foreground' }
const STROKE: Record<Meaning, string> = { expense: 'var(--expense)', income: 'var(--income)', net: 'var(--foreground)' }

function Spark({ data, meaning }: { data: string[]; meaning: Meaning }) {
  if (data.length < 2) return <div className="h-6" />
  const pts = data.map((v, i) => ({ i, v: Number(v) }))
  return (
    <div className="h-6" aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={pts} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <Area type="monotone" dataKey="v" stroke={STROKE[meaning]} strokeWidth={1.5} fill={STROKE[meaning]} fillOpacity={0.12} isAnimationActive={false} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

interface TileProps {
  label: string
  k: KpiOut | undefined
  meaning: Meaning
  to: string
  sub?: React.ReactNode
  loading?: boolean
  size?: 'lg' | 'md' | 'sm'
  /** sparkline points (desktop) */
  spark?: string[]
  /** mobile: put `sub` on its own line */
  stack?: boolean
  testId?: string
}

export function KpiTile({ label, k, meaning, to, sub, loading, size = 'lg', spark, stack, testId }: TileProps) {
  const nav = useNavigate()
  return (
    <button
      type="button"
      onClick={() => nav(to)}
      data-testid={testId}
      className="flex min-w-0 flex-col gap-1 rounded-lg border bg-card p-4 text-left shadow-xs transition-colors hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none md:p-5"
    >
      <span className="flex items-center gap-1.5 text-caption text-muted-foreground">
        <span className={cn('size-1.5 rounded-full', DOT[meaning])} /> {label}
      </span>
      {loading || !k ? (
        <span className="flex flex-col gap-1.5 py-1">
          <span className="pf-pulse h-7 w-3/5 rounded-xs bg-muted" />
          <span className="pf-pulse h-3 w-2/5 rounded-xs bg-muted" />
        </span>
      ) : (
        <>
          <span
            className={cn('num truncate', size === 'lg' ? 'text-title-2 lg:text-display' : size === 'md' ? 'text-title-2' : 'text-title-3', kpiTone(k, meaning))}
            translate="no"
            data-testid={testId ? `${testId}-value` : undefined}
          >
            {kpiValueText(k, meaning, size === 'sm')}
          </span>
          {(() => {
            const approx = k.approx_usd != null && k.value != null
              ? formatMoney(meaning === 'expense' && !Dec.from(k.approx_usd).isZero() ? Dec.from(k.approx_usd).abs().neg().toString() : k.approx_usd, 'USD', { approx: true, sign: meaning === 'expense' ? 'auto' : 'always' })
              : null
            if (stack) {
              return (
                <>
                  {approx && <span className="num truncate text-small text-muted-foreground">{approx}</span>}
                  {sub && <span className="num truncate text-caption text-muted-foreground" data-testid={testId ? `${testId}-change` : undefined}>{sub}</span>}
                </>
              )
            }
            return (
              <span className="num truncate text-small text-muted-foreground">
                {approx}
                {sub && <>{approx && ' · '}{sub}</>}
              </span>
            )
          })()}
          {spark && <span className="hidden lg:block"><Spark data={spark} meaning={meaning} /></span>}
        </>
      )}
    </button>
  )
}

export function KpiTiles({ summary, loading, mobile = false }: { summary: SummaryOut | undefined; loading: boolean; mobile?: boolean }) {
  const suffix = prevMonthSuffix(summary?.month.date_from)
  const currency = summary?.currency === 'USD' ? 'USD' : 'VND'
  const sparkOn = { enabled: !mobile && !!summary }
  // same keys as DynamicsChart (days of this month) and WeekBars (last 7 days) — no extra request there
  const month = useSeries({ preset: 'this_month', granularity: 'day', currency }, sparkOn)
  const week = useSeries({ preset: 'last_7_days', granularity: 'day', currency }, sparkOn)
  const sparks = kpiSparks(month.data?.points ?? [], week.data?.points ?? [])
  const tile = (label: string, k: KpiOut | undefined, meaning: Meaning, to: string, testId: string, spark: string[], size: TileProps['size'] = 'lg') => (
    <KpiTile
      label={label} k={k} meaning={meaning} to={to} loading={loading} size={size} testId={testId}
      spark={mobile ? undefined : spark.length >= 2 ? spark : k?.sparkline ?? []}
      stack={mobile}
      sub={k && changeText(k, meaning, testId === 'kpi-today' ? ' ко вчера' : suffix)}
    />
  )
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 lg:gap-4" data-testid="kpi-tiles">
      {tile('Сегодня', summary?.spent_today, 'expense', '/operations?period=today&kind=expense', 'kpi-today', sparks.today)}
      {tile('Месяц', summary?.spent_month, 'expense', '/operations?period=this_month&kind=expense', 'kpi-month', summary?.spent_month.sparkline.length ? summary.spent_month.sparkline : sparks.month)}
      {tile('Доходы', summary?.income_month, 'income', '/operations?period=this_month&kind=income', 'kpi-income', sparks.income, mobile ? 'sm' : 'lg')}
      {tile('Поток', summary?.net_month, 'net', '/operations?period=this_month', 'kpi-net', sparks.net, mobile ? 'sm' : 'lg')}
    </div>
  )
}
