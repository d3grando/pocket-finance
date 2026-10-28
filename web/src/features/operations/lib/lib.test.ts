import { describe, expect, it } from 'vitest'
import { decAdd, decCmp, decNeg, fromUnits, normalizeAmountInput, shiftDecimal, toUnits } from './decimal'
import { formatAxis, formatMoney, formatPercent, localFormatCompact, localFormatMoney, plural } from './format'
import { addMonths, dayGroupLabel, endOfMonth, formatTime, rangeLabel, resolvePreset, startOfWeek, todayInTz } from './dates'
import { DEFAULT_FILTER, filterToParams, parseAmountQuery, parseFilter, searchFilterToParams, toApiFilter } from './filters'
import { dayTotal, groupByDay, totalsLine, txAmountView, txTitle } from './tx'
import { sampleTxs, tx } from '../test/fixtures'

const nb = (s: string) => s.replace(/ /g, ' ')

describe('decimal', () => {
  it('is exact and float-free', () => {
    expect(decAdd('0.1', '0.2')).toBe('0.3')
    expect(decAdd('1250000', '-420000', '0.00000001')).toBe('830000.00000001')
    expect(fromUnits(toUnits('-3.50'))).toBe('-3.5')
    expect(decNeg('12.4')).toBe('-12.4')
    expect(decCmp('100', '99.99999999')).toBe(1)
    expect(toUnits('150')).toBe(15000000000n)
    expect(toUnits('−5')).toBe(-500000000n)
    expect(shiftDecimal('1.5', 6)).toBe('1500000')
  })
  it('normalises typed amounts', () => {
    expect(normalizeAmountInput('150 000')).toBe('150000')
    expect(normalizeAmountInput('1,5м')).toBe('1500000')
    expect(normalizeAmountInput('85к')).toBe('85000')
    expect(normalizeAmountInput('21.3m')).toBe('21300000')
    expect(normalizeAmountInput('12.40')).toBe('12.4')
    expect(normalizeAmountInput('0')).toBeNull()
    expect(normalizeAmountInput('abc')).toBeNull()
    expect(normalizeAmountInput('-5')).toBeNull()
  })
})

describe('format (DESIGN §3.2 examples)', () => {
  it.each([
    [['1250000', 'VND'], '1 250 000 ₫'],
    [['-420000', 'VND', { sign: 'always' }], '−420 000 ₫'],
    [['300', 'USDT', { sign: 'always' }], '+300 USDT'],
    [['12.4', 'USD', { approx: true }], '≈ $12.40'],
    [['-180', 'USD'], '−$180.00'],
    [['3', 'TON'], '3 TON'],
    [['0.02150000', 'BTC'], '0.0215 BTC'],
    [['4990.5', 'RUB'], '4 990.50 ₽'],
    [['4990', 'RUB'], '4 990 ₽'],
    [['12.5', 'USDT'], '12.50 USDT'],
    [['0', 'VND'], '0 ₫'],
    [['1250000.5', 'VND'], '1 250 000 ₫'],
    [['1250001.5', 'VND'], '1 250 002 ₫'],
  ] as const)('%j → %s', (args, out) => {
    const [a, c, o] = args as unknown as [string, string, object | undefined]
    expect(localFormatMoney(a, c, o)).toBe(nb(out))
    expect(formatMoney(a, c, o)).toBe(nb(out))
  })
  it('compact, percent, axis, null', () => {
    expect(localFormatCompact('1250000', 'VND')).toBe(nb('1.25м ₫'))
    expect(localFormatCompact('450000', 'VND')).toBe(nb('450к ₫'))
    expect(localFormatCompact('1200', 'USD')).toBe('$1.2k')
    expect(formatPercent('0.183', { sign: 'always' })).toBe('+18%')
    expect(formatPercent('0.045', { sign: 'always' })).toBe('+4.5%')
    expect(formatPercent('-0.16', { sign: 'always' })).toBe('−16%')
    expect(formatAxis(1_200_000, 'VND')).toBe('1.2м')
    expect(formatAxis(450_000, 'VND')).toBe('450к')
    expect(formatMoney(null, 'VND')).toBe('—')
    expect(plural(1, ['операция', 'операции', 'операций'])).toBe('операция')
    expect(plural(3, ['операция', 'операции', 'операций'])).toBe('операции')
    expect(plural(12, ['операция', 'операции', 'операций'])).toBe('операций')
  })
})

describe('dates (user tz, not browser tz)', () => {
  it('today in Asia/Ho_Chi_Minh across the UTC midnight', () => {
    expect(todayInTz('Asia/Ho_Chi_Minh', new Date('2026-10-26T18:30:00Z'))).toBe('2026-10-27')
    expect(todayInTz('UTC', new Date('2026-10-26T18:30:00Z'))).toBe('2026-10-26')
    expect(formatTime('2026-10-27T07:32:00Z', 'Asia/Ho_Chi_Minh')).toBe('14:32')
  })
  it('presets and labels', () => {
    expect(resolvePreset('this_month', '2026-10-27')).toEqual({ from: '2026-10-01', to: '2026-10-31' })
    expect(resolvePreset('last_month', '2026-03-31')).toEqual({ from: '2026-02-01', to: '2026-02-28' })
    expect(resolvePreset('last_7_days', '2026-10-27')).toEqual({ from: '2026-10-21', to: '2026-10-27' })
    expect(resolvePreset('this_week', '2026-10-27')).toEqual({ from: '2026-10-26', to: '2026-11-01' })
    expect(startOfWeek('2026-11-01')).toBe('2026-10-26')
    expect(addMonths('2026-01-31', 1)).toBe('2026-02-28')
    expect(endOfMonth('2024-02-10')).toBe('2024-02-29')
    expect(rangeLabel('2026-10-01', '2026-10-31')).toBe('Октябрь 2026')
    expect(rangeLabel('2026-10-01', '2026-10-27')).toBe('1–27 окт')
    expect(rangeLabel('2026-09-20', '2026-10-05')).toBe('20 сен–5 окт')
    expect(dayGroupLabel('2026-10-27', '2026-10-27')).toBe('Сегодня · вт, 27 октября')
    expect(dayGroupLabel('2026-10-26', '2026-10-27')).toBe('Вчера · пн, 26 октября')
    expect(dayGroupLabel('2026-10-23', '2026-10-27')).toBe('Пятница, 23 октября')
  })
})

describe('filters ⇄ URL ⇄ API', () => {
  it('round-trips and omits defaults', () => {
    const f = parseFilter(new URLSearchParams('period=2026-10&cat=1,2&acc=3&kind=expense,income&biz=business&q=кофе'))
    expect(f).toMatchObject({ period: '2026-10', cat: [1, 2], acc: [3], kind: ['expense', 'income'], biz: 'business', q: 'кофе' })
    expect(filterToParams(f).toString()).toBe('period=2026-10&q=%D0%BA%D0%BE%D1%84%D0%B5&cat=1%2C2&acc=3&kind=expense%2Cincome&biz=business')
    expect(filterToParams(DEFAULT_FILTER).toString()).toBe('')
    expect(parseFilter(new URLSearchParams('status=needs_account')).period).toBe('all_time')
    // the shell links use API-style names
    expect(parseFilter(new URLSearchParams('period=this_month&kind=expense&category_id=7')).cat).toEqual([7])
  })
  it('builds the GET /transactions filter', () => {
    const f = parseFilter(new URLSearchParams('period=this_month&cat=1&biz=personal&status=needs_account'))
    expect(toApiFilter(f, '2026-10-27')).toEqual({ sort: 'date_desc', date_from: '2026-10-01', date_to: '2026-10-31', category_id: [1], is_business: false, status: 'needs_account' })
    expect(toApiFilter(parseFilter(new URLSearchParams('period=all_time')), '2026-10-27').date_from).toBeUndefined()
  })
  it('understands amount search («>100$», «150к»)', () => {
    expect(parseAmountQuery('>100$')).toEqual({ min: '100', currency: 'USD' })
    expect(parseAmountQuery('< 50к')).toEqual({ max: '50000', currency: 'VND' })
    expect(parseAmountQuery('150к')).toEqual({ min: '150000', max: '150000', currency: 'VND' })
    expect(parseAmountQuery('от 20 usdt')).toEqual({ min: '20', currency: 'USDT' })
    expect(parseAmountQuery('кофе')).toBeNull()
    expect(parseAmountQuery('85')).toBeNull()
    const api = toApiFilter({ ...DEFAULT_FILTER, q: '>100$' }, '2026-10-27')
    expect(api).toMatchObject({ min_amount: '100', amount_currency: 'USD' })
    expect(api.q).toBeUndefined()
  })
  it('maps an AI SearchFilter to /operations params', () => {
    const sp = searchFilterToParams({ period: { preset: 'last_month' }, kinds: ['expense'], min_amount: '100', amount_currency: 'USD', category_ids: [1] })
    expect(Object.fromEntries(sp)).toEqual({ period: 'last_month', cat: '1', kind: 'expense', min: '100', amt_cur: 'USD' })
  })
})

describe('tx view', () => {
  it('signs and tones by kind', () => {
    expect(txAmountView(sampleTxs[0])).toMatchObject({ main: nb('−45 000 ₫'), tone: 'foreground', sub: null })
    expect(txAmountView(sampleTxs[2])).toMatchObject({ main: '−$180.00', sub: nb('≈ 4 705 200 ₫') })
    expect(txAmountView(sampleTxs[3])).toMatchObject({ main: nb('+300 USDT'), tone: 'income', sub: nb('≈ $300.00') })
    expect(txAmountView(sampleTxs[4])).toMatchObject({ main: nb('3 TON'), tone: 'transfer', sub: 'Кошелёк → Копилка' })
    expect(txAmountView(tx({ kind: 'adjustment', amount: '120000', account_amount: '-120000', category: null })).main).toBe(nb('−120 000 ₫'))
    expect(txTitle(sampleTxs[0])).toEqual({ title: 'латте · Highlands', category: 'Кофе', categoryDistinct: true })
    // ledger sends title = category name: description / merchant must win, category stays in its column
    expect(txTitle(tx({ title: 'Кофе', description: 'Highlands', category: { id: 3, name: 'Кофе', kind: 'expense', icon: 'Coffee', color: null, is_business: false, is_new: false } }))).toEqual({ title: 'Highlands', category: 'Кофе', categoryDistinct: true })
    expect(txTitle(tx({ title: 'Серверы/инфра', description: null, merchant: { id: 2, name: 'vast.ai' }, category: { id: 14, name: 'Серверы/инфра', kind: 'expense', icon: 'Server', color: null, is_business: true, is_new: false } })).title).toBe('vast.ai')
    expect(txTitle(tx({ title: 'Еда', description: 'еда' }))).toEqual({ title: 'Еда', category: 'Еда', categoryDistinct: false })
    expect(txTitle(tx({ title: 'Серверы/инфра · vast.ai', category: { id: 14, name: 'Серверы/инфра', kind: 'expense', icon: 'Server', color: null, is_business: true, is_new: false } })).title).toBe('Серверы/инфра · vast.ai')
  })
  it('groups by day with ₫ totals (expense − refund)', () => {
    const groups = groupByDay(sampleTxs)
    expect(groups.map((g) => g.date)).toEqual(['2026-10-27', '2026-10-26', '2026-10-25'])
    expect(groups[0].total).toBe(nb('−165 000 ₫'))
    expect(dayTotal([tx({ amount: '100000' }), tx({ kind: 'refund', amount: '30000' })])).toBe(nb('−70 000 ₫'))
    expect(dayTotal([sampleTxs[3]])).toBe(nb('+7 842 000 ₫'))
    // a day with both: two signed numbers, never «the expense» standing for the whole day
    expect(dayTotal([tx({ amount: '610000' }), tx({ kind: 'income', amount: '25000000' })])).toBe(`${nb('−610 000 ₫')} · ${nb('+25 000 000 ₫')}`)
    expect(totalsLine({ expense_vnd: '8350000', income_vnd: '0', expense_usd: '319.4', income_usd: '0', count: 42 })).toEqual([nb('−8 350 000 ₫'), 'расход ' + nb('≈ $319.40')])
  })
})
