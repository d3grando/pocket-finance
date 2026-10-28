// Pure assistant logic (unit-tested): idempotency-key bookkeeping, card helpers,
// keyboard mapping. No React here.
import { newIdempotencyKey } from '@/lib/api/client'
import type { Button, Card, CreatedCard, TransactionOut, UndoResult } from '@/lib/api/types'
import { DEFAULT_TZ, formatMoney, formatRelative } from '@/lib/format'
import { debtRealAccount, debtTxTitle } from '@/features/debts/lib'

/**
 * One Idempotency-Key per user attempt (DESIGN §9.1, ARCH §5.2):
 * - generated at the first Enter for a text and reused for retries ([Повторить], auto-retry, 409 in progress);
 * - a second Enter with the same text while its request is in flight is ignored;
 * - after a 2xx the key is forgotten — sending the same text later is a new operation.
 */
export class KeyBook {
  private keys = new Map<string, { key: string; inFlight: boolean }>()

  static norm(text: string) {
    return text.trim().replace(/\s+/g, ' ')
  }

  /** Key for this text; `busy` = a request with this text is already running (ignore the Enter). */
  acquire(text: string, gen: () => string = newIdempotencyKey): { key: string; busy: boolean } {
    const t = KeyBook.norm(text)
    const cur = this.keys.get(t)
    if (cur?.inFlight) return { key: cur.key, busy: true }
    const key = cur?.key ?? gen()
    this.keys.set(t, { key, inFlight: true })
    return { key, busy: false }
  }

  /** Request finished without reaching a final answer (network/5xx/409 in progress) — keep the key. */
  failed(text: string) {
    const cur = this.keys.get(KeyBook.norm(text))
    if (cur) cur.inFlight = false
  }

  /** Server answered 2xx (or a final 4xx) — the attempt is over. */
  done(text: string) {
    this.keys.delete(KeyBook.norm(text))
  }

  peek(text: string) {
    return this.keys.get(KeyBook.norm(text))?.key ?? null
  }

  /** Drop a kept key (next send of this text = a new attempt); a request in flight keeps its key. */
  forget(text: string) {
    const t = KeyBook.norm(text)
    if (!this.keys.get(t)?.inFlight) this.keys.delete(t)
  }
}

/** Cards that end the attempt but keep the text in the field (DESIGN §9.2 g/h). */
export function keepsInput(cards: Card[]): boolean {
  return cards.some((c) => (c.kind === 'unclear' || c.kind === 'error') && c.keep_input !== false)
}

/** Kinds that collapse after 30 s without interaction. */
export const EPHEMERAL = new Set<Card['kind']>(['created', 'created_many', 'updated', 'deleted', 'applied', 'undone', 'category', 'balance'])

export function undoButton(c: Card): Button | undefined {
  return c.buttons.find((b) => b.action.kind === 'undo')
}

export function editButton(c: Card): Button | undefined {
  return c.buttons.find((b) => b.action.kind === 'edit')
}

/** Action ids a card's «Отменить» reverts (created_many: all of them). */
export function cardActionIds(c: Card): string[] {
  switch (c.kind) {
    case 'created_many':
      return c.action_ids
    case 'created': case 'needs_account': case 'updated': case 'deleted': case 'applied': case 'category':
      return [c.action_id]
    case 'balance':
      return c.action_id ? [c.action_id] : []
    default: {
      const b = undoButton(c)
      return b?.action.action_id ? [b.action.action_id] : []
    }
  }
}

/** Card title for a transaction the client re-rendered after a mutation (variant a). */
export function txCardTitle(tx: TransactionOut): string {
  // debt operations: «500 000 ₫ · Дал в долг: Коля» like the server card (DEBTS.md §8)
  const debt = debtTxTitle(tx)
  if (debt) return `${formatMoney(tx.amount, tx.currency)} · ${debt}`
  const income = tx.kind === 'income' || tx.kind === 'refund'
  const money = formatMoney(tx.amount, tx.currency, { sign: income ? 'always' : 'never' })
  const what = tx.category?.name ?? (tx.kind === 'transfer' ? 'Перевод' : tx.kind === 'exchange' ? 'Обмен' : tx.title)
  return `${money} · ${what}${tx.category?.is_new ? ' 🆕' : ''}`
}

export function txCardSubtitle(tx: TransactionOut, tz = DEFAULT_TZ, now = new Date()): string {
  const parts: string[] = []
  if (tx.debt) {
    if (tx.debt.op === 'forgive') parts.push(tx.category?.name ?? 'Долг')
    else parts.push(tx.status === 'needs_account' ? 'счёт не выбран' : debtRealAccount(tx)?.name ?? 'Без счёта')
    if (tx.description) parts.push(`«${tx.description}»`)
    parts.push(formatRelative(tx.occurred_at, now, tz))
    return parts.join(' · ')
  }
  if (tx.counter_account) parts.push(`${tx.account?.name ?? '—'} → ${tx.counter_account.name}`)
  else parts.push(tx.status === 'needs_account' ? 'счёт не выбран' : tx.account?.name ?? 'Без счёта')
  if (tx.counterparty) parts.push(tx.counterparty)
  if (tx.description && tx.description !== tx.category?.name) parts.push(`«${tx.description}»`)
  if (tx.is_estimated && tx.account_amount && tx.account_asset && tx.account_asset !== tx.currency) {
    parts.push(formatMoney(`-${tx.account_amount}`, tx.account_asset, { approx: true }))
  }
  parts.push(formatRelative(tx.occurred_at, now, tz))
  return parts.join(' · ')
}

export function txApproxUsd(tx: TransactionOut): string | null {
  if (!tx.amount_usd || tx.currency === 'USD' || tx.currency === 'VND') return null
  return formatMoney(tx.amount_usd, 'USD', { approx: true })
}

/** Client-built «created» card after choose_account / inline edit / set_category (variant a). */
export function cardFromTransaction(tx: TransactionOut, actionId: string, tz = DEFAULT_TZ): CreatedCard {
  return {
    kind: 'created',
    title: txCardTitle(tx),
    subtitle: txCardSubtitle(tx, tz),
    action_id: actionId,
    transaction: tx,
    category_is_new: !!tx.category?.is_new,
    approx: txApproxUsd(tx),
    note: null,
    buttons: [
      ...(tx.category?.is_new
        ? [{ id: 'change-category', label: 'В другую', style: 'outline', hotkey: null, action: { kind: 'change_category', transaction_id: tx.id } } as Button]
        : []),
      { id: 'undo', label: 'Отменить', style: 'ghost', hotkey: 'Ctrl+Z', action: { kind: 'undo', action_id: actionId } },
      { id: 'edit', label: 'Изменить', style: 'ghost', hotkey: 'Ctrl+E', action: { kind: 'edit', transaction_id: tx.id } },
    ],
  }
}

/** The `debt` brief of a card (DEBTS.md §8) still describes `tx` only while its amount and currency are
 *  the same (choosing an account keeps the balance; an edited amount makes «осталось …» stale). */
export function debtBriefFor(prev: Card, tx: TransactionOut): CreatedCard['debt'] {
  if (prev.kind !== 'created' && prev.kind !== 'needs_account' && prev.kind !== 'updated') return null
  const d = prev.debt
  const old = prev.transaction
  if (!d || old.id !== tx.id || old.amount !== tx.amount || old.currency !== tx.currency) return null
  return d
}

/** A client-built card for `tx` that keeps the debt line of the card it replaces (when still true). */
export function withDebtOf<C extends Card>(next: C, prev: Card, tx: TransactionOut): C {
  if (next.kind !== 'created' && next.kind !== 'needs_account' && next.kind !== 'updated') return next
  const debt = debtBriefFor(prev, tx)
  return debt ? { ...next, debt } : next
}

/** «Отменено: …» line (variant i) with [Вернуть] → redo = undo of the undo action. */
export function undoneCard(r: UndoResult): Card {
  // the backend sends full phrases («Отменено: …», «Возвращено: …», «Снова удалено: …»)
  const summary = r.summary
  return {
    kind: 'undone',
    title: summary,
    subtitle: null,
    undone_action_id: r.undone_action_id,
    undo_action_id: r.undo_action_id,
    buttons: r.undo_action_id
      ? [{ id: 'redo', label: 'Вернуть', style: 'ghost', hotkey: null, action: { kind: 'redo', action_id: r.undo_action_id } }]
      : [],
  }
}

export type LocalErrorKind = 'network' | 'unknown' | 'in_progress' | 'server' | 'auth' | 'rate' | 'other'

/** Local cards whose request may still land on the server: re-sent automatically with the same key. */
export const AUTO_RETRY_KINDS = new Set<LocalErrorKind>(['network', 'unknown', 'in_progress'])

/**
 * Local error card — never claims anything was saved, and never claims «ничего не записано» when the
 * outcome is unknown:
 * - network: the browser is offline, the request did not leave;
 * - unknown: no answer (connection dropped / timeout) — the server may have saved it; the same key
 *   makes a repeat safe (a replay settles the outcome);
 * - in_progress: the first attempt is still running on the server (409 idempotency_in_progress).
 */
export function localErrorCard(kind: LocalErrorKind, message?: string): Card {
  const text: Record<LocalErrorKind, [string, string | null]> = {
    network: ['Не сохранено — нет связи', 'Текст остался в поле. Повторю, когда связь вернётся.'],
    unknown: ['Нет ответа сервера — не знаю, сохранилось ли', 'Повторю, когда связь вернётся. Повтор не создаст дубль.'],
    in_progress: ['Ещё обрабатывается…', 'Сервер ещё выполняет этот запрос. «Повторить» покажет результат и не создаст дубль.'],
    server: ['Не удалось сохранить. Ничего не записано', message ?? null],
    auth: ['Сессия закончилась — войдите снова. Набранный текст сохранён', null],
    rate: ['Слишком много запросов. Ничего не записано', message ?? 'Подождите немного и повторите.'],
    other: [message ?? 'Не удалось выполнить запрос. Ничего не записано', null],
  }
  const [title, subtitle] = text[kind]
  return {
    kind: 'error',
    code: 'internal',
    title,
    subtitle,
    text: subtitle,
    keep_input: true,
    buttons: kind === 'auth' ? [] : [
      { id: 'manual', label: 'Вручную', style: 'ghost', hotkey: null, action: { kind: 'manual' } },
      { id: 'retry', label: 'Повторить', style: 'outline', hotkey: 'Enter', action: { kind: 'retry' } },
    ],
  }
}

/** Account choice by digit 1–9 for a needs_account card (DESIGN §9.2 d). */
export function accountButtonForDigit(c: Card, digit: string): Button | undefined {
  if (c.kind !== 'needs_account') return undefined
  const byHotkey = c.buttons.find((b) => b.action.kind === 'choose_account' && b.hotkey === digit)
  if (byHotkey) return byHotkey
  const choices = c.buttons.filter((b) => b.action.kind === 'choose_account')
  const n = Number(digit)
  return n >= 1 && n <= choices.length ? choices[n - 1] : undefined
}

export type CardHotkey = 'undo' | 'edit' | 'confirm' | 'cancel' | { digit: string } | null

/**
 * Map a key press in the AI line to a card command. Only when the field is empty (otherwise
 * Ctrl+Z is a text undo, digits are typed) — preview Enter/Esc are handled by the preview card itself.
 */
export function cardHotkey(e: { key: string; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey: boolean }, fieldEmpty: boolean): CardHotkey {
  if (!fieldEmpty) return null
  const mod = e.ctrlKey || e.metaKey
  if (mod && !e.shiftKey && (e.key === 'z' || e.key === 'Z' || e.key === 'я' || e.key === 'Я')) return 'undo'
  if (mod && !e.shiftKey && (e.key === 'e' || e.key === 'E' || e.key === 'у' || e.key === 'У')) return 'edit'
  if (!mod && !e.altKey && /^[1-9]$/.test(e.key)) return { digit: e.key }
  return null
}

/** Operations a card shows (created_many: the ones still on the card). */
export function cardTransactions(c: Card): TransactionOut[] {
  if (c.kind === 'created_many') return c.transactions
  return 'transaction' in c && c.transaction ? [c.transaction] : []
}

/** Card kinds that start with creating operations: their «Отменить» means «remove the operation». */
export const CREATING_KINDS = new Set<Card['kind']>(['created', 'created_many', 'needs_account'])

/** The card re-drawn for the current server state of one of its operations. */
export function withTransaction(c: Card, tx: TransactionOut, tz = DEFAULT_TZ): Card {
  switch (c.kind) {
    case 'created_many':
      return { ...c, transactions: c.transactions.map((t) => (t.id === tx.id ? tx : t)) }
    case 'created':
      return c.transaction.id === tx.id ? withDebtOf({ ...cardFromTransaction(tx, c.action_id, tz), note: c.note }, c, tx) : c
    case 'needs_account':
      if (c.transaction.id !== tx.id) return c
      return tx.status === 'needs_account'
        ? { ...c, transaction: tx, title: txCardTitle(tx), debt: debtBriefFor(c, tx) }
        : withDebtOf(cardFromTransaction(tx, c.action_id, tz), c, tx)
    default:
      return 'transaction' in c && c.transaction && c.transaction.id === tx.id ? ({ ...c, transaction: tx } as Card) : c
  }
}

/** The card after one of its operations was deleted elsewhere (null → nothing left to show). */
export function withoutTransaction(c: Card, txId: number): Card | null {
  if (c.kind === 'created_many') {
    const rest = c.transactions.filter((t) => t.id !== txId)
    return rest.length ? { ...c, transactions: rest } : null
  }
  return 'transaction' in c && c.transaction?.id === txId ? null : c
}

/**
 * The field still shows the text being sent (`inFlight`) and the user types: keep only the new
 * characters (typed after or before it). Edits inside the sent text (Backspace, a fix) stay as they are.
 */
export function typeOver(prev: string, next: string, inFlight: string | null): string {
  if (!inFlight || prev.trim() !== inFlight || next.length <= prev.length) return next
  if (next.startsWith(prev)) {
    const added = next.slice(prev.length)
    return added.trim() ? added.replace(/^\s+/, '') : next // Shift+Enter / a space: still the same phrase
  }
  if (next.endsWith(prev)) return next.slice(0, next.length - prev.length)
  return next
}

/**
 * 409 idempotency_in_progress: the first attempt is still running (the LLM may take up to
 * AI_TIMEOUT = 20 s) — keep asking with the same key for ~30 s before giving up (ms between asks).
 */
export const RETRY_IN_PROGRESS = [800, 1500, 3000, 5000, 5000, 5000, 5000, 5000]
