import { describe, expect, it } from 'vitest'
import { comparePairs, compareSummary, topSlices } from './slices'
import { comparePeriod, isTrimmed, lastMonths, parseAnalyticsPeriod, periodParam, periodTitle, seriesGranularity, shiftPeriod, sideLabel, statsSpan, withRange, withScale } from './period'
import { monthForecast } from '../charts/bars'

const sp = (s: string) => new URLSearchParams(s)
const T = '2026-10-27'

describe('analytics period', () => {
  it('parses URL forms and bot presets', () => {
    expect(parseAnalyticsPeriod(sp(''), T)).toEqual({ scale: 'month', from: '2026-10-01', to: '2026-10-31' })
    expect(parseAnalyticsPeriod(sp('period=this_month'), T).from).toBe('2026-10-01')
    expect(parseAnalyticsPeriod(sp('period=last_month'), T)).toEqual({ scale: 'month', from: '2026-09-01', to: '2026-09-30' })
    expect(parseAnalyticsPeriod(sp('period=2026-Q3'), T)).toEqual({ scale: 'quarter', from: '2026-07-01', to: '2026-09-30' })
    expect(parseAnalyticsPeriod(sp('period=2025'), T)).toEqual({ scale: 'year', from: '2025-01-01', to: '2025-12-31' })
    expect(parseAnalyticsPeriod(sp('period=custom&from=2026-10-05&to=2026-10-20'), T)).toEqual({ scale: 'custom', from: '2026-10-05', to: '2026-10-20' })
    expect(parseAnalyticsPeriod(sp('period=last_7_days'), T)).toEqual({ scale: 'custom', from: '2026-10-21', to: '2026-10-27' })
  })
  it('shifts, rescales, compares', () => {
    const oct = parseAnalyticsPeriod(sp(''), T)
    expect(shiftPeriod(oct, -1)).toEqual({ scale: 'month', from: '2026-09-01', to: '2026-09-30' })
    expect(comparePeriod(oct, 'yoy')).toEqual({ scale: 'month', from: '2025-10-01', to: '2025-10-31' })
    expect(withScale(oct, 'quarter', T)).toEqual({ scale: 'quarter', from: '2026-10-01', to: '2026-12-31' })
    expect(periodParam(withScale(oct, 'quarter', T))).toEqual({ period: '2026-Q4' })
    expect(shiftPeriod({ scale: 'custom', from: '2026-10-05', to: '2026-10-11' }, -1)).toEqual({ scale: 'custom', from: '2026-09-28', to: '2026-10-04' })
    expect(lastMonths('2026-10-31', 3)).toEqual(['2026-08-01', '2026-09-01', '2026-10-01'])
    expect(Math.round(monthForecast('2700', '2026-10-01', T)!)).toBe(3100)
    expect(monthForecast('2700', '2026-09-01', T)).toBeNull()
  })
})

describe('regressions', () => {
  it('all_time stays a backend preset; its range comes from the answer', () => {
    const p = parseAnalyticsPeriod(sp('period=all_time'), T)
    expect(statsSpan(p)).toEqual({ preset: 'all_time' })
    expect(periodParam(p)).toEqual({ period: 'all_time' })
    expect(periodTitle(p)).toBe('За всё время')
    expect(shiftPeriod(p, -1)).toBe(p)
    const r = withRange(p, { date_from: '2026-08-15', date_to: T })
    expect([r.from, r.to]).toEqual(['2026-08-15', T])
    expect(seriesGranularity(r)).toBe('day')
    expect(seriesGranularity(withRange(p, { date_from: '2000-01-01', date_to: T }))).toBe('month')
    expect(statsSpan(parseAnalyticsPeriod(sp(''), T))).toEqual({ preset: 'custom', date_from: '2026-10-01', date_to: '2026-10-31' })
  })
  it('a running period is compared with the same elapsed days', () => {
    const oct = parseAnalyticsPeriod(sp(''), T)
    expect(comparePeriod(oct, 'prev', T)).toEqual({ scale: 'month', from: '2026-09-01', to: '2026-09-27' })
    expect(isTrimmed(comparePeriod(oct, 'prev', T))).toBe(true)
    expect(comparePeriod(oct, 'yoy', T)).toEqual({ scale: 'month', from: '2025-10-01', to: '2025-10-27' })
    // a finished month — full previous month; the last day of a month counts as finished (backend: today < d2)
    expect(comparePeriod(parseAnalyticsPeriod(sp('period=2026-09'), T), 'prev', T)).toEqual({ scale: 'month', from: '2026-08-01', to: '2026-08-31' })
    expect(comparePeriod(parseAnalyticsPeriod(sp(''), '2026-10-31'), 'prev', '2026-10-31')).toEqual({ scale: 'month', from: '2026-09-01', to: '2026-09-30' })
    // 31 March → previous month is shorter: capped at its end
    expect(comparePeriod(parseAnalyticsPeriod(sp(''), '2026-03-30'), 'prev', '2026-03-30')).toEqual({ scale: 'month', from: '2026-02-01', to: '2026-02-28' })
    expect(comparePeriod(withScale(oct, 'quarter', T), 'prev', T)).toEqual({ scale: 'quarter', from: '2026-07-01', to: '2026-07-27' })
  })
  it('comparison side labels keep the year when it differs', () => {
    const oct = parseAnalyticsPeriod(sp(''), T)
    expect(sideLabel(oct, T, true)).toBe('1–27 окт')
    expect(sideLabel(comparePeriod(oct, 'prev', T), T, false)).toBe('1–27 сен')
    expect(sideLabel(comparePeriod(oct, 'yoy', T), T, false)).toBe('1–27 окт 2025')
    expect(sideLabel(parseAnalyticsPeriod(sp('period=2026-09'), T), T, false)).toBe('Сентябрь')
    expect(sideLabel(parseAnalyticsPeriod(sp('period=2025-09'), T), T, false)).toBe('Сентябрь 2025')
  })
  it('comparison summary counts new categories as growth', () => {
    const pair = (name: string, current: string, previous: string, change: string | null) => ({ categoryId: name.length, name, current, previous, change })
    expect(compareSummary([pair('Серверы/инфра', '230', '0', null), pair('Еда', '104', '0', null)], 'USD')).toBe('Новые траты: «Серверы/инфра» $230.00 и ещё 1')
    expect(compareSummary([pair('Еда', '110', '100', '0.1'), pair('Кофе', '20', '0', null)], 'USD')).toBe('Сильнее всего выросли траты на «Еда»: +10%; новые траты: «Кофе» $20.00')
    expect(compareSummary([pair('Еда', '90', '100', '-0.1')], 'USD')).toBe('Траты по категориям не выросли')
  })
})

describe('slices', () => {
  const s = (id: number, amount: string, share: string) => ({ category_id: id, name: `c${id}`, icon: null, color: null, is_business: false, is_new: false, amount, share, count: 1 })
  it('top-7 + exact «ещё N»', () => {
    const items = Array.from({ length: 10 }, (_, i) => s(i + 1, `${100 - i}.1`, '0.1'))
    const r = topSlices(items)
    expect(r).toHaveLength(8)
    expect(r[0].color).toBe('var(--chart-1)')
    expect(r[7]).toMatchObject({ key: 'other', name: 'ещё 3', amount: '276.3', share: '0.3', categoryIds: [8, 9, 10], color: 'var(--chart-8)' })
  })
  it('pairs current vs previous', () => {
    const pairs = comparePairs([s(1, '110', '0.5'), s(2, '50', '0.5')], [s(1, '100', '1'), s(3, '20', '1')])
    expect(pairs.map((p) => [p.categoryId, p.current, p.previous])).toEqual([[1, '110', '100'], [2, '50', '0'], [3, '0', '20']])
    expect(Number(pairs[0].change)).toBeCloseTo(0.1)
    expect(pairs[1].change).toBeNull()
  })
})
