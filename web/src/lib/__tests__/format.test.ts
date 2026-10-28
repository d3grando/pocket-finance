import { describe, expect, it } from 'vitest'
import {
  addDays, formatCompact, formatDayHeader, formatMoney, formatPercent, formatRelative, formatShortDate, localDateISO, plural,
} from '../format'

const S = (s: string) => s.replace(/ /g, ' ') // examples in DESIGN use U+00A0

describe('formatMoney — DESIGN §3.2 reference examples', () => {
  it.each([
    [['1250000', 'VND'], '1 250 000 ₫'],
    [['-420000', 'VND', { sign: 'always' }], '−420 000 ₫'],
    [['300', 'USDT', { sign: 'always' }], '+300 USDT'],
    [['12.4', 'USD', { approx: true }], '≈ $12.40'],
    [['-180', 'USD'], '−$180.00'],
    [['3', 'TON'], '3 TON'],
    [['0.02150000', 'BTC'], '0.0215 BTC'],
    [['4990.5', 'RUB'], '4 990.50 ₽'],
  ] as const)('%j → %s', (args, expected) => {
    expect(formatMoney(...(args as unknown as [string, string, object]))).toBe(S(expected))
  })

  it('more rules', () => {
    expect(formatMoney('4990', 'RUB')).toBe(S('4 990 ₽'))
    expect(formatMoney('12.5', 'USDT')).toBe(S('12.50 USDT'))
    // the confirmation echoes what was written (20.2552 must not become 20.26)
    expect(formatMoney('20.2552', 'USDT')).toBe(S('20.2552 USDT'))
    expect(formatMoney('0.12345678', 'USDT')).toBe(S('0.123457 USDT'))
    expect(formatMoney('1250', 'USD')).toBe(S('$1 250.00'))
    expect(formatMoney('0', 'VND')).toBe(S('0 ₫'))
    expect(formatMoney(null, 'VND')).toBe('—')
    expect(formatMoney('150000.5', 'VND')).toBe(S('150 000 ₫')) // half-even
    expect(formatMoney('150001.5', 'VND')).toBe(S('150 002 ₫'))
    expect(formatMoney('392100', 'VND', { approx: true, sign: 'auto' })).toBe(S('≈ 392 100 ₫'))
    expect(formatMoney('-5', 'VND', { sign: 'never' })).toBe(S('5 ₫'))
    expect(formatMoney('100', 'usdt')).toBe(S('100 USDT'))
  })

  it('never uses float: huge and precise values survive', () => {
    expect(formatMoney('123456789012345678.12345678', 'BTC')).toBe(S('123 456 789 012 345 678.12345678 BTC'))
    expect(formatMoney('0.1', 'TON')).toBe(S('0.1 TON'))
  })
})

describe('formatCompact', () => {
  it('DESIGN examples', () => {
    expect(formatCompact('1250000', 'VND')).toBe(S('1.25м ₫'))
    expect(formatCompact('450000', 'VND')).toBe(S('450к ₫'))
    expect(formatCompact('1240', 'USD')).toBe('$1.2k')
    expect(formatCompact('-12450000', 'VND')).toBe(S('−12.45м ₫'))
    expect(formatCompact('18300000', 'VND', { sign: 'always' })).toBe(S('+18.3м ₫'))
    expect(formatCompact('950', 'VND')).toBe(S('950 ₫'))
  })
})

describe('formatPercent', () => {
  it('rounds and signs', () => {
    expect(formatPercent('0.183', { sign: 'always' })).toBe('+18%')
    expect(formatPercent('0.045', { sign: 'always' })).toBe('+4.5%')
    expect(formatPercent('-0.12')).toBe('−12%')
    expect(formatPercent('0')).toBe('0%')
    expect(formatPercent(null)).toBe('—')
    expect(formatPercent(0.378)).toBe('38%')
  })
})

describe('dates in Asia/Ho_Chi_Minh, not the browser timezone', () => {
  const now = new Date('2026-10-27T09:00:00Z') // 16:00 in HCM
  it('local date of an instant', () => {
    expect(localDateISO(new Date('2026-10-26T18:30:00Z'))).toBe('2026-10-27') // 01:30 next day in HCM
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
  })
  it('relative', () => {
    expect(formatRelative('2026-10-27T07:32:00Z', now)).toBe('сегодня, 14:32')
    expect(formatRelative('2026-10-26T02:10:00Z', now)).toBe('вчера, 09:10')
    expect(formatRelative('2026-10-23T05:00:00Z', now)).toBe('пт, 23 окт') // 27.10.2026 is a Tuesday
    expect(formatShortDate('2025-10-24', now)).toBe('24 окт 2025')
  })
  it('day headers', () => {
    expect(formatDayHeader('2026-10-27', now)).toBe('Сегодня')
    expect(formatDayHeader('2026-10-26', now)).toBe('Вчера')
    expect(formatDayHeader('2026-10-24', now)).toBe('Суббота, 24 октября')
    expect(formatDayHeader('2025-12-31', now)).toBe('Среда, 31 декабря 2025')
  })
  it('plural', () => {
    expect(plural(1, ['операция', 'операции', 'операций'])).toBe('операция')
    expect(plural(3, ['операция', 'операции', 'операций'])).toBe('операции')
    expect(plural(12, ['операция', 'операции', 'операций'])).toBe('операций')
    expect(plural(21, ['операция', 'операции', 'операций'])).toBe('операция')
  })
})
