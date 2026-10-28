// Category slices → top-7 + «Прочее» with stable colours (DESIGN §1.5). Exact decimal sums.
import type { CategorySlice } from '@/lib/api'
import { decAdd, decCmp, decSign, decToNumber } from '@/features/operations/lib/decimal'
import { formatMoney, formatPercent } from '@/features/operations/lib/format'

export const CHART_COLORS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)', 'var(--chart-6)', 'var(--chart-7)']
export const OTHER_COLOR = 'var(--chart-8)'

export interface Slice {
  key: string
  categoryIds: number[]
  name: string
  icon: string | null
  isBusiness: boolean
  isNew: boolean
  amount: string
  share: string
  count: number
  color: string
  value: number
}

export function topSlices(items: CategorySlice[], top = 7): Slice[] {
  const sorted = [...items].sort((a, b) => decCmp(b.amount, a.amount))
  const head = sorted.slice(0, top).map<Slice>((s, i) => ({
    key: `c${s.category_id}`,
    categoryIds: [s.category_id],
    name: s.name,
    icon: s.icon,
    isBusiness: s.is_business,
    isNew: s.is_new,
    amount: s.amount,
    share: s.share,
    count: s.count,
    color: CHART_COLORS[i],
    value: decToNumber(s.amount),
  }))
  const tail = sorted.slice(top)
  if (tail.length) {
    const amount = decAdd(...tail.map((t) => t.amount))
    head.push({
      key: 'other',
      categoryIds: tail.map((t) => t.category_id),
      name: `ещё ${tail.length}`,
      icon: null,
      isBusiness: false,
      isNew: false,
      amount,
      share: decAdd(...tail.map((t) => t.share)),
      count: tail.reduce((n, t) => n + t.count, 0),
      color: OTHER_COLOR,
      value: decToNumber(amount),
    })
  }
  return head
}

/** Colour of a category by its rank in the current period (for compare bars). */
export function colorMap(items: CategorySlice[]): Map<number, string> {
  const m = new Map<number, string>()
  topSlices(items).forEach((s) => s.categoryIds.forEach((id) => m.set(id, s.color)))
  return m
}

export interface ComparePair {
  categoryId: number
  name: string
  current: string
  previous: string
  /** (cur - prev) / prev as a decimal string, null when prev = 0. */
  change: string | null
}

/** Pair current vs previous by category (union, sorted by current desc). */
export function comparePairs(current: CategorySlice[], previous: CategorySlice[], limit = 8): ComparePair[] {
  const prev = new Map(previous.map((p) => [p.category_id, p]))
  const ids = new Set([...current.map((c) => c.category_id), ...previous.map((p) => p.category_id)])
  const cur = new Map(current.map((c) => [c.category_id, c]))
  const rows: ComparePair[] = []
  for (const id of ids) {
    const c = cur.get(id)
    const p = prev.get(id)
    const ca = c?.amount ?? '0'
    const pa = p?.amount ?? '0'
    const pn = decToNumber(pa)
    rows.push({ categoryId: id, name: (c ?? p)!.name, current: ca, previous: pa, change: pn ? String((decToNumber(ca) - pn) / pn) : null })
  }
  return rows.sort((a, b) => decCmp(b.current, a.current) || decCmp(b.previous, a.previous)).slice(0, limit)
}

/** One-line conclusion of the comparison. Categories that had nothing before (no % — «новое») count as growth. */
export function compareSummary(pairs: ComparePair[], currency: string): string {
  const up = pairs.filter((p) => p.change && Number(p.change) > 0).sort((a, b) => Number(b.change) - Number(a.change))[0]
  const fresh = pairs.filter((p) => p.change === null && decSign(p.current) > 0).sort((a, b) => decToNumber(b.current) - decToNumber(a.current))
  const freshText = fresh.length ? `новые траты: «${fresh[0].name}» ${formatMoney(fresh[0].current, currency)}${fresh.length > 1 ? ` и ещё ${fresh.length - 1}` : ''}` : ''
  if (up) return `Сильнее всего выросли траты на «${up.name}»: ${formatPercent(up.change, { sign: 'always' })}${freshText ? `; ${freshText}` : ''}`
  if (freshText) return freshText[0].toUpperCase() + freshText.slice(1)
  return 'Траты по категориям не выросли'
}
