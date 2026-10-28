// Regression tests (UI helpers): Quick Capture window sizing, command palette «Записать»,
// KPI sparklines, theme-color, PWA update safety, card redraw helpers.
import { describe, expect, it } from 'vitest'
import { cardTransactions, withTransaction, withoutTransaction } from '@/features/assistant/logic'
import { kpiSparks } from '@/features/dashboard/sparks'
import { looksLikeOperation } from '@/features/shell/palette'
import { safeToReload } from '@/lib/pwa'
import { THEME_COLOR, applyTheme } from '@/lib/theme'
import { quickWindowHeight, recentRowsThatFit } from '@/pages/quick/sizing'
import { createdCard, needsAccountCard, tx } from '@/test/fixtures'

describe('Quick Capture window', () => {
  it('fits «Последние» rows into 450 and only grows by what still does not fit', () => {
    // idle: header+AI line+KPIs ≈ 200 → all 4 rows
    expect(recentRowsThatFit(200, 32, 40)).toBe(4)
    // a result card (+100) → fewer rows, the window stays 450
    expect(recentRowsThatFit(300, 32, 40)).toBe(2)
    expect(quickWindowHeight(300 + 32 + 2 * 40)).toBe(450)
    // account choice: nothing fits → no rows, the window grows to the content
    expect(recentRowsThatFit(440, 32, 40)).toBe(0)
    expect(quickWindowHeight(440)).toBe(472)
  })
  it('shrinks back after the card is gone and never exceeds 640', () => {
    expect(quickWindowHeight(380)).toBe(450)
    expect(quickWindowHeight(900)).toBe(640)
  })
})

describe('command palette (DESIGN §9.7)', () => {
  it('offers «Записать: …» only for operation-like text', () => {
    expect(looksLikeOperation('кофе 23к')).toBe(true)
    expect(looksLikeOperation('15$ подписка')).toBe(true)
    expect(looksLikeOperation('аналитика')).toBe(false)
    expect(looksLikeOperation('42')).toBe(false)
  })
})

describe('KPI sparklines on every desktop tile (DESIGN §9.3)', () => {
  it('builds series for today (7 days), month, income and net', () => {
    const month = [{ expense: '1', income: '5', net: '4' }, { expense: '2', income: '0', net: '-2' }]
    const week = [{ expense: '3' }, { expense: '4' }]
    expect(kpiSparks(month, week)).toEqual({ today: ['3', '4'], month: ['1', '2'], income: ['5', '0'], net: ['4', '-2'] })
  })
})

describe('theme-color follows the chosen theme', () => {
  it('a manual switch recolours every theme-color meta', () => {
    document.head.innerHTML = '<meta name="theme-color" media="(prefers-color-scheme: light)" content="#5d4ed6"><meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0d0e12">'
    applyTheme('dark')
    const metas = [...document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')].map((m) => m.content)
    expect(metas).toEqual([THEME_COLOR.dark, THEME_COLOR.dark])
    applyTheme('light')
    expect([...document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')].map((m) => m.content)).toEqual([THEME_COLOR.light, THEME_COLOR.light])
  })
})

describe('PWA update never wipes a draft', () => {
  it('is not safe to reload with text in the AI line or a dialog open', () => {
    document.body.innerHTML = '<textarea data-testid="ai-input"></textarea>'
    expect(safeToReload()).toBe(true)
    document.querySelector('textarea')!.value = 'такси до аэропорта 250к'
    expect(safeToReload()).toBe(false)
    document.body.innerHTML = '<textarea data-testid="ai-input"></textarea><div role="dialog"></div>'
    expect(safeToReload()).toBe(false)
  })
})

describe('card redraw from the server state', () => {
  it('created: new amount in the title; needs_account → created once an account is chosen elsewhere', () => {
    const c = withTransaction(createdCard(), tx({ amount: '251000', version: 2 }))
    expect(c.title.replace(/\s/g, ' ')).toBe('251 000 ₫ · Еда')
    expect(cardTransactions(c)[0].version).toBe(2)
    const na = needsAccountCard()
    const t = cardTransactions(na)[0]
    expect(withTransaction(na, { ...t, version: 2 }).kind).toBe('needs_account')
    expect(withTransaction(na, { ...t, version: 2, status: 'posted', account: { id: 3, name: 'Кошелёк', icon: null } }).kind).toBe('created')
  })
  it('deleted elsewhere: a single card has nothing left', () => {
    expect(withoutTransaction(createdCard(), 123)).toBeNull()
    expect(withoutTransaction(createdCard(), 999)).not.toBeNull()
  })
})
