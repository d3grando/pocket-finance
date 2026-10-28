// Presentation helpers for TransactionOut (DESIGN §1.3, §9.4). Display only — no math
// on money beyond sign flips (string level).
import { formatMoney } from './format'
import type { TransactionOut } from './api/types'
import { debtRealAccount, debtTxTitle } from '@/features/debts/lib'

export type Tone = 'expense' | 'income' | 'transfer' | 'neutral'

const neg = (s: string) => (s.startsWith('-') ? s.slice(1) : `-${s}`)

/** Main amount text of a row: «−45 000 ₫», «+300 USDT», «3 TON», «100 USDT → 99.8 USDT». */
export function txAmount(tx: TransactionOut): { text: string; tone: Tone } {
  switch (tx.kind) {
    case 'expense':
      return { text: formatMoney(neg(tx.amount), tx.currency), tone: 'neutral' }
    case 'income':
    case 'refund':
      return { text: formatMoney(tx.amount, tx.currency, { sign: 'always' }), tone: 'income' }
    case 'transfer':
      return { text: formatMoney(tx.amount, tx.currency), tone: 'transfer' }
    case 'exchange':
      return {
        text: `${formatMoney(tx.amount, tx.currency)} → ${tx.counter_amount ? formatMoney(tx.counter_amount, tx.counter_asset ?? tx.currency) : '—'}`,
        tone: 'transfer',
      }
    case 'adjustment': {
      const v = tx.account_amount ?? tx.amount
      return { text: formatMoney(v, tx.account_asset ?? tx.currency, { sign: 'always' }), tone: 'neutral' }
    }
  }
}

/** Secondary «≈ −392 100 ₫» when the operation is not in ₫ (summary currency). */
export function txApprox(tx: TransactionOut, base: 'VND' | 'USD' = 'VND'): string | null {
  const v = base === 'VND' ? tx.amount_vnd : tx.amount_usd
  if (!v || tx.currency === base) return null
  if (tx.kind === 'transfer' || tx.kind === 'exchange' || tx.kind === 'adjustment') return null
  const signed = tx.kind === 'expense' ? neg(v) : v
  return formatMoney(signed, base, { approx: true, sign: tx.kind === 'expense' ? 'auto' : 'always' })
}

/** Row title: description or merchant, else category (DESIGN §9.4). Backend `title` wins when present. */
export function txTitle(tx: TransactionOut): string {
  const debt = debtTxTitle(tx)
  if (debt) return debt
  if (tx.kind === 'transfer') return `Перевод ${tx.account?.name ?? ''} → ${tx.counter_account?.name ?? ''}`.trim()
  if (tx.kind === 'exchange') return `Обмен ${tx.account?.name ?? ''} → ${tx.counter_account?.name ?? ''}`.trim()
  if (tx.kind === 'adjustment') return `Сверка ${tx.account?.name ?? ''}`.trim()
  return tx.title || tx.description || tx.merchant?.name || tx.category?.name || 'Операция'
}

export function txAccountLabel(tx: TransactionOut): string | null {
  if (tx.status === 'needs_account') return null
  if (tx.debt) return tx.debt.op === 'forgive' ? 'Долг' : (debtRealAccount(tx)?.name ?? 'Без счёта')
  if (tx.counter_account) return `${tx.account?.name ?? '—'} → ${tx.counter_account.name}`
  return tx.account?.name ?? 'Без счёта'
}
