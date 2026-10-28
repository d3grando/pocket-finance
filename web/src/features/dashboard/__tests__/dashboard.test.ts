import { describe, expect, it } from 'vitest'
import type { AccountOut, CategorySlice, KpiOut } from '@/lib/api/types'
import { knownBalanceLines } from '../BalancesCard'
import { toSlices } from '../CategoryDonut'
import { toWeeks, weekStart } from '../DynamicsChart'
import { kpiTone, kpiValueText, prevMonthSuffix } from '../KpiTiles'
import { groupByDay } from '../RecentTransactions'
import { tx } from '@/test/fixtures'

const N = (s: string) => s.replace(/ /g, ' ')
const kpi = (value: string | null): KpiOut => ({ value, currency: 'VND', approx_usd: null, change_ratio: null, sparkline: [] })

describe('KPI tiles', () => {
  it('expenses are shown with «−», income with «+», null → «—», real zero → 0 ₫', () => {
    expect(kpiValueText(kpi('420000'), 'expense')).toBe(N('−420 000 ₫'))
    expect(kpiValueText(kpi('0'), 'expense')).toBe(N('0 ₫'))
    expect(kpiValueText(kpi(null), 'expense')).toBe('—')
    expect(kpiValueText(kpi('18300000'), 'income')).toBe(N('+18 300 000 ₫'))
    expect(kpiValueText(kpi('-5850000'), 'net')).toBe(N('−5 850 000 ₫'))
    expect(kpiValueText(kpi('12450000'), 'expense', true)).toBe(N('−12.45м ₫'))
  })
  it('net colour follows the sign', () => {
    expect(kpiTone(kpi('100'), 'net')).toBe('text-income')
    expect(kpiTone(kpi('-1'), 'net')).toBe('text-expense')
    expect(kpiTone(kpi('-1'), 'expense')).toBe('text-foreground')
  })
  it('«к сент.» for October', () => {
    expect(prevMonthSuffix('2026-10-01')).toBe(' к сент.')
    expect(prevMonthSuffix('2026-01-01')).toBe(' к дек.')
  })
})

describe('donut slices (DESIGN §1.5)', () => {
  const slice = (id: number, amount: string, share: string): CategorySlice => ({ category_id: id, name: `c${id}`, icon: null, color: null, is_business: false, is_new: false, amount, share, count: 1 })
  it('top-7 by amount keep positional colours, the tail folds into chart-8', () => {
    const items = Array.from({ length: 9 }, (_, i) => slice(i + 1, String((i + 1) * 100), '0.1'))
    const s = toSlices(items)
    expect(s).toHaveLength(8)
    expect(s[0]).toMatchObject({ id: 9, color: 'var(--chart-1)' })
    expect(s[7]).toMatchObject({ id: null, name: 'ещё 2', amount: '300', share: '0.2', color: 'var(--chart-8)' })
  })
  it('drops zero rows', () => {
    expect(toSlices([slice(1, '0', '0')])).toEqual([])
  })
})

describe('dynamics weeks', () => {
  it('Monday-based weeks with exact sums', () => {
    expect(weekStart('2026-10-27')).toBe('2026-10-26') // Tue → Mon
    expect(weekStart('2026-11-01')).toBe('2026-10-26') // Sun → Mon
    const w = toWeeks([
      { date: '2026-10-26', label: '', expense: '0.1', income: '0', net: '-0.1' },
      { date: '2026-10-27', label: '', expense: '0.2', income: '1', net: '0.8' },
      { date: '2026-11-02', label: '', expense: '5', income: '0', net: '-5' },
    ])
    expect(w).toHaveLength(2)
    expect(w[0]).toMatchObject({ key: '2026-10-26', expense: '0.3', income: '1', net: '0.7' })
  })
})

describe('balances: only known amounts', () => {
  const acc = (over: Partial<AccountOut>): AccountOut => ({
    id: 1, name: 'Карта', kind: 'bank', currency: 'VND', is_multi_asset: false, aliases: [], default_for_currencies: [], color: null, icon: null,
    sort_order: 0, is_archived: false, version: 1, assets: [], ...over,
  })
  it('unknown balance is never 0', () => {
    const r = knownBalanceLines([
      acc({ assets: [{ asset: 'VND', opening_balance: '1', opening_at: null, balance: '34120000', approx_usd: '1305.3', approx_vnd: '34120000' }] }),
      acc({ id: 2, name: 'Наличные', assets: [{ asset: 'VND', opening_balance: null, opening_at: null, balance: null, approx_usd: null, approx_vnd: null }] }),
      acc({ id: 3, name: 'Кошелёк', currency: null, is_multi_asset: true, assets: [
        { asset: 'USDT', opening_balance: '1', opening_at: null, balance: '312.4', approx_usd: '312.4', approx_vnd: null },
        { asset: 'TON', opening_balance: '1', opening_at: null, balance: '3', approx_usd: '16.2', approx_vnd: null },
      ] }),
    ])
    expect(r.unknown).toBe(1)
    expect(r.lines.map((l) => l.text)).toEqual([N('34 120 000 ₫'), `${N('312.40 USDT')} · ${N('3 TON')}`])
    expect(r.lines[1].approx).toBe(N('≈ $328.60'))
  })
})

describe('recent list grouping', () => {
  it('groups consecutive rows by local date', () => {
    const g = groupByDay([tx({ id: 1 }), tx({ id: 2 }), tx({ id: 3, occurred_on: '2026-10-26' })])
    expect(g.map(([d, items]) => [d, items.length])).toEqual([['2026-10-27', 2], ['2026-10-26', 1]])
  })
})
