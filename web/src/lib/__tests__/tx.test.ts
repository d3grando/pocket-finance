import { describe, expect, it } from 'vitest'
import { tx } from '@/test/fixtures'
import { txAmount, txApprox, txTitle } from '../tx'

const N = (s: string) => s.replace(/ /g, ' ')

describe('transaction row presentation (DESIGN §1.3, §9.4)', () => {
  it('expense: «−», neutral colour (not red)', () => {
    expect(txAmount(tx())).toEqual({ text: N('−250 000 ₫'), tone: 'neutral' })
  })
  it('income: «+», income colour', () => {
    expect(txAmount(tx({ kind: 'income', amount: '300', currency: 'USDT' }))).toEqual({ text: N('+300 USDT'), tone: 'income' })
  })
  it('exchange: «100 USDT → 99.8 USDT»', () => {
    expect(txAmount(tx({ kind: 'exchange', amount: '100', currency: 'USDT', counter_amount: '99.8', counter_asset: 'USDT' })).text).toBe(`${N('100 USDT')} → ${N('99.80 USDT')}`)
  })
  it('≈ in ₫ only for foreign currency', () => {
    expect(txApprox(tx())).toBeNull()
    expect(txApprox(tx({ amount: '180', currency: 'USD', amount_vnd: '4705200' }))).toBe(N('≈ −4 705 200 ₫'))
  })
  it('title falls back to category', () => {
    expect(txTitle(tx({ title: '', description: null }))).toBe('Еда')
  })
})
