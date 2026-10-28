// Pure helpers for rendering operations (amount text, grouping by day). Unit-tested.
import type { TransactionOut } from '@/lib/api'
import { decAdd, decNeg, decSign } from './decimal'
import { formatMoney } from './format'
import { debtTxTitle } from '@/features/debts/lib'

const FIAT = new Set(['VND', 'USD', 'RUB', 'EUR', 'GBP', 'THB', 'CNY', 'JPY', 'KRW', 'SGD'])
export const isCrypto = (cur: string) => !FIAT.has(cur.toUpperCase())

export type AmountTone = 'foreground' | 'income' | 'transfer'

export interface TxAmountView {
  main: string
  tone: AmountTone
  sub: string | null
  /** For screen readers. */
  aria: string
}

/** Secondary «≈» line: crypto → ≈ $, foreign fiat → ≈ ₫; estimated debit in another asset wins. */
function approxLine(tx: TransactionOut, negative: boolean): string | null {
  if (tx.account_asset && tx.account_amount && tx.account_asset !== tx.currency) {
    const v = negative ? decNeg(tx.account_amount) : tx.account_amount
    return formatMoney(v, tx.account_asset, { approx: tx.is_estimated })
  }
  if (tx.currency === 'VND') return null
  if (isCrypto(tx.currency) && tx.currency !== 'USD') return tx.amount_usd ? formatMoney(tx.amount_usd, 'USD', { approx: true }) : null
  return tx.amount_vnd ? formatMoney(tx.amount_vnd, 'VND', { approx: true }) : null
}

export function txAmountView(tx: TransactionOut): TxAmountView {
  switch (tx.kind) {
    case 'income':
    case 'refund': {
      const main = formatMoney(tx.amount, tx.currency, { sign: 'always' })
      return { main, tone: 'income', sub: approxLine(tx, false), aria: `${tx.kind === 'refund' ? 'Возврат' : 'Доход'} ${main}` }
    }
    case 'transfer': {
      const main = formatMoney(tx.amount, tx.currency)
      const route = `${tx.account?.name ?? '—'} → ${tx.counter_account?.name ?? '—'}`
      return { main, tone: 'transfer', sub: route, aria: `Перевод ${main}, ${route}` }
    }
    case 'exchange': {
      const got = tx.counter_amount && tx.counter_asset ? formatMoney(tx.counter_amount, tx.counter_asset) : '—'
      const main = `${formatMoney(tx.amount, tx.currency)} → ${got}`
      const route = `${tx.account?.name ?? '—'} → ${tx.counter_account?.name ?? '—'}`
      return { main, tone: 'transfer', sub: route, aria: `Обмен ${main}` }
    }
    case 'adjustment': {
      const v = tx.account_amount ?? tx.amount
      const main = formatMoney(v, tx.account_asset ?? tx.currency, { sign: 'always' })
      return { main, tone: 'foreground', sub: 'корректировка', aria: `Корректировка ${main}` }
    }
    default: {
      const main = formatMoney(decNeg(tx.amount), tx.currency)
      return { main, tone: 'foreground', sub: approxLine(tx, false), aria: `Расход ${main}` }
    }
  }
}

/** Title + category for a row (DESIGN §9.4): the title is the description / merchant / recipient
 *  («ужин с друзьями · Highlands»), otherwise the server title, otherwise the category. `category` is
 *  always the category name for regular operations (its own column); show it under the title only
 *  when it differs (`categoryDistinct`). */
export function txTitle(tx: TransactionOut): { title: string; category: string | null; categoryDistinct: boolean } {
  const debt = debtTxTitle(tx)
  if (debt) {
    // «Дал в долг: Коля» (a transfer — no category); forgiving is an expense/income with its category
    const cat = tx.category?.name ?? null
    return { title: debt, category: cat, categoryDistinct: !!cat }
  }
  if (tx.kind === 'transfer') return { title: tx.description || 'Перевод', category: null, categoryDistinct: false }
  if (tx.kind === 'exchange') return { title: tx.description || 'Обмен', category: null, categoryDistinct: false }
  if (tx.kind === 'adjustment') return { title: tx.description || 'Сверка остатка', category: null, categoryDistinct: false }
  const cat = tx.category?.name ?? null
  const seen = new Set<string>(cat ? [cat.toLowerCase()] : [])
  const parts: string[] = []
  for (const raw of [tx.description, tx.merchant?.name, tx.counterparty]) {
    const v = raw?.trim()
    if (!v || seen.has(v.toLowerCase())) continue
    seen.add(v.toLowerCase())
    parts.push(v)
  }
  const title = parts.slice(0, 2).join(' · ') || (tx.title && tx.title !== cat ? tx.title : '') || cat || 'Операция'
  return { title, category: cat, categoryDistinct: !!cat && title !== cat }
}

export interface DayGroup {
  date: string
  items: TransactionOut[]
  /** Day totals in ₫: «−610 000 ₫» (expense − refunds), «+25 000 000 ₫» (income), both when both exist. */
  total: string | null
}

/** Signed parts, never one number standing for two meanings: «−610 000 ₫ · +25 000 000 ₫». */
export function dayTotal(items: TransactionOut[]): string | null {
  let exp = '0'
  let inc = '0'
  for (const t of items) {
    if (!t.amount_vnd) continue
    if (t.kind === 'expense') exp = decAdd(exp, t.amount_vnd)
    else if (t.kind === 'refund') exp = decAdd(exp, decNeg(t.amount_vnd))
    else if (t.kind === 'income') inc = decAdd(inc, t.amount_vnd)
  }
  const parts: string[] = []
  if (decSign(exp) !== 0) parts.push(formatMoney(decNeg(exp), 'VND', { sign: 'always' }))
  if (decSign(inc) !== 0) parts.push(formatMoney(inc, 'VND', { sign: 'always' }))
  return parts.length ? parts.join(' · ') : null
}

/** Group consecutive items by `occurred_on` preserving server order. */
export function groupByDay(items: TransactionOut[]): DayGroup[] {
  const out: DayGroup[] = []
  for (const t of items) {
    const last = out[out.length - 1]
    if (last && last.date === t.occurred_on) last.items.push(t)
    else out.push({ date: t.occurred_on, items: [t], total: null })
  }
  for (const g of out) g.total = dayTotal(g.items)
  return out
}

export type FlatRow = { type: 'day'; key: string; date: string; total: string | null } | { type: 'tx'; key: string; tx: TransactionOut }

export function flattenGroups(groups: DayGroup[]): FlatRow[] {
  const rows: FlatRow[] = []
  for (const g of groups) {
    rows.push({ type: 'day', key: `d-${g.date}`, date: g.date, total: g.total })
    for (const tx of g.items) rows.push({ type: 'tx', key: `t-${tx.id}`, tx })
  }
  return rows
}

/** «42 операции · −8 350 000 ₫ · ≈ $319.40» parts from page totals. */
export function totalsLine(t: { expense_vnd: string; income_vnd: string; expense_usd: string; income_usd: string; count: number }): string[] {
  const parts: string[] = []
  if (decSign(t.expense_vnd) !== 0) parts.push(formatMoney(decNeg(t.expense_vnd), 'VND'))
  if (decSign(t.income_vnd) !== 0) parts.push(formatMoney(t.income_vnd, 'VND', { sign: 'always' }))
  if (decSign(t.expense_usd) !== 0) parts.push(`расход ${formatMoney(t.expense_usd, 'USD', { approx: true })}`)
  return parts
}
