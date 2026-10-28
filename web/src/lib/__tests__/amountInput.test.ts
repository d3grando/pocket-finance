import { describe, expect, it } from 'vitest'
import { parseAmountInput, parseAssetList } from '../amountInput'

describe('parseAmountInput', () => {
  it.each([
    ['34 120 000', 'VND', '34120000', null],
    ['2.5м', 'VND', '2500000', null],
    ['800к', 'VND', '800000', null],
    ['1,5 млн', 'VND', '1500000', null],
    ['85', 'VND', '85000', null], // «кофе 85» → 85 000 ₫
    ['85', 'USD', '85', null],
    ['15$', 'VND', '15', 'USD'],
    ['$15.5', null, '15.5', 'USD'],
    ['312.4 USDT', null, '312.4', 'USDT'],
    ['3 тона', null, '3', 'TON'],
    ['1,500,000', 'VND', '1500000', null],
    ['4990.50', 'RUB', '4990.5', null],
    ['21.3m', 'VND', '21300000', null],
  ])('%s (%s) → %s %s', (raw, cur, amount, asset) => {
    expect(parseAmountInput(raw, cur)).toEqual({ amount, asset })
  })
  it('respects small_amount_thousands=false and decimals', () => {
    expect(parseAmountInput('85', 'VND', false)?.amount).toBe('85')
    expect(parseAmountInput('0.5', 'VND')?.amount).toBe('0.5')
  })
  it('rejects garbage', () => {
    expect(parseAmountInput('', 'VND')).toBeNull()
    expect(parseAmountInput('кофе', 'VND')).toBeNull()
    expect(parseAmountInput('12 абвгд', 'VND')).toBeNull()
  })
})

describe('parseAssetList', () => {
  it('multi-asset wallet input', () => {
    expect(parseAssetList('312.4 USDT, 3 TON')).toEqual([{ amount: '312.4', asset: 'USDT' }, { amount: '3', asset: 'TON' }])
    expect(parseAssetList('10 usdt и 0.5 btc')).toEqual([{ amount: '10', asset: 'USDT' }, { amount: '0.5', asset: 'BTC' }])
  })
  it('needs an asset for every amount', () => {
    expect(parseAssetList('312.4, 3 TON')).toBeNull()
  })
})
