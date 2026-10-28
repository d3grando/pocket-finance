import { describe, expect, it } from 'vitest'
import { tx } from '@/test/fixtures'
import { buildPatch } from '../TxEditForm'

const base = (t = tx()) => ({ amount: t.amount, currency: t.currency, categoryId: String(t.category!.id), accountId: String(t.account!.id), date: t.occurred_on, description: '' })

describe('inline edit → PATCH body (only changed fields + version)', () => {
  it('nothing changed → only version', () => {
    expect(buildPatch(tx(), base())).toEqual({ version: 1 })
  })
  it('«нет, 300к»', () => {
    expect(buildPatch(tx(), { ...base(), amount: '300к' })).toEqual({ version: 1, amount: '300000' })
  })
  it('untouched small VND amount is not multiplied by 1000', () => {
    const t = tx({ amount: '500' })
    expect(buildPatch(t, base(t))).toEqual({ version: 1 })
  })
  it('category, account «Без счёта», date and description', () => {
    expect(buildPatch(tx({ version: 4 }), { ...base(), categoryId: '3', accountId: 'none', date: '2026-10-26', description: 'обед' }))
      .toEqual({ version: 4, category_id: 3, account_none: true, account_id: null, occurred_on: '2026-10-26', description: 'обед' })
  })
  it('bad amount → error, nothing sent', () => {
    expect(buildPatch(tx(), { ...base(), amount: 'много' })).toEqual({ error: 'Не понял сумму' })
    expect(buildPatch(tx(), { ...base(), amount: '0' })).toEqual({ error: 'Не понял сумму' })
  })
})
