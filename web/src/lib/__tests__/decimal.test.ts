import { describe, expect, it } from 'vitest'
import { Dec, parseDecimal, sumDec } from '../decimal'

describe('Dec (BigInt fixed point)', () => {
  it('parses and prints canonical strings', () => {
    expect(Dec.from('1250000').toString()).toBe('1250000')
    expect(Dec.from('0.02150000').toString()).toBe('0.0215')
    expect(Dec.from('-3.5').toString()).toBe('-3.5')
    expect(Dec.from('−5').toString()).toBe('-5')
    expect(parseDecimal('1,5')).toBeNull()
    expect(parseDecimal('abc')).toBeNull()
    expect(parseDecimal('.5')?.toString()).toBe('0.5')
  })
  it('exact arithmetic (0.1 + 0.2 = 0.3)', () => {
    expect(Dec.from('0.1').add('0.2').toString()).toBe('0.3')
    expect(Dec.from('2.5').mul('1000000').toString()).toBe('2500000')
    expect(Dec.from('100').div('3').toFixedString(4)).toBe('33.3333')
    expect(Dec.from('5200000').sub('5320000').toString()).toBe('-120000')
  })
  it('rounds half-even', () => {
    expect(Dec.from('2.5').toFixedString(0)).toBe('2')
    expect(Dec.from('3.5').toFixedString(0)).toBe('4')
    expect(Dec.from('-2.5').toFixedString(0)).toBe('-2')
    expect(Dec.from('1.005').toFixedString(2, true)).toBe('1.00')
    expect(Dec.from('1.015').toFixedString(2, true)).toBe('1.02')
  })
  it('compares and sums', () => {
    expect(Dec.from('10').cmp('9.99')).toBe(1)
    expect(Dec.from('-1').isNeg()).toBe(true)
    expect(sumDec(['1.1', null, '2.2', undefined]).toString()).toBe('3.3')
  })
})
