import { describe, expect, it } from 'vitest'
import { accounts } from '@/features/operations/test/fixtures'
import { txTitle as opsTxTitle } from '@/features/operations/lib/tx'
import { txAccountLabel, txTitle } from '@/lib/tx'
import { cardFromTransaction, txCardSubtitle, txCardTitle, withTransaction } from '@/features/assistant/logic'
import type { Card } from '@/lib/api/types'
import {
  accountQuestion, accountsFor, ageLabel, allPeople, balanceDirectionText, balancesApproxUsd, balancesText, debtAgeDays, debtBriefLine,
  changesNotInSubtitle, debtRealAccount, debtTxTitle, excessText, hasOpenDebts, historyLabel, latestLiveAction, matchPeople, parseReminderDays,
  repayOpFor, serverBalanceText, sideBalances, sideTotal, subtitleHasDebtLine, titleLinksDebt,
} from './lib'
import type { ActionOut, AuditEntryOut, DebtBrief } from '@/lib/api/types'
import { bal, debtTx, emptyDebts, fullDebts, kolya, misha, petya, vasya } from './test/fixtures'

/** money is grouped with U+00A0 (DESIGN §3) — compare with plain spaces */
const N = <T,>(v: T): T => (typeof v === 'string' ? (v.replace(/\u00a0/g, ' ') as T) : v && typeof v === 'object' ? (Object.fromEntries(Object.entries(v).map(([k, x]) => [k, N(x)])) as T) : v)

describe('debt texts', () => {
  it('balances per currency, never converted; ≈ $ only when something is not in $', () => {
    expect(N(balancesText([bal('VND', '300000', '11.48'), bal('USDT', '50', '50')]))).toBe('300 000 ₫ · 50 USDT')
    expect(N(balancesText([bal('VND', '-1000000', '-38.26')]))).toBe('1 000 000 ₫') // the side says who owes
    expect(N(balancesApproxUsd([bal('VND', '300000', '11.48'), bal('USDT', '50', '50')]))).toBe('≈ $61.48')
    expect(N(balancesApproxUsd([bal('USD', '20', '20')]))).toBeNull()
    expect(N(balancesApproxUsd([bal('TON', '3', null)]))).toBeNull() // no rate → no fake number
  })

  it('age of the oldest open balance', () => {
    expect(debtAgeDays([bal('VND', '1', null, '2026-09-27'), bal('USDT', '1', null, '2026-10-20')], '2026-10-27')).toBe(30)
    expect(ageLabel(30)).toBe('30 дн.')
    expect(ageLabel(0)).toBe('сегодня')
    expect(ageLabel(debtAgeDays([bal('VND', '1', null, null)], '2026-10-27'))).toBeNull()
  })

  it('card line «Коля: долг тебе 300 000 ₫», my debt, closed; overpayment', () => {
    expect(N(debtBriefLine({ debt_id: 5, name: 'Коля', op: 'repay_in', currency: 'VND', balance_after: '300000', excess: null }))).toBe('Коля: долг тебе 300 000 ₫')
    expect(N(debtBriefLine({ debt_id: 7, name: 'Петя', op: 'borrow', currency: 'VND', balance_after: '-1000000', excess: null }))).toBe('Твой долг · Петя: 1 000 000 ₫')
    expect(N(debtBriefLine({ debt_id: 5, name: 'Коля', op: 'repay_in', currency: 'VND', balance_after: '0', excess: null }))).toBe('Коля · долг закрыт')
    const over = { debt_id: 5, name: 'Коля', op: 'repay_in' as const, currency: 'VND', balance_after: '-100000', excess: '100000' }
    expect(N(excessText(over))).toBe('Излишек 100 000 ₫ — теперь ты должен: Коля')
    expect(N(excessText({ ...over, op: 'repay_out', balance_after: '50000', excess: '50000' }))).toBe('Излишек 50 000 ₫ — теперь это долг тебе: Коля')
    expect(N(excessText({ ...over, excess: null }))).toBeNull()
    expect(N(balanceDirectionText(bal('VND', '-1000000', null)))).toBe('ты должен 1 000 000 ₫')
    expect(N(balanceDirectionText(bal('USDT', '50', null)))).toBe('долг тебе 50 USDT')
  })

  it('history labels and repay direction', () => {
    expect(historyLabel('lend', '500000')).toBe('Дал в долг')
    expect(historyLabel('repay_in', '-200000')).toBe('Возврат')
    expect(historyLabel('forgive', '-300000')).toBe('Списан')
    expect(historyLabel('forgive', '100000')).toBe('Прощён')
    expect(repayOpFor('300000')).toBe('repay_in')
    expect(repayOpFor('-20')).toBe('repay_out')
  })

  it('home/accounts totals in the summary currency with ≈ $', () => {
    expect(N(sideTotal(fullDebts.totals, 'owed_to_me'))).toEqual({ main: '≈ 1 807 000 ₫', approx: '≈ $69.13' })
    expect(N(sideTotal(fullDebts.totals, 'i_owe', 'USD'))).toEqual({ main: '≈ $58.26', approx: null })
    expect(N(sideTotal({ ...fullDebts.totals, i_owe_vnd: null, i_owe_usd: null }, 'i_owe')).main).toBeNull()
    expect(hasOpenDebts(emptyDebts)).toBe(false)
    expect(hasOpenDebts(fullDebts)).toBe(true)
    expect(sideBalances(misha, 'i_owe').map((b) => b.currency)).toEqual(['USDT'])
    expect(allPeople(fullDebts).map((p) => p.name)).toEqual(['Коля', 'Вася', 'Миша', 'Петя'])
  })

  it('autocomplete by name, alias, @handle; ё = е', () => {
    const people = [kolya, vasya, petya]
    expect(matchPeople(people, 'ко').map((p) => p.name)).toEqual(['Коля'])
    expect(matchPeople(people, 'коле').map((p) => p.name)).toEqual(['Коля']) // alias
    expect(matchPeople(people, '@vas').map((p) => p.name)).toEqual(['Вася'])
    expect(matchPeople(people, '').length).toBe(3)
    expect(matchPeople([{ ...petya, name: 'Пётр' }], 'петр').map((p) => p.name)).toEqual(['Пётр'])
  })

  it('accounts for a debt: a coin never from a single-currency account; fiat — fitting first', () => {
    expect(accountsFor(accounts, 'USDT').map((a) => a.name)).toEqual(['Кошелёк'])
    expect(accountsFor(accounts, 'VND').map((a) => a.name)).toEqual(['Карта', 'Наличные', 'Кошелёк', 'Карта RUB'])
    expect(accountsFor([...accounts, { ...accounts[0], id: 41, name: 'Долг: Коля', kind: 'debt' }], 'VND').some((a) => a.kind === 'debt')).toBe(false)
  })

  it('settings «старше N дней»: 1..365 only', () => {
    expect(parseReminderDays('45')).toBe(45)
    expect(['', '0', '366', '3.5', 'abc'].map(parseReminderDays)).toEqual([null, null, null, null, null])
  })
})

describe('debt operations in lists and cards', () => {
  it('titles «Дал в долг: Коля»; forgive by side; the real account; «На какой счёт?» for inflows', () => {
    const lend = debtTx({ op: 'lend' })
    const back = debtTx({ op: 'repay_in', amount: '200000' })
    const forgive = debtTx({ op: 'forgive' })
    expect(debtTxTitle(lend)).toBe('Дал в долг: Коля')
    expect(debtTxTitle(back)).toBe('Возврат долга: Коля')
    expect(debtTxTitle(forgive)).toBe('Списан долг: Коля')
    expect(debtTxTitle({ ...forgive, kind: 'income' })).toBe('Долг прощён: Коля')
    expect(debtTxTitle({ ...lend, debt: null })).toBeNull()
    expect(debtRealAccount(lend)?.name).toBe('Карта')
    expect(debtRealAccount(back)?.name).toBe('Карта') // the receiver
    expect(accountQuestion(back)).toBe('На какой счёт?')
    expect(accountQuestion(lend)).toBe('С какого счёта?')
    expect(accountQuestion({ kind: 'income', debt: null })).toBe('На какой счёт?')
    // Home / Quick rows and «Операции»
    expect(txTitle(lend)).toBe('Дал в долг: Коля')
    expect(txAccountLabel(back)).toBe('Карта')
    expect(txAccountLabel(forgive)).toBe('Долг')
    expect(opsTxTitle(lend)).toEqual({ title: 'Дал в долг: Коля', category: null, categoryDistinct: false })
    expect(opsTxTitle(forgive)).toEqual({ title: 'Списан долг: Коля', category: 'Переводы людям', categoryDistinct: true })
  })

  it('client-built cards keep the debt line while it is still true (choose account), drop it after an amount edit', () => {
    const pending = debtTx({ id: 77, op: 'lend', amount: '50', currency: 'USDT', status: 'needs_account', account: null })
    const card: Card = {
      kind: 'needs_account', title: '50 USDT · Дал в долг: Вася', subtitle: 'Вася: долг тебе 50 USDT · ≈ $50.00', question: 'С какого счёта?',
      action_id: 'a-1', transaction: pending, options: [], buttons: [],
      debt: { debt_id: 6, name: 'Вася', op: 'lend', currency: 'USDT', balance_after: '50', excess: null },
    }
    expect(N(txCardTitle(pending))).toBe('50 USDT · Дал в долг: Коля')
    const chosen = { ...pending, status: 'posted' as const, account: { id: 3, name: 'Кошелёк', icon: 'Bot' }, version: 2 }
    const after = withTransaction(card, chosen)
    expect(after.kind).toBe('created')
    expect(after.kind === 'created' && after.debt?.balance_after).toBe('50')
    expect(N(txCardSubtitle(chosen, 'Asia/Ho_Chi_Minh', new Date('2026-10-27T08:00:00Z')))).toMatch(/^Кошелёк · /)
    const edited = withTransaction(after, { ...chosen, amount: '40', version: 3 })
    expect(edited.kind === 'created' && edited.debt).toBeFalsy()
    // ordinary operations are untouched
    const plain = cardFromTransaction({ ...chosen, debt: null, kind: 'expense' }, 'a-2')
    expect('debt' in plain && plain.debt).toBeFalsy()
  })
})

describe('assistant card: the debt line is not repeated', () => {
  const brief = (p: Partial<DebtBrief>): DebtBrief => ({ debt_id: 5, name: 'Коля', op: 'lend', currency: 'VND', balance_after: '400000', excess: null, ...p })
  it('server balance texts like ai/debt_cards.balance_text', () => {
    expect(N(serverBalanceText(brief({})))).toBe('Коля: долг тебе 400 000 ₫')
    expect(N(serverBalanceText(brief({ op: 'borrow', balance_after: '-1000000' })))).toBe('ты должен 1 000 000 ₫')
    expect(N(serverBalanceText(brief({ op: 'repay_in', balance_after: '300000' })))).toBe('осталось 300 000 ₫')
    expect(serverBalanceText(brief({ op: 'repay_in', balance_after: '-100000' }))).toBe('долг закрыт')
    expect(N(serverBalanceText(brief({ op: 'repay_out', balance_after: '-500000' })))).toBe('осталось 500 000 ₫')
    expect(serverBalanceText(brief({ op: 'forgive', balance_after: '0' }))).toBe('долг закрыт')
  })
  it('subtitle with the balance (any spaces) → no line; without it (older server) → line', () => {
    expect(subtitleHasDebtLine('Карта · Коля: долг тебе 400\u00a0000\u00a0₫', brief({}))).toBe(true)
    expect(subtitleHasDebtLine('Изменено: 100 000 ₫ → 150 000 ₫ · Коля: долг тебе 400 000 ₫', brief({}))).toBe(true)
    expect(subtitleHasDebtLine('Маша: долг тебе $120.00 · ≈ $120.00', brief({ name: 'Маша', currency: 'USD', balance_after: '120' }))).toBe(true)
    expect(subtitleHasDebtLine('Карта · осталось 300 000 ₫', brief({ op: 'repay_in', balance_after: '300000' }))).toBe(true)
    expect(subtitleHasDebtLine('Карта', brief({}))).toBe(false)
    expect(subtitleHasDebtLine(null, brief({}))).toBe(false)
    expect(subtitleHasDebtLine('Карта · Коля: долг тебе 500 000 ₫', brief({}))).toBe(false) // a stale balance is not «the same»
  })
  it('updated: change lines already in «Изменено: …» are dropped', () => {
    expect(changesNotInSubtitle(['100 000 ₫ → 150 000 ₫'], 'Изменено: 100 000 ₫ → 150 000 ₫ · Коля: долг тебе 450 000 ₫')).toEqual([])
    expect(changesNotInSubtitle(['Еда → Кофе', '45 000 ₫ → 50 000 ₫'], 'Изменено: Еда → Кофе')).toEqual(['45 000 ₫ → 50 000 ₫'])
    expect(changesNotInSubtitle(['a → b'], null)).toEqual(['a → b'])
  })
  it('the title links the name only when it ends with «: Имя»', () => {
    expect(titleLinksDebt('500 000 ₫ · Дал в долг: Коля', { name: 'Коля' })).toBe(true)
    expect(titleLinksDebt('500 000 ₫ · Переводы людям', { name: 'Коля' })).toBe(false)
  })
})

describe('«Отменить последнюю»: the newest action still in effect', () => {
  const audit = (id: number, action_id: string | null): AuditEntryOut => ({ id, entity_type: 'transaction', entity_id: '9', operation: 'update', before: null, after: null, action_id, source: 'web', created_at: '2026-10-27T10:00:00Z' })
  const act = (id: string, p: Partial<ActionOut> = {}): ActionOut => ({ id, kind: 'update_transaction', summary: '', source: 'web', input_text: null, entity_refs: [], is_undoable: true, undone_at: null, created_at: '2026-10-27T10:00:00Z', ...p })
  it('edited twice → the last edit (the creation is skipped, it already answered 409)', () => {
    expect(latestLiveAction([audit(1, 'c'), audit(2, 'e1'), audit(3, 'e2')], [act('e1'), act('e2')], 'c')).toBe('e2')
  })
  it('order by audit id, not by array order; missing from the action page = live', () => {
    expect(latestLiveAction([audit(3, 'e2'), audit(1, 'c'), audit(2, 'e1')], [], 'c')).toBe('e2')
  })
  it('the last edit was undone → the edit before it, never the undo itself', () => {
    const a = [act('e1'), act('e2', { undone_at: '2026-10-27T11:00:00Z' }), act('u', { kind: 'undo' })]
    expect(latestLiveAction([audit(1, 'c'), audit(2, 'e1'), audit(3, 'e2'), audit(4, 'u')], a, 'c')).toBe('e1')
  })
  it('undone and restored (no live non-undo action) → the restore is undone', () => {
    const a = [act('c', { kind: 'debt', undone_at: 'x' }), act('u1', { kind: 'undo', undone_at: 'y' }), act('u2', { kind: 'undo' })]
    expect(latestLiveAction([audit(1, 'c'), audit(2, 'u1'), audit(3, 'u2')], a, 'c')).toBe('u2')
  })
  it('nothing → null', () => {
    expect(latestLiveAction([audit(1, 'c'), audit(2, null)], [], 'c')).toBeNull()
  })
})
