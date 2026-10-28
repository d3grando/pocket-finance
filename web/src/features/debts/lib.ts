// Pure helpers for debts (DEBTS.md §8, §10): labels, balance texts, ages, card lines.
// Money stays Decimal strings (lib/decimal); names are shown as the server gives them (nominative after
// a colon — never declined).
import type {
  AccountOut, ActionOut, AuditEntryOut, DebtBalanceOut, DebtBrief, DebtDirection, DebtOpKind, DebtOut, DebtTotalsOut, DebtsOut,
  TransactionOut,
} from '@/lib/api/types'
import { Dec } from '@/lib/decimal'
import { formatMoney, isFiatCurrency, plural } from '@/lib/format'

/** Money comes to me on these operations: the real account is the receiver (`counter_account`). */
export const isInflowOp = (op: DebtOpKind | null | undefined) => op === 'repay_in' || op === 'borrow'

/** Title verb of an operation («Дал в долг: Коля»). Forgive depends on the side it closed. */
export function opVerb(op: DebtOpKind, forgiveOwedToMe = true): string {
  switch (op) {
    case 'lend': return 'Дал в долг'
    case 'repay_in': return 'Возврат долга'
    case 'borrow': return 'Занял'
    case 'repay_out': return 'Вернул долг'
    case 'forgive': return forgiveOwedToMe ? 'Списан долг' : 'Долг прощён'
  }
}

/** Short label for the history of a person (DEBTS.md §10). */
export function historyLabel(op: DebtOpKind, delta: string): string {
  if (op === 'repay_in') return 'Возврат'
  if (op === 'forgive') return Dec.from(delta).isNeg() ? 'Списан' : 'Прощён'
  return opVerb(op)
}

/** «Дал в долг: Коля» for an operation with `tx.debt`, else null. Forgive: expense = I wrote off what
 *  the person owed me; income = the person forgave my debt. */
export function debtTxTitle(tx: Pick<TransactionOut, 'debt' | 'kind'>): string | null {
  if (!tx.debt) return null
  return `${opVerb(tx.debt.op, tx.kind !== 'income')}: ${tx.debt.name}`
}

/** The real account of a debt operation (the other side is the person's service account). */
export function debtRealAccount(tx: TransactionOut): TransactionOut['account'] {
  if (!tx.debt || tx.debt.op === 'forgive') return null
  return isInflowOp(tx.debt.op) ? tx.counter_account : tx.account
}

/** «С какого счёта?» / «На какой счёт?» for an operation waiting for its account. */
export function accountQuestion(tx: Pick<TransactionOut, 'kind' | 'debt'>): string {
  if (tx.debt) return isInflowOp(tx.debt.op) ? 'На какой счёт?' : 'С какого счёта?'
  return tx.kind === 'income' || tx.kind === 'refund' ? 'На какой счёт?' : 'С какого счёта?'
}

const abs = (s: string) => Dec.from(s).abs().toString()

/** «300 000 ₫ · 50 USDT» — each currency on its own, never converted. */
export function balancesText(balances: Pick<DebtBalanceOut, 'amount' | 'currency'>[]): string {
  return balances.map((b) => formatMoney(abs(b.amount), b.currency)).join(' · ')
}

/** «≈ $61.48» next to the balances when some of them are not in $ (null: nothing to add / no rates). */
export function balancesApproxUsd(balances: DebtBalanceOut[]): string | null {
  if (!balances.length || balances.every((b) => b.currency === 'USD')) return null
  const known = balances.filter((b) => b.approx_usd != null)
  if (!known.length) return null
  const sum = known.reduce((s, b) => s.add(Dec.from(b.approx_usd!).abs()), Dec.ZERO)
  return formatMoney(sum.toString(), 'USD', { approx: true })
}

/** Whole days between two local dates 'YYYY-MM-DD'. */
export function daysBetween(from: string, to: string): number {
  const a = Date.UTC(+from.slice(0, 4), +from.slice(5, 7) - 1, +from.slice(8, 10))
  const b = Date.UTC(+to.slice(0, 4), +to.slice(5, 7) - 1, +to.slice(8, 10))
  return Math.round((b - a) / 86_400_000)
}

/** Age of the oldest open balance in days (null when unknown). */
export function debtAgeDays(balances: Pick<DebtBalanceOut, 'open_since'>[], today: string): number | null {
  const ages = balances.filter((b) => b.open_since).map((b) => Math.max(0, daysBetween(b.open_since!, today)))
  return ages.length ? Math.max(...ages) : null
}

/** «30 дн.» / «сегодня» (null — no date). */
export function ageLabel(days: number | null): string | null {
  if (days == null) return null
  if (days === 0) return 'сегодня'
  return `${days} дн.`
}

/** Balances of one side of a person (a person in both lists shows only that side's currencies). */
export function sideBalances(d: DebtOut, side: DebtDirection): DebtBalanceOut[] {
  return d.balances.filter((b) => b.direction === side)
}

/** Header number of a side: «≈ 12 300 000 ₫» in the summary currency (₫ by default) + «≈ $470». */
export function sideTotal(totals: DebtTotalsOut, side: DebtDirection, summary: 'VND' | 'USD' = 'VND'): { main: string | null; approx: string | null } {
  const vnd = side === 'owed_to_me' ? totals.owed_to_me_vnd : totals.i_owe_vnd
  const usd = side === 'owed_to_me' ? totals.owed_to_me_usd : totals.i_owe_usd
  if (summary === 'USD') return { main: usd != null ? formatMoney(abs(usd), 'USD', { approx: true }) : null, approx: null }
  return {
    main: vnd != null ? formatMoney(abs(vnd), 'VND', { approx: true }) : null,
    approx: usd != null ? formatMoney(abs(usd), 'USD', { approx: true }) : null,
  }
}

export const hasOpenDebts = (d: DebtsOut | undefined | null) => !!d && (d.owed_to_me.length > 0 || d.i_owe.length > 0)

/** One line for an assistant card with `card.debt` (DEBTS.md §10): «Коля: долг тебе 300 000 ₫». */
export function debtBriefLine(b: DebtBrief): string {
  const v = Dec.from(b.balance_after)
  if (v.isZero()) return `${b.name} · долг закрыт`
  const money = formatMoney(abs(b.balance_after), b.currency)
  return v.isPos() ? `${b.name}: долг тебе ${money}` : `Твой долг · ${b.name}: ${money}`
}

/** The balance part the server appends to a debt card subtitle (backend `ai/debt_cards.balance_text`):
 *  «Коля: долг тебе 500 000 ₫» / «ты должен …» / «осталось …» / «долг закрыт». */
export function serverBalanceText(b: DebtBrief): string {
  const v = Dec.from(b.balance_after)
  if (v.isZero()) return 'долг закрыт'
  const money = formatMoney(abs(b.balance_after), b.currency)
  if (b.op === 'repay_in') return v.isPos() ? `осталось ${money}` : 'долг закрыт'
  if (b.op === 'repay_out') return v.isNeg() ? `осталось ${money}` : 'долг закрыт'
  if (b.op === 'forgive') return `осталось ${money}`
  return v.isPos() ? `${b.name}: долг тебе ${money}` : `ты должен ${money}`
}

/** The card title ends with the person's name («… · Дал в долг: Коля») — the title can link it. */
export const titleLinksDebt = (title: string, b: Pick<DebtBrief, 'name'>) => title.endsWith(`: ${b.name}`)

const squash = (s: string) => s.replace(/\s+/g, ' ').trim().toLowerCase()

/** The card subtitle already says what the debt line would («Карта · Коля: долг тебе 400 000 ₫»): the line is
 *  not repeated. A subtitle from an older server without the balance keeps the line. */
export function subtitleHasDebtLine(subtitle: string | null | undefined, b: DebtBrief): boolean {
  if (!subtitle) return false
  const s = squash(subtitle)
  return s.includes(squash(debtBriefLine(b))) || s.includes(squash(serverBalanceText(b)))
}

/** Lines of an `updated` card not already in its subtitle («Изменено: 100 000 ₫ → 150 000 ₫ · …»). */
export function changesNotInSubtitle(changes: string[], subtitle: string | null | undefined): string[] {
  if (!subtitle) return changes
  const s = squash(subtitle)
  return changes.filter((c) => !s.includes(squash(c)))
}

/** «Отменить последнюю» when the operation was changed after it was recorded (the creating action → 409
 *  undo_conflict, DEBTS.md §15): the newest action in its audit history that is still in effect — not an
 *  undo itself, not undone. Actions missing from `actions` (older than that page) count as live. With no
 *  such action (e.g. the operation was undone and restored), the newest one (the restore) is undone. */
export function latestLiveAction(audit: AuditEntryOut[], actions: ActionOut[], skip: string | null = null): string | null {
  const byId = new Map(actions.map((a) => [a.id, a]))
  const ids: string[] = []
  for (const e of [...audit].sort((x, y) => y.id - x.id)) {
    if (e.action_id && e.action_id !== skip && !ids.includes(e.action_id)) ids.push(e.action_id)
  }
  const live = ids.find((id) => {
    const a = byId.get(id)
    return !a || (a.kind !== 'undo' && !a.undone_at && a.is_undoable)
  })
  return live ?? ids[0] ?? null
}

/** Overpayment note: «Излишек 100 000 ₫ — теперь ты должен: Коля» (null — no excess). */
export function excessText(b: DebtBrief): string | null {
  if (!b.excess || Dec.from(b.excess).isZero()) return null
  const money = formatMoney(abs(b.excess), b.currency)
  return Dec.from(b.balance_after).isNeg() ? `Излишек ${money} — теперь ты должен: ${b.name}` : `Излишек ${money} — теперь это долг тебе: ${b.name}`
}

/** Direction text of one balance: «долг тебе 300 000 ₫» / «ты должен 1 000 000 ₫» (no gendered «должен» about the person). */
export function balanceDirectionText(b: Pick<DebtBalanceOut, 'amount' | 'currency'>): string {
  const money = formatMoney(abs(b.amount), b.currency)
  return Dec.from(b.amount).isNeg() ? `ты должен ${money}` : `долг тебе ${money}`
}

/** Which repay operation closes a balance: they owe me → they return (repay_in), I owe → I return. */
export const repayOpFor = (amount: string): DebtOpKind => (Dec.from(amount).isNeg() ? 'repay_out' : 'repay_in')

/** People for the autocomplete: name / alias / @handle starts a word with the typed text. */
export function matchPeople(people: DebtOut[], query: string, limit = 6): DebtOut[] {
  const q = query.trim().toLowerCase().replace(/^@/, '').replace(/ё/g, 'е')
  if (!q) return people.slice(0, limit)
  const norm = (s: string) => s.toLowerCase().replace(/ё/g, 'е')
  const hit = (d: DebtOut) =>
    [d.name, ...(d.aliases ?? []), d.handle ?? ''].some((s) => s && norm(s).split(/[\s@]+/).some((w) => w.startsWith(q)))
  return people.filter(hit).slice(0, limit)
}

/** Accounts offered for a debt operation in `currency`: wallets / same currency first; for fiat also the
 *  others ($ from Карта is converted at the VCB rate); a coin never from a single-currency account. */
export function accountsFor(accounts: AccountOut[], currency: string): AccountOut[] {
  const live = accounts.filter((a) => !a.is_archived && a.kind !== 'debt')
  const fit = live.filter((a) => a.is_multi_asset || a.currency === currency)
  return isFiatCurrency(currency) ? [...fit, ...live.filter((a) => !fit.includes(a))] : fit
}

/** «3 человека» */
export const peopleCount = (n: number) => `${n} ${plural(n, ['человек', 'человека', 'человек'])}`

/** Unique people of both lists (a person with debts both ways appears once). */
export function allPeople(d: DebtsOut | undefined): DebtOut[] {
  if (!d) return []
  const seen = new Map<number, DebtOut>()
  for (const p of [...d.owed_to_me, ...d.i_owe, ...(d.settled ?? [])]) if (!seen.has(p.id)) seen.set(p.id, p)
  return [...seen.values()]
}

export const DEBT_DAYS_MIN = 1
export const DEBT_DAYS_MAX = 365

/** Settings «старше N дней»: «45» → 45; anything else (empty, 0, 400, «3.5») → null. */
export function parseReminderDays(raw: string): number | null {
  const t = raw.trim()
  if (!/^\d{1,3}$/.test(t)) return null
  const n = Number(t)
  return n >= DEBT_DAYS_MIN && n <= DEBT_DAYS_MAX ? n : null
}
