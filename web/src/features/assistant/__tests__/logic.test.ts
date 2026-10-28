import { describe, expect, it } from 'vitest'
import { createdCard, needsAccountCard, tx } from '@/test/fixtures'
import {
  KeyBook, accountButtonForDigit, cardActionIds, cardFromTransaction, cardHotkey, keepsInput, localErrorCard, undoneCard,
} from '../logic'

describe('KeyBook — one Idempotency-Key per attempt', () => {
  it('reuses the key after a failed attempt (retry after network loss)', () => {
    const b = new KeyBook()
    let n = 0
    const gen = () => `k${++n}`
    const first = b.acquire('еда 250к', gen)
    expect(first).toEqual({ key: 'k1', busy: false })
    b.failed('еда 250к')
    expect(b.acquire('  еда   250к ', gen)).toEqual({ key: 'k1', busy: false }) // same text, same key
  })
  it('ignores a second Enter while the request is in flight', () => {
    const b = new KeyBook()
    b.acquire('кофе 85к', () => 'k1')
    expect(b.acquire('кофе 85к', () => 'k2')).toEqual({ key: 'k1', busy: true })
  })
  it('a new text gets its own key; after 2xx the same text is a new operation', () => {
    const b = new KeyBook()
    let n = 0
    const gen = () => `k${++n}`
    b.acquire('кофе 85к', gen)
    expect(b.acquire('такси 70к', gen).key).toBe('k2')
    b.done('кофе 85к')
    expect(b.acquire('кофе 85к', gen).key).toBe('k3')
  })
})

describe('card helpers', () => {
  it('undo chain of each card kind', () => {
    expect(cardActionIds(createdCard())).toEqual(['a-1'])
    expect(cardActionIds(needsAccountCard())).toEqual(['a-2'])
    expect(cardActionIds({ kind: 'created_many', title: '', subtitle: null, buttons: [], action_ids: ['x', 'y'], transactions: [], total: null, total_currency: null })).toEqual(['x', 'y'])
  })
  it('unclear / error keep the text in the field', () => {
    expect(keepsInput([createdCard()])).toBe(false)
    expect(keepsInput([localErrorCard('network')])).toBe(true)
  })
  it('client-built created card after choose_account (variant a)', () => {
    const c = cardFromTransaction(tx({ amount: '15', currency: 'USD', amount_usd: '15', account: { id: 1, name: 'Карта', icon: null }, is_estimated: true, account_amount: '392100', account_asset: 'VND', title: 'Подписки', category: { id: 7, name: 'Подписки', kind: 'expense', icon: null, color: null, is_business: false, is_new: false } }), 'a-9')
    expect(c.kind).toBe('created')
    expect(c.title).toBe('$15.00 · Подписки')
    expect(c.subtitle).toContain('Карта')
    expect(c.subtitle).toContain('≈ −392 100 ₫')
    expect(c.buttons.map((b) => b.action.kind)).toEqual(['undo', 'edit'])
  })
  it('new category keeps 🆕 and «В другую»', () => {
    const c = cardFromTransaction(tx({ category: { id: 99, name: 'Софт и AI', kind: 'expense', icon: null, color: null, is_business: true, is_new: true } }), 'a')
    expect(c.title).toContain('🆕')
    expect(c.buttons[0].action.kind).toBe('change_category')
  })
  it('income gets a plus sign', () => {
    const c = cardFromTransaction(tx({ kind: 'income', amount: '300', currency: 'USDT', category: { id: 20, name: 'Клиенты', kind: 'income', icon: null, color: null, is_business: true, is_new: false } }), 'a')
    expect(c.title).toBe('+300 USDT · Клиенты')
  })
  it('undone card offers redo with the undo action id', () => {
    const c = undoneCard({ status: 'undone', undone_action_id: 'a-1', undo_action_id: 'u-1', summary: 'Отменено: 150 000 ₫ · Еда', changed: [] })
    expect(c.title).toBe('Отменено: 150 000 ₫ · Еда')
    expect(c.buttons[0].action).toEqual({ kind: 'redo', action_id: 'u-1' })
  })
})

describe('keyboard', () => {
  const ev = (key: string, mods: Partial<{ ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey: boolean }> = {}) =>
    ({ key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods })
  it('Ctrl+Z / Ctrl+E / digits only when the field is empty', () => {
    expect(cardHotkey(ev('z', { ctrlKey: true }), true)).toBe('undo')
    expect(cardHotkey(ev('я', { ctrlKey: true }), true)).toBe('undo') // RU layout
    expect(cardHotkey(ev('e', { metaKey: true }), true)).toBe('edit')
    expect(cardHotkey(ev('2'), true)).toEqual({ digit: '2' })
    expect(cardHotkey(ev('z', { ctrlKey: true }), false)).toBeNull()
    expect(cardHotkey(ev('2'), false)).toBeNull()
  })
  it('digit → account button by hotkey, then by position', () => {
    const c = needsAccountCard()
    expect(accountButtonForDigit(c, '2')?.id).toBe('acc-4')
    expect(accountButtonForDigit(c, '0')?.id).toBe('acc-none')
    expect(accountButtonForDigit(createdCard(), '1')).toBeUndefined()
  })
})
