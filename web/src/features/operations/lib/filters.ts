// Operations filter state ⇄ URL (?period=2026-10&cat=1,2 …, DESIGN §9.5) ⇄ GET /transactions query.
// URL contract (also used by KPI tiles, AI answers and bot links):
//   period = today|yesterday|this_week|last_week|last_7_days|last_30_days|this_month|last_month|
//            this_year|last_year|all_time|YYYY-MM|YYYY|custom (with from/to=YYYY-MM-DD)
//   q, cat=1,2 (alias category_id)  acc=3 (alias account_id)  cur=USD  kind=expense,income  biz=business|personal  status=needs_account
//   min, max, amt_cur (amount range), sort=date_desc|date_asc|amount_desc|amount_asc, tx=<id>
import type { SearchFilter, TransactionFilter, TxKind } from '@/lib/api'
import { endOfMonth, endOfYear, rangeLabel, resolvePreset, PRESET_LABELS, type DateSpan } from './dates'
import { normalizeAmountInput } from './decimal'

export type BizFilter = 'all' | 'business' | 'personal'
export type SortKey = NonNullable<TransactionFilter['sort']>

export interface OpsFilter {
  period: string
  from: string
  to: string
  q: string
  cat: number[]
  acc: number[]
  cur: string
  kind: TxKind[]
  biz: BizFilter
  status: '' | 'needs_account'
  min: string
  max: string
  amtCur: string
  sort: SortKey
}

export const ALL_KINDS: TxKind[] = ['expense', 'income', 'refund', 'transfer', 'exchange', 'adjustment']
export const KIND_LABELS: Record<TxKind, string> = {
  expense: 'Расход',
  income: 'Доход',
  refund: 'Возврат',
  transfer: 'Перевод',
  exchange: 'Обмен',
  adjustment: 'Корректировка',
}

export const DEFAULT_FILTER: OpsFilter = {
  period: 'this_month',
  from: '',
  to: '',
  q: '',
  cat: [],
  acc: [],
  cur: '',
  kind: [],
  biz: 'all',
  status: '',
  min: '',
  max: '',
  amtCur: '',
  sort: 'date_desc',
}

const ids = (s: string | null) =>
  (s ?? '')
    .split(',')
    .map((x) => Number(x.trim()))
    .filter((n) => Number.isInteger(n) && n > 0)

const SORTS: SortKey[] = ['date_desc', 'date_asc', 'amount_desc', 'amount_asc']

export function parseFilter(sp: URLSearchParams): OpsFilter {
  const status = sp.get('status') === 'needs_account' ? 'needs_account' : ''
  const from = sp.get('from') ?? sp.get('date_from') ?? ''
  const to = sp.get('to') ?? sp.get('date_to') ?? ''
  let period = sp.get('period') ?? ''
  if (!period) period = from || to ? 'custom' : status ? 'all_time' : DEFAULT_FILTER.period
  const biz = sp.get('biz')
  const sort = sp.get('sort') as SortKey | null
  return {
    period,
    from,
    to,
    q: sp.get('q') ?? '',
    cat: ids(sp.get('cat') ?? sp.getAll('category_id').join(',')),
    acc: ids(sp.get('acc') ?? sp.getAll('account_id').join(',')),
    cur: (sp.get('cur') ?? '').toUpperCase(),
    kind: (sp.get('kind') ?? '').split(',').filter((k): k is TxKind => (ALL_KINDS as string[]).includes(k)),
    biz: biz === 'business' || biz === 'personal' ? biz : 'all',
    status,
    min: sp.get('min') ?? '',
    max: sp.get('max') ?? '',
    amtCur: (sp.get('amt_cur') ?? '').toUpperCase(),
    sort: sort && SORTS.includes(sort) ? sort : 'date_desc',
  }
}

/** Serialise only non-default values; keeps `tx` if present in `keep`. */
export function filterToParams(f: OpsFilter, keep?: URLSearchParams): URLSearchParams {
  const sp = new URLSearchParams()
  const defPeriod = f.status ? 'all_time' : DEFAULT_FILTER.period
  if (f.period && f.period !== defPeriod) sp.set('period', f.period)
  if (f.period === 'custom') {
    if (f.from) sp.set('from', f.from)
    if (f.to) sp.set('to', f.to)
  }
  if (f.q) sp.set('q', f.q)
  if (f.cat.length) sp.set('cat', f.cat.join(','))
  if (f.acc.length) sp.set('acc', f.acc.join(','))
  if (f.cur) sp.set('cur', f.cur)
  if (f.kind.length) sp.set('kind', f.kind.join(','))
  if (f.biz !== 'all') sp.set('biz', f.biz)
  if (f.status) sp.set('status', f.status)
  if (f.min) sp.set('min', f.min)
  if (f.max) sp.set('max', f.max)
  if (f.amtCur) sp.set('amt_cur', f.amtCur)
  if (f.sort !== 'date_desc') sp.set('sort', f.sort)
  const tx = keep?.get('tx')
  if (tx) sp.set('tx', tx)
  return sp
}

/** Period → concrete dates; null = no date restriction. */
export function periodSpan(f: Pick<OpsFilter, 'period' | 'from' | 'to'>, today: string): DateSpan | null {
  const p = f.period
  if (p === 'custom') return f.from || f.to ? { from: f.from || '1970-01-01', to: f.to || '2999-12-31' } : null
  if (/^\d{4}-\d{2}$/.test(p)) return { from: `${p}-01`, to: endOfMonth(`${p}-01`) }
  if (/^\d{4}$/.test(p)) return { from: `${p}-01-01`, to: endOfYear(`${p}-01-01`) }
  return resolvePreset(p, today)
}

export function periodLabel(f: Pick<OpsFilter, 'period' | 'from' | 'to'>, today: string): string {
  if (PRESET_LABELS[f.period]) return PRESET_LABELS[f.period]
  const span = periodSpan(f, today)
  if (!span) return 'Всё время'
  if (f.period === 'custom' && (!f.from || !f.to)) return f.from ? `с ${f.from}` : `по ${f.to}`
  return rangeLabel(span.from, span.to)
}

// ------------------------------------------------------------------ amount search («>100$», «150к»)
const CUR_WORDS: [RegExp, string][] = [
  [/^(\$|usd|долл\w*|бакс\w*)$/i, 'USD'],
  [/^(₫|vnd|đ|донг\w*)$/i, 'VND'],
  [/^(usdt|юсдт)$/i, 'USDT'],
  [/^(₽|rub|руб\w*)$/i, 'RUB'],
]

export interface AmountQuery {
  min?: string
  max?: string
  currency: string
}

/** Parses «>100$», «< 50к», «150к», «от 1м», «до 20$»; plain words/numbers without hint → null. */
export function parseAmountQuery(raw: string): AmountQuery | null {
  const s = raw.trim().replace(/\s+/g, ' ')
  const m = /^(>=?|<=?|от|до)?\s?(\$)?\s?(\d[\d\s.,]*\d|\d)\s?(млн|kk|кк|к|k|м|m)?\s?([^\d\s]+)?$/i.exec(s)
  if (!m) return null
  const [, op, dollarPrefix, num, suffix, curWord] = m
  let currency = dollarPrefix ? 'USD' : ''
  if (curWord) {
    const hit = CUR_WORDS.find(([re]) => re.test(curWord))
    if (!hit) return null
    currency = hit[1]
  }
  if (!op && !suffix && !currency) return null
  const value = normalizeAmountInput(num + (suffix ?? ''))
  if (!value) return null
  currency ||= 'VND'
  if (op === '>' || op === '>=' || op === 'от') return { min: value, currency }
  if (op === '<' || op === '<=' || op === 'до') return { max: value, currency }
  return { min: value, max: value, currency }
}

export function toApiFilter(f: OpsFilter, today: string): TransactionFilter {
  const span = periodSpan(f, today)
  const out: TransactionFilter = { sort: f.sort }
  if (span) {
    out.date_from = span.from
    out.date_to = span.to
  }
  const amountQ = f.q ? parseAmountQuery(f.q) : null
  if (amountQ) {
    if (amountQ.min) out.min_amount = amountQ.min
    if (amountQ.max) out.max_amount = amountQ.max
    out.amount_currency = amountQ.currency
    if (!['USD', 'VND'].includes(amountQ.currency)) out.currency = amountQ.currency
  } else if (f.q) out.q = f.q
  if (f.min) out.min_amount = f.min
  if (f.max) out.max_amount = f.max
  if (f.amtCur) out.amount_currency = f.amtCur
  if (f.cat.length) out.category_id = f.cat
  if (f.acc.length) out.account_id = f.acc
  if (f.cur) out.currency = f.cur
  if (f.kind.length) out.kind = f.kind
  if (f.biz !== 'all') out.is_business = f.biz === 'business'
  if (f.status) out.status = f.status
  return out
}

/** API filter → query object for api.get / export URL (repeatable params as arrays). */
export function apiQuery(f: TransactionFilter): Record<string, string | number | boolean | (string | number)[] | undefined> {
  return {
    date_from: f.date_from,
    date_to: f.date_to,
    kind: f.kind,
    category_id: f.category_id,
    account_id: f.account_id,
    currency: f.currency,
    min_amount: f.min_amount,
    max_amount: f.max_amount,
    amount_currency: f.amount_currency,
    q: f.q,
    is_business: f.is_business,
    status: f.status,
    merchant_id: f.merchant_id,
    sort: f.sort,
    limit: f.limit,
    offset: f.offset,
  }
}

export function activeFilterCount(f: OpsFilter): number {
  let n = 0
  if (f.cat.length) n++
  if (f.acc.length) n++
  if (f.cur) n++
  if (f.kind.length) n++
  if (f.biz !== 'all') n++
  if (f.status) n++
  if (f.min || f.max) n++
  if (f.period !== DEFAULT_FILTER.period && !(f.status && f.period === 'all_time')) n++
  return n
}

/** AI answer card filter (SearchFilter) → /operations URL params. Exported for the AI answer cards. */
export function searchFilterToParams(sf: SearchFilter): URLSearchParams {
  const f: OpsFilter = { ...DEFAULT_FILTER, period: 'all_time' }
  if (sf.period) {
    f.period = sf.period.preset
    if (sf.period.preset === 'custom') {
      f.from = sf.period.date_from ?? ''
      f.to = sf.period.date_to ?? ''
    }
  }
  if (sf.kinds?.length) f.kind = sf.kinds
  if (sf.category_ids?.length) f.cat = sf.category_ids
  if (sf.account_ids?.length) f.acc = sf.account_ids
  if (sf.currency) f.cur = sf.currency
  if (sf.min_amount) f.min = sf.min_amount
  if (sf.max_amount) f.max = sf.max_amount
  if (sf.amount_currency) f.amtCur = sf.amount_currency
  if (sf.text || sf.counterparty) f.q = (sf.text ?? sf.counterparty) as string
  if (sf.is_business === true) f.biz = 'business'
  if (sf.is_business === false) f.biz = 'personal'
  if (sf.needs_account) f.status = 'needs_account'
  if (sf.sort) f.sort = sf.sort
  return filterToParams(f)
}
