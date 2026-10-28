// Category donut with legend table, period comparison bars, business/personal panel, top purchases.
import { useState } from 'react'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { BusinessOut, ByCategoryOut, TopItem } from '@/lib/api'
import { cn } from '@/lib/utils'
import { decSign, decToNumber } from '@/features/operations/lib/decimal'
import { formatDateShort } from '@/features/operations/lib/dates'
import { formatMoney, formatPercent } from '@/features/operations/lib/format'
import { Card, Segmented } from '@/features/operations/ui/kit'
import { comparePairs, compareSummary, topSlices, type Slice } from '../lib/slices'
import { BizStackChart, type BizMonth } from './bars'
import { ChartCard, ChartTooltip, DataTable, useFirstAnimation } from './common'

export function CategoryDonut({ data, currency, kind, onKind, periodText, loading, busy, onPick }: { data?: ByCategoryOut; currency: string; kind: 'expense' | 'income'; onKind: (k: 'expense' | 'income') => void; periodText: string; loading?: boolean; busy?: boolean; onPick: (s: Slice) => void }) {
  const slices = topSlices(data?.items ?? [])
  const [hover, setHover] = useState<string | null>(null)
  const animate = useFirstAnimation(!loading)
  const empty = !loading && (!slices.length || decSign(data?.total) <= 0)
  const lead = slices[0]
  const summary = empty || !lead ? 'Нет данных за период' : `Больше всего ${kind === 'income' ? 'пришло' : 'ушло'} на «${lead.name}»: ${formatPercent(lead.share)}`
  return (
    <ChartCard
      title="Категории"
      summary={summary}
      loading={loading}
      busy={busy}
      empty={empty}
      height={260}
      actions={<Segmented size="sm" label="Расходы или доходы" value={kind} onChange={onKind} options={[{ value: 'expense', label: 'Расходы' }, { value: 'income', label: 'Доходы' }]} />}
      table={<DataTable head={['Категория', 'Сумма', 'Доля', 'Операций']} rows={(data?.items ?? []).map((s) => [s.name, formatMoney(s.amount, currency), formatPercent(s.share), String(s.count)])} />}
    >
      <div className="grid items-center gap-4 sm:grid-cols-[176px_minmax(0,1fr)] sm:gap-3">
        <div className="relative mx-auto size-[200px] sm:size-[176px]">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={slices}
                dataKey="value"
                nameKey="name"
                innerRadius="68%"
                outerRadius="100%"
                paddingAngle={1}
                cornerRadius={3}
                stroke="var(--card)"
                strokeWidth={2}
                isAnimationActive={animate}
                animationDuration={320}
                onMouseEnter={(_, i) => setHover(slices[i]?.key ?? null)}
                onMouseLeave={() => setHover(null)}
                onClick={(_, i) => slices[i] && onPick(slices[i])}
                cursor="pointer"
              >
                {slices.map((s) => (
                  <Cell key={s.key} fill={s.color} fillOpacity={hover && hover !== s.key ? 0.35 : 1} />
                ))}
              </Pie>
              <Tooltip content={({ active, payload }) => { const s = payload?.[0]?.payload as Slice | undefined; return s ? <ChartTooltip active={active} rows={[{ color: s.color, label: s.name, value: `${formatMoney(s.amount, currency)} · ${formatPercent(s.share)}` }]} /> : null }} />
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 grid place-content-center text-center">
            <span className="num text-[18px] leading-6 font-semibold tracking-[-0.01em]">{formatMoney(data?.total ?? null, currency)}</span>
            <span className="text-xs text-muted-foreground">{periodText}</span>
          </div>
        </div>
        <ul className="grid gap-0.5" aria-label="Легенда">
          {slices.map((s) => (
            <li key={s.key}>
              <button
                type="button"
                onMouseEnter={() => setHover(s.key)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(s.key)}
                onBlur={() => setHover(null)}
                onClick={() => onPick(s)}
                title={s.name}
                className={cn('grid w-full grid-cols-[10px_minmax(0,1fr)_auto_36px] items-center gap-x-2 rounded-sm px-1 py-1 text-left text-[13px] leading-4 hover:bg-surface-2', hover && hover !== s.key && 'opacity-50')}
              >
                <span className="size-2.5 rounded-full" style={{ background: s.color }} />
                <span className="line-clamp-2 break-words">
                  {s.name}
                  {s.isNew && ' 🆕'}
                </span>
                <span className="num text-right">{formatMoney(s.amount, currency)}</span>
                <span className="num text-right text-muted-foreground">{formatPercent(s.share)}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </ChartCard>
  )
}

export function CompareCard({ current, previous, currency, title, picker, loading, busy, onPick }: { current?: ByCategoryOut; previous?: ByCategoryOut; currency: string; title: string; picker: React.ReactNode; loading?: boolean; busy?: boolean; onPick: (categoryId: number) => void }) {
  const pairs = comparePairs(current?.items ?? [], previous?.items ?? [])
  const max = Math.max(1, ...pairs.flatMap((p) => [decToNumber(p.current), decToNumber(p.previous)]))
  const empty = !loading && !pairs.length
  const summary = empty ? 'Нет данных для сравнения' : compareSummary(pairs, currency)
  return (
    <ChartCard
      title={title}
      summary={summary}
      loading={loading}
      busy={busy}
      empty={empty}
      height={200}
      actions={picker}
      table={<DataTable head={['Категория', 'Этот', 'Прошлый', 'Δ']} rows={pairs.map((p) => [p.name, formatMoney(p.current, currency), formatMoney(p.previous, currency), p.change ? formatPercent(p.change, { sign: 'always' }) : '—'])} />}
    >
      <ul className="grid gap-2.5">
        {pairs.map((p) => {
          const grew = p.change !== null && Number(p.change) > 0
          return (
            <li key={p.categoryId}>
              <button type="button" onClick={() => onPick(p.categoryId)} className="grid w-full grid-cols-[minmax(72px,120px)_minmax(0,1fr)_52px] items-center gap-3 rounded-sm text-left hover:bg-surface-2 max-sm:grid-cols-[minmax(0,1fr)_52px]">
                <span className="truncate text-[13px] max-sm:col-span-2">{p.name}</span>
                <span className="grid gap-1 max-sm:col-start-1">
                  <span className="flex items-center gap-2">
                    <span className="h-2.5 rounded-r-sm bg-chart-1" style={{ width: `${(decToNumber(p.current) / max) * 100}%`, minWidth: decSign(p.current) > 0 ? 2 : 0 }} />
                    <span className="num shrink-0 text-xs">{formatMoney(p.current, currency)}</span>
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="h-2.5 rounded-r-sm bg-muted-foreground/40" style={{ width: `${(decToNumber(p.previous) / max) * 100}%`, minWidth: decSign(p.previous) > 0 ? 2 : 0 }} />
                    <span className="num shrink-0 text-xs text-muted-foreground">{formatMoney(p.previous, currency)}</span>
                  </span>
                </span>
                <span className={cn('num text-right text-[13px] font-medium max-sm:row-start-2 max-sm:col-start-2', p.change === null || Number(p.change) === 0 ? 'text-muted-foreground' : grew ? 'text-expense' : 'text-income')}>{p.change === null ? 'новое' : formatPercent(p.change, { sign: 'always' })}</span>
              </button>
            </li>
          )
        })}
      </ul>
      <p className="mt-3 flex gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-chart-1" /> этот период</span>
        <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-muted-foreground/40" /> период для сравнения</span>
      </p>
    </ChartCard>
  )
}

function Stat({ label, value, tone, dot }: { label: string; value: string; tone?: 'income' | 'expense'; dot?: string }) {
  return (
    <div className="min-w-0 rounded-md bg-surface-2 px-3 py-2">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        {dot && <span className="size-1.5 rounded-full" style={{ background: dot }} />}
        {label}
      </div>
      <div className={cn('num truncate text-[15px] font-semibold', tone === 'income' && 'text-income', tone === 'expense' && 'text-expense')}>{value}</div>
    </div>
  )
}

export function BusinessCard({ data, months, currency, loading, busy }: { data?: BusinessOut; months: BizMonth[]; currency: string; loading?: boolean; busy?: boolean }) {
  const net = data?.business_net ?? null
  const summary = data ? `Бизнес: доход ${formatMoney(data.business_income, currency)}, расход ${formatMoney(data.business_expense, currency)}, итог ${formatMoney(data.business_net, currency, { sign: 'always' })}` : 'Загрузка'
  return (
    <ChartCard
      title="Бизнес и личное"
      summary={summary}
      loading={loading}
      busy={busy}
      height={260}
      table={<DataTable head={['Месяц', 'Бизнес-расход', 'Личный расход']} rows={months.map((m) => [m.month.slice(0, 7), formatMoney(m.business, currency), formatMoney(m.personal, currency)])} />}
    >
      <div className="grid gap-3">
        <div className="grid grid-cols-3 gap-2">
          <Stat label="Бизнес: доход" value={formatMoney(data?.business_income ?? null, currency, { sign: 'always' })} tone="income" dot="var(--chart-1)" />
          <Stat label="Бизнес: расход" value={formatMoney(data?.business_expense ?? null, currency)} dot="var(--chart-1)" />
          <Stat label="Бизнес: итог" value={formatMoney(net, currency, { sign: 'always' })} tone={net === null ? undefined : decSign(net) >= 0 ? 'income' : 'expense'} />
          <Stat label="Личное: доход" value={formatMoney(data?.personal_income ?? null, currency, { sign: 'always' })} dot="var(--chart-2)" />
          <Stat label="Личное: расход" value={formatMoney(data?.personal_expense ?? null, currency)} dot="var(--chart-2)" />
        </div>
        <BizStackChart months={months} currency={currency} loading={loading} />
        <p className="flex gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-chart-1" /> Бизнес-расходы</span>
          <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-chart-2" /> Личные расходы</span>
        </p>
      </div>
    </ChartCard>
  )
}

export function TopCard({ items, currency, today, loading, busy, onPick }: { items: TopItem[]; currency: string; today: string; loading?: boolean; busy?: boolean; onPick: (txId: number) => void }) {
  const max = Math.max(1, ...items.map((i) => decToNumber(i.amount)))
  const empty = !loading && !items.length
  return (
    <Card title="Топ покупок" busy={busy}>
      {loading ? (
        <div className="h-60" aria-busy="true" />
      ) : empty ? (
        <p className="grid h-40 place-items-center text-sm text-muted-foreground">Нет данных за период</p>
      ) : (
        <ol className="grid gap-1" aria-label="Самые крупные покупки">
          {items.map((it, i) => (
            <li key={it.transaction_id}>
              <button type="button" onClick={() => onPick(it.transaction_id)} className="grid w-full grid-cols-[20px_minmax(0,1fr)_auto] items-center gap-x-2 rounded-sm px-1 py-1.5 text-left hover:bg-surface-2">
                <span className="num text-[13px] text-muted-foreground">{i + 1}</span>
                <span className="min-w-0">
                  <span className="block truncate text-[13px] font-medium">{it.title}</span>
                  <span className="mt-1 block h-1.5 rounded-full bg-chart-1/80" style={{ width: `${Math.max(2, (decToNumber(it.amount) / max) * 100)}%` }} aria-hidden />
                </span>
                <span className="text-right">
                  <span className="num block text-[13px] font-medium">{formatMoney(it.amount, currency)}</span>
                  <span className="num block text-xs text-muted-foreground">
                    {it.original_currency !== currency ? `${formatMoney(it.original_amount, it.original_currency)} · ` : ''}
                    {formatDateShort(it.occurred_on, today)}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}
