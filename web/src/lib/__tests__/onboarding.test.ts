import { describe, expect, it } from 'vitest'
import { approxUsd, parseRow } from '@/pages/onboarding'
import type { AccountOut } from '@/lib/api/types'

const acc = (over: Partial<AccountOut>): AccountOut => ({
  id: 1, name: 'Карта', kind: 'bank', currency: 'VND', is_multi_asset: false, aliases: [], default_for_currencies: [], color: null, icon: null,
  sort_order: 0, is_archived: false, version: 1, assets: [], ...over,
})

describe('onboarding rows', () => {
  it('single-currency accounts understand «2.5м» and reject other currencies', () => {
    expect(parseRow(acc({}), '2.5м', true)).toEqual([{ amount: '2500000', asset: 'VND' }])
    expect(parseRow(acc({}), '', true)).toBe('empty')
    expect(parseRow(acc({}), '15 USDT', true)).toBeNull()
    expect(parseRow(acc({ currency: 'RUB' }), '4990.50', true)).toEqual([{ amount: '4990.5', asset: 'RUB' }])
  })
  it('wallets take a list of coins', () => {
    expect(parseRow(acc({ currency: null, is_multi_asset: true }), '312.4 USDT, 3 TON', true)).toEqual([{ amount: '312.4', asset: 'USDT' }, { amount: '3', asset: 'TON' }])
  })
  it('≈ USD total from latest rates (exact decimals)', () => {
    const rate = (base: string, quote: string, r: string) => ({ base, quote, rate: r, rate_date: '2026-10-27', kind: 'sell', source: 'x', fetched_at: null, is_fallback: false, label: '' })
    const total = approxUsd([{ amount: '2600000', asset: 'VND' }, { amount: '3', asset: 'TON' }, { amount: '10', asset: 'USDT' }], {
      usd_vnd: rate('USD', 'VND', '26000'), usd_rub: null, crypto: [rate('TON', 'USD', '5.4')],
    })
    expect(total?.toFixedString(2, true)).toBe('126.20')
  })
})
