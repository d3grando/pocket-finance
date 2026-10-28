// «Аналитика» (DESIGN §10.5): period navigator, ₫/$ switch, 5 KPIs, charts from /stats/* (real backend
// data; currency conversion happens on the backend by the rate on each operation's date).
import { useMemo, useState } from 'react'
import { keepPreviousData, useQueries, useQuery } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router'
import { CalendarDays, ChevronLeft, ChevronRight, WifiOff } from 'lucide-react'
import { api, qk, type BusinessOut, type ByCategoryOut, type CompareOut, type DailyAverageOut, type SeriesOut, type TopOut } from '@/lib/api'
import { cn } from '@/lib/utils'
import { isOfflineEmpty, loadErrorMessage, useSettings, useToday } from '@/features/operations/api'
import { decNeg, decSign } from '@/features/operations/lib/decimal'
import { endOfMonth, rangeLabel } from '@/features/operations/lib/dates'
import { formatMoney, formatPercent } from '@/features/operations/lib/format'
import { Button, EmptyState, ErrorNote, Field, Input, NativeSelect, Page, PageHeader, Segmented, Skeleton } from '@/features/operations/ui/kit'
import { useDelayed } from '@/features/operations/ui/hooks'
import { Popover } from '@/features/operations/ui/overlays'
import { comparePeriod, compareLabel, isAllTime, isCurrent, isTrimmed, lastMonths, parseAnalyticsPeriod, periodParam, periodTitle, seriesGranularity, shiftPeriod, sideLabel, statsSpan, withRange, withScale, type AnPeriod, type Scale } from './lib/period'
import type { Slice } from './lib/slices'
import { DailyChart, MonthlyChart, type BizMonth } from './charts/bars'
import { BusinessCard, CategoryDonut, CompareCard, TopCard } from './charts/panels'

type Cur = 'USD' | 'VND'

function useStat<T>(kind: string, path: string, params: Record<string, string | number | undefined>, enabled = true) {
  return useQuery({
    queryKey: qk.stats(kind, params),
    queryFn: ({ signal }) => api.get<T>(path, { query: params, signal }),
    placeholderData: keepPreviousData,
    enabled,
  })
}

function Kpi({ label, value, sub, subTone, subTitle, dot, loading }: { label: string; value: string; sub?: string | null; subTone?: 'income' | 'expense'; subTitle?: string; dot?: string; loading?: boolean }) {
  const skel = useDelayed(!!loading)
  return (
    <div className="min-w-0 rounded-lg border border-border bg-card px-4 py-3 shadow-xs">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        {dot && <span className="size-1.5 rounded-full" style={{ background: dot }} />}
        {label}
      </div>
      {loading ? (
        <div className="mt-1.5 grid gap-1.5">{skel && <><Skeleton className="h-6 w-3/5" /><Skeleton className="h-3 w-2/5" /></>}</div>
      ) : (
        <>
          <div className="num mt-0.5 truncate text-[18px] leading-6 font-semibold tracking-[-0.01em] lg:text-[22px] lg:leading-7" translate="no">
            {value}
          </div>
          <div className={cn('num h-[18px] truncate text-[13px] text-muted-foreground', subTone === 'income' && 'text-income', subTone === 'expense' && 'text-expense')} title={subTitle}>{sub ?? ''}</div>
        </>
      )}
    </div>
  )
}

function changeText(ratio: string | null | undefined, label: string): string | null {
  if (ratio === null || ratio === undefined) return null
  return `${formatPercent(ratio, { sign: 'always' })} ${label}`
}

export function AnalyticsView() {
  const [sp, setSp] = useSearchParams()
  const navigate = useNavigate()
  const { today } = useToday()
  const { data: settings } = useSettings()
  const parsed = useMemo(() => parseAnalyticsPeriod(sp, today), [sp, today])
  const allTime = isAllTime(parsed)
  const cur: Cur = (sp.get('cur') ?? settings?.analytics_currency ?? 'USD') === 'VND' ? 'VND' : 'USD'
  const [cmpMode, setCmpMode] = useState<'prev' | 'yoy'>('prev')
  const [catKind, setCatKind] = useState<'expense' | 'income'>('expense')

  const go = (p: AnPeriod, curOverride?: Cur) => {
    const next = new URLSearchParams(periodParam(p))
    const c = curOverride ?? (sp.get('cur') as Cur | null)
    if (c) next.set('cur', c)
    setSp(next, { replace: true })
  }

  // all_time: the backend resolves the first operation date; the range comes back with the answer.
  const base = { ...statsSpan(parsed), currency: cur }
  const avg = useStat<DailyAverageOut>('daily-average', '/stats/daily-average', base)
  const rangeKnown = !allTime || (!!avg.data && !avg.isPlaceholderData)
  const period = withRange(parsed, rangeKnown && allTime ? avg.data?.range : null)
  const running = isCurrent(period, today) && today < period.to
  const prev = comparePeriod(period, 'prev', today)
  const cmp = comparePeriod(period, cmpMode, today)
  const compare = useStat<CompareOut>('compare', '/stats/compare', allTime ? base : { ...base, prev_date_from: prev.from, prev_date_to: prev.to })
  const business = useStat<BusinessOut>('business', '/stats/business', base)
  const granularity = rangeKnown ? seriesGranularity(period) : 'day'
  const days = useStat<SeriesOut>('series', '/stats/series', { ...base, granularity }, rangeKnown)
  const months = lastMonths(period.to, 12)
  const monthly = useStat<SeriesOut>('series', '/stats/series', { preset: 'custom', date_from: months[0], date_to: endOfMonth(months[11]), granularity: 'month', currency: cur })
  const byCat = useStat<ByCategoryOut>('by-category', '/stats/by-category', { ...base, kind: catKind })
  const byCatNow = useStat<ByCategoryOut>('by-category', '/stats/by-category', { ...base, kind: 'expense' }, !allTime)
  const byCatPrev = useStat<ByCategoryOut>('by-category', '/stats/by-category', { ...statsSpan(cmp), currency: cur, kind: 'expense' }, !allTime)
  const top = useStat<TopOut>('top', '/stats/top', { ...base, limit: 10 })
  const bizMonthsList = months.slice(-6)
  const bizMonths = useQueries({
    queries: bizMonthsList.map((m) => {
      const params = { preset: 'custom', date_from: m, date_to: endOfMonth(m), currency: cur }
      return { queryKey: qk.stats('business', params), queryFn: () => api.get<BusinessOut>('/stats/business', { query: params }), placeholderData: keepPreviousData }
    }),
  })
  const bizMonthData: BizMonth[] = bizMonthsList.map((m, i) => ({ month: m, business: bizMonths[i].data?.business_expense ?? '0', personal: bizMonths[i].data?.personal_expense ?? '0' }))

  const main = [compare, avg, business, days, monthly, byCat, top]
  // Offline with nothing cached: TanStack pauses the query (no data, no error) — never show «нет данных».
  const offline = main.filter((q) => isOfflineEmpty(q))
  // disabled queries (all_time series before the range is known) do not count against «everything is offline»
  const allOffline = offline.length > 0 && main.every((q) => isOfflineEmpty(q) || (q.data === undefined && q.fetchStatus === 'idle'))
  const firstError = main.find((q) => q.error && !isOfflineEmpty(q))?.error
  const retry = () => main.forEach((q) => void q.refetch())
  const waiting = (q: { isLoading: boolean; data?: unknown; fetchStatus: string; error?: unknown }) => q.isLoading || isOfflineEmpty(q)
  const cLabel = compareLabel(prev, today)
  const cTitle = isTrimmed(prev) ? `${rangeLabel(period.from, today)} к ${rangeLabel(prev.from, prev.to)}` : undefined
  const c = compare.data
  const title = periodTitle(period)
  const opsLink = (extra: Record<string, string>) => navigate(`/operations?${new URLSearchParams({ ...(allTime ? { period: 'all_time' } : { period: 'custom', from: period.from, to: period.to }), ...extra })}`)
  const pickCategory = (s: Slice) => opsLink({ cat: s.categoryIds.join(','), kind: catKind === 'income' ? 'income' : 'expense,refund' })

  return (
    <Page>
      <PageHeader
        title="Аналитика"
        actions={
          <>
            <div className="flex items-center gap-1">
              <Button size="iconSm" variant="ghost" aria-label="Предыдущий период" onClick={() => go(shiftPeriod(period, -1))} disabled={allTime}>
                <ChevronLeft />
              </Button>
              {period.scale === 'custom' ? (
                <CustomRange key={`${period.from}-${period.to}`} period={period} onApply={go} />
              ) : (
                <span className="num min-w-28 text-center text-[15px] font-semibold" aria-live="polite">{title}</span>
              )}
              <Button size="iconSm" variant="ghost" aria-label="Следующий период" onClick={() => go(shiftPeriod(period, 1))} disabled={allTime || (period.to >= today && period.scale !== 'custom')}>
                <ChevronRight />
              </Button>
            </div>
            <Segmented<Scale> label="Масштаб периода" value={period.scale} onChange={(s) => go(withScale(period, s, today))} options={[{ value: 'month', label: 'Месяц' }, { value: 'quarter', label: 'Квартал' }, { value: 'year', label: 'Год' }, { value: 'custom', label: 'Период' }]} />
            <Segmented<Cur> label="Валюта аналитики" value={cur} onChange={(v) => go(period, v)} options={[{ value: 'VND', label: '₫', title: 'Донги' }, { value: 'USD', label: '$', title: 'Доллары' }]} />
          </>
        }
      />
      {offline.length > 0 && !allOffline ? (
        <p className="mt-4 flex flex-wrap items-center gap-2 rounded-md bg-warning-soft px-3 py-2 text-sm text-warning" role="status">
          <WifiOff className="size-4" /> Нет связи — часть данных не загружена
          <Button size="sm" variant="ghost" onClick={retry}>Повторить</Button>
        </p>
      ) : null}
      {firstError ? <ErrorNote className="mt-4" error={new Error(loadErrorMessage(firstError, 'Не удалось загрузить аналитику'))} onRetry={retry} /> : null}

      {allOffline ? (
        <EmptyState className="mt-4 rounded-lg border border-border bg-card" icon={<WifiOff />} title="Нет связи" text="Аналитика не загружена — проверьте интернет. Данные появятся, как только связь вернётся." action={<Button onClick={retry}>Повторить</Button>} />
      ) : (
        <>
          <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
            <Kpi label="Расходы" dot="var(--expense)" loading={waiting(compare)} value={c ? formatMoney(decNeg(c.current.expense), cur) : '—'} sub={allTime ? null : changeText(c?.expense_change_ratio, cLabel)} subTitle={cTitle} subTone={!allTime && c?.expense_change_ratio ? (decSign(c.expense_change_ratio) > 0 ? 'expense' : 'income') : undefined} />
            <Kpi label="Доходы" dot="var(--income)" loading={waiting(compare)} value={c ? formatMoney(c.current.income, cur, { sign: 'always' }) : '—'} sub={allTime ? null : changeText(c?.income_change_ratio, cLabel)} subTitle={cTitle} subTone={!allTime && c?.income_change_ratio ? (decSign(c.income_change_ratio) >= 0 ? 'income' : 'expense') : undefined} />
            <Kpi label="Поток" loading={waiting(compare)} value={c ? formatMoney(c.current.net, cur, { sign: 'always' }) : '—'} sub={c && !allTime ? `было ${formatMoney(c.previous.net, cur, { sign: 'always' })}` : null} subTitle={cTitle} />
            <Kpi label="В среднем за день" loading={waiting(avg)} value={avg.data ? formatMoney(decNeg(avg.data.average), cur) : '—'} sub={avg.data ? `${avg.data.days} дн. · ${isCurrent(period, today) ? 'по сегодня' : 'за период'}` : null} />
            <Kpi label="Бизнес: итог" dot="var(--chart-1)" loading={waiting(business)} value={business.data ? formatMoney(business.data.business_net, cur, { sign: 'always' }) : '—'} sub={business.data ? `доход ${formatMoney(business.data.business_income, cur)}` : null} />
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-12">
            <div className="min-w-0 lg:col-span-7">
              <DailyChart
                points={days.data?.points ?? []}
                granularity={granularity}
                average={avg.data?.average ?? null}
                currency={cur}
                today={today}
                loading={waiting(days) || !rangeKnown}
                busy={days.isFetching && !days.isLoading}
                onPick={(d) => navigate(granularity === 'month' ? `/operations?period=${d.slice(0, 7)}` : `/operations?${new URLSearchParams({ period: 'custom', from: d, to: d })}`)}
              />
            </div>
            <div className="min-w-0 lg:col-span-5">
              <CategoryDonut data={byCat.data} currency={cur} kind={catKind} onKind={setCatKind} periodText={allTime ? 'за всё время' : `за ${title.toLowerCase()}`} loading={waiting(byCat)} busy={byCat.isFetching && !byCat.isLoading} onPick={pickCategory} />
            </div>
            {!allTime && (
              <div className="min-w-0 lg:col-span-7">
                <CompareCard
                  title={`Сравнение: ${sideLabel(period, today, running)} vs ${sideLabel(cmp, today, false)}`}
                  current={byCatNow.data}
                  previous={byCatPrev.data}
                  currency={cur}
                  loading={waiting(byCatNow) || waiting(byCatPrev)}
                  busy={byCatPrev.isFetching && !byCatPrev.isLoading}
                  onPick={(id) => opsLink({ cat: String(id) })}
                  picker={
                    <NativeSelect aria-label="С чем сравнить" value={cmpMode} onChange={(e) => setCmpMode(e.target.value as 'prev' | 'yoy')} className="h-8 w-auto text-[13px]">
                      <option value="prev">с прошлым периодом</option>
                      <option value="yoy">с прошлым годом</option>
                    </NativeSelect>
                  }
                />
              </div>
            )}
            <div className={cn('min-w-0', allTime ? 'lg:col-span-12' : 'lg:col-span-5')}>
              <MonthlyChart points={monthly.data?.points ?? []} currency={cur} today={today} loading={waiting(monthly)} busy={monthly.isFetching && !monthly.isLoading} onPick={(m) => navigate(`/operations?period=${m}`)} />
            </div>
            <div className="min-w-0 lg:col-span-7">
              <BusinessCard data={business.data} months={bizMonthData} currency={cur} loading={waiting(business)} busy={business.isFetching && !business.isLoading} />
            </div>
            <div className="min-w-0 lg:col-span-5">
              <TopCard items={top.data?.items ?? []} currency={cur} today={today} loading={waiting(top)} busy={top.isFetching && !top.isLoading} onPick={(id) => navigate(`/operations?tx=${id}`)} />
            </div>
          </div>
        </>
      )}
    </Page>
  )
}

function CustomRange({ period, onApply }: { period: AnPeriod; onApply: (p: AnPeriod) => void }) {
  const [open, setOpen] = useState(false)
  const [from, setFrom] = useState(period.from)
  const [to, setTo] = useState(period.to)
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger={
        <Button variant="ghost" size="sm" className="num text-[15px] font-semibold">
          <CalendarDays /> {periodTitle(period)}
        </Button>
      }
    >
      <form
        className="grid gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (from && to && from <= to) {
            onApply({ scale: 'custom', from, to })
            setOpen(false)
          }
        }}
      >
        <div className="grid grid-cols-2 gap-2">
          <Field label="С" htmlFor="an-from">
            <Input id="an-from" type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} />
          </Field>
          <Field label="По" htmlFor="an-to">
            <Input id="an-to" type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
          </Field>
        </div>
        <Button type="submit" variant="primary" size="sm" disabled={!from || !to || from > to}>
          Показать
        </Button>
      </form>
    </Popover>
  )
}
