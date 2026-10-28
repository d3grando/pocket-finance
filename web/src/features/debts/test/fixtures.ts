// Contract-shaped debt fixtures (docs/API.md §15) for vitest.
import type { DebtBalanceOut, DebtDetailOut, DebtOut, DebtsOut, TransactionOut } from '@/lib/api/types'
import { tx } from '@/features/operations/test/fixtures'

export const bal = (currency: string, amount: string, approx_usd: string | null, open_since: string | null = '2026-09-27', approx_vnd: string | null = null): DebtBalanceOut => ({
  currency, amount, direction: amount.startsWith('-') ? 'i_owe' : 'owed_to_me', approx_usd, approx_vnd, open_since,
})

export function person(p: Partial<DebtOut> & { id: number; name: string }): DebtOut {
  return {
    handle: null, aliases: [], note: null, account_id: 40 + p.id, is_archived: false, status: 'owed_to_me', balances: [],
    last_activity_on: '2026-10-20', remind_snoozed_until: null, version: 2, created_at: '2026-09-27T10:00:00+07:00', ...p,
  }
}

export const kolya = person({ id: 5, name: 'Коля', aliases: ['коле', 'коли'], balances: [bal('VND', '300000', '11.48', '2026-09-27', '300000')] })
export const vasya = person({ id: 6, name: 'Вася', handle: 'vasya', balances: [bal('USDT', '50', '50', '2026-10-20')] })
export const petya = person({ id: 7, name: 'Петя', status: 'i_owe', balances: [bal('VND', '-1000000', '-38.26', '2026-10-25', '-1000000')] })
/** owes me ₫ and I owe him USDT: in both lists, each with its own balances */
export const misha = person({ id: 8, name: 'Миша', status: 'mixed', balances: [bal('VND', '200000', '7.65'), bal('USDT', '-20', '-20', '2026-10-26')] })
export const lena = person({ id: 9, name: 'Лена', status: 'settled', balances: [] })

export const emptyDebts: DebtsOut = {
  owed_to_me: [], i_owe: [], settled: [],
  totals: { owed_to_me_vnd: '0', owed_to_me_usd: '0', i_owe_vnd: '0', i_owe_usd: '0', owed_to_me_count: 0, i_owe_count: 0, missing_rates: false },
}

export const fullDebts: DebtsOut = {
  owed_to_me: [kolya, vasya, { ...misha, balances: [misha.balances[0]] }],
  i_owe: [petya, { ...misha, balances: [misha.balances[1]] }],
  settled: [],
  totals: { owed_to_me_vnd: '1807000', owed_to_me_usd: '69.13', i_owe_vnd: '1523000', i_owe_usd: '58.26', owed_to_me_count: 3, i_owe_count: 2, missing_rates: false },
}

export const onlyOwedToMe: DebtsOut = {
  ...emptyDebts,
  owed_to_me: [kolya],
  totals: { ...emptyDebts.totals, owed_to_me_vnd: '300000', owed_to_me_usd: '11.48', owed_to_me_count: 1 },
}

export function debtTx(p: Partial<TransactionOut> & { op: NonNullable<TransactionOut['debt']>['op']; debtId?: number; name?: string }): TransactionOut {
  const { op, debtId = 5, name = 'Коля', ...rest } = p
  const debtAcc = { id: 40 + debtId, name: `Долг: ${name}`, icon: 'HandCoins' }
  const bank = { id: 1, name: 'Карта', icon: 'Landmark' }
  const inflow = op === 'repay_in' || op === 'borrow'
  const forgive = op === 'forgive'
  return tx({
    kind: forgive ? 'expense' : 'transfer',
    amount: '500000',
    title: name,
    counterparty: name,
    category: forgive ? { id: 13, name: 'Переводы людям', kind: 'expense', icon: 'Send', color: null, is_business: false, is_new: false } : null,
    account: forgive ? debtAcc : inflow ? debtAcc : bank,
    counter_account: forgive ? null : inflow ? bank : debtAcc,
    counter_amount: forgive ? null : '500000',
    counter_asset: forgive ? null : 'VND',
    debt: { id: debtId, name, op },
    ...rest,
  })
}

export const kolyaDetail: DebtDetailOut = {
  debt: kolya,
  total: 2,
  limit: 50,
  offset: 0,
  history: [
    { transaction: debtTx({ id: 902, op: 'repay_in', amount: '200000', occurred_on: '2026-10-20', created_action_id: 'act-902' }), op: 'repay_in', currency: 'VND', delta: '-200000', balance_after: '300000' },
    { transaction: debtTx({ id: 901, op: 'lend', amount: '500000', occurred_on: '2026-09-27', created_action_id: 'act-901' }), op: 'lend', currency: 'VND', delta: '500000', balance_after: '500000' },
  ],
}
