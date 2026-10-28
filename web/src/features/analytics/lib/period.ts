// Analytics period: month / quarter / year / custom, in the URL as ?period=2026-10 | 2026-Q4 | 2026 |
// custom&from&to, also accepts stats presets (?period=this_month from bot/AI links). `all_time` stays a
// backend preset (the backend knows the first operation date); its dates are filled from a response.
import {
  addDays,
  addMonths,
  daysBetween,
  endOfMonth,
  endOfQuarter,
  endOfYear,
  monthLabel,
  rangeLabel,
  resolvePreset,
  startOfMonth,
  startOfQuarter,
  startOfYear,
} from '@/features/operations/lib/dates'

export type Scale = 'month' | 'quarter' | 'year' | 'custom'
export interface AnPeriod {
  scale: Scale
  from: string
  to: string
  /** «За всё время»: requests use preset=all_time; from/to are placeholders until `withRange`. */
  preset?: 'all_time'
}

/** Longest span still drawn by days; longer periods are drawn by months. */
export const MAX_DAILY_SPAN = 400

export const isAllTime = (p: AnPeriod) => p.preset === 'all_time'

/** Query params of a period for /stats/*. */
export function statsSpan(p: AnPeriod): Record<string, string> {
  return isAllTime(p) ? { preset: 'all_time' } : { preset: 'custom', date_from: p.from, date_to: p.to }
}

/** all_time with the real range returned by the backend (first operation … today). */
export function withRange(p: AnPeriod, range: { date_from: string; date_to: string } | null | undefined): AnPeriod {
  return isAllTime(p) && range ? { ...p, from: range.date_from, to: range.date_to } : p
}

export function seriesGranularity(p: AnPeriod): 'day' | 'month' {
  return daysBetween(p.from, p.to) + 1 > MAX_DAILY_SPAN ? 'month' : 'day'
}

export function monthPeriod(anchor: string): AnPeriod {
  return { scale: 'month', from: startOfMonth(anchor), to: endOfMonth(anchor) }
}
export function quarterPeriod(anchor: string): AnPeriod {
  return { scale: 'quarter', from: startOfQuarter(anchor), to: endOfQuarter(anchor) }
}
export function yearPeriod(anchor: string): AnPeriod {
  return { scale: 'year', from: startOfYear(anchor), to: endOfYear(anchor) }
}

export function withScale(p: AnPeriod, scale: Scale, today: string): AnPeriod {
  const anchor = today >= p.from && today <= p.to ? today : p.from
  if (scale === 'month') return monthPeriod(anchor)
  if (scale === 'quarter') return quarterPeriod(anchor)
  if (scale === 'year') return yearPeriod(anchor)
  return { scale: 'custom', from: p.from, to: p.to, ...(isAllTime(p) ? { preset: 'all_time' as const } : {}) }
}

export function parseAnalyticsPeriod(sp: URLSearchParams, today: string): AnPeriod {
  const raw = sp.get('period') ?? ''
  let m: RegExpExecArray | null
  if ((m = /^(\d{4})-(\d{2})$/.exec(raw))) return monthPeriod(`${m[1]}-${m[2]}-01`)
  if ((m = /^(\d{4})-Q([1-4])$/i.exec(raw))) return quarterPeriod(`${m[1]}-${String((Number(m[2]) - 1) * 3 + 1).padStart(2, '0')}-01`)
  if ((m = /^(\d{4})$/.exec(raw))) return yearPeriod(`${m[1]}-01-01`)
  if (raw === 'custom') {
    const from = sp.get('from') ?? sp.get('date_from') ?? ''
    const to = sp.get('to') ?? sp.get('date_to') ?? ''
    if (/^\d{4}-\d{2}-\d{2}$/.test(from) && /^\d{4}-\d{2}-\d{2}$/.test(to) && from <= to) return { scale: 'custom', from, to }
    return monthPeriod(today)
  }
  if (raw === 'this_month' || raw === '') return monthPeriod(today)
  if (raw === 'last_month') return monthPeriod(addMonths(startOfMonth(today), -1))
  if (raw === 'this_year') return yearPeriod(today)
  if (raw === 'last_year') return yearPeriod(`${Number(today.slice(0, 4)) - 1}-01-01`)
  const span = resolvePreset(raw, today)
  if (span) return { scale: 'custom', ...span }
  if (raw === 'all_time') return { scale: 'custom', from: today, to: today, preset: 'all_time' }
  return monthPeriod(today)
}

export function periodParam(p: AnPeriod): Record<string, string> {
  if (isAllTime(p)) return { period: 'all_time' }
  if (p.scale === 'month') return { period: p.from.slice(0, 7) }
  if (p.scale === 'quarter') return { period: `${p.from.slice(0, 4)}-Q${Math.floor((Number(p.from.slice(5, 7)) - 1) / 3) + 1}` }
  if (p.scale === 'year') return { period: p.from.slice(0, 4) }
  return { period: 'custom', from: p.from, to: p.to }
}

export function shiftPeriod(p: AnPeriod, dir: -1 | 1): AnPeriod {
  if (isAllTime(p)) return p
  if (p.scale === 'month') return monthPeriod(addMonths(p.from, dir))
  if (p.scale === 'quarter') return quarterPeriod(addMonths(p.from, dir * 3))
  if (p.scale === 'year') return yearPeriod(addMonths(p.from, dir * 12))
  const len = daysBetween(p.from, p.to) + 1
  return { scale: 'custom', from: addDays(p.from, dir * len), to: addDays(p.to, dir * len) }
}

/** The period to compare with: previous month/quarter/year, or equal-length span before; `yoy` — same span a year back.
 *  A running month/quarter/year (today inside, not its last day) is compared with the same number of elapsed
 *  days («1–27 окт» vs «1–27 сен») — the rule of the backend's previous_range, so Home and Analytics agree. */
export function comparePeriod(p: AnPeriod, mode: 'prev' | 'yoy', today?: string): AnPeriod {
  let prev: AnPeriod
  if (mode === 'yoy') {
    const from = addMonths(p.from, -12)
    const to = p.scale === 'month' ? endOfMonth(from) : p.scale === 'quarter' ? endOfQuarter(from) : p.scale === 'year' ? endOfYear(from) : addMonths(p.to, -12)
    prev = { scale: p.scale, from, to }
  } else {
    prev = shiftPeriod(p, -1)
  }
  if (today && p.scale !== 'custom' && p.from <= today && today < p.to) {
    const cut = addDays(prev.from, daysBetween(p.from, today))
    if (cut < prev.to) prev = { ...prev, to: cut }
  }
  return prev
}

/** Is `p` a calendar unit cut short (compare side of a running month)? */
export const isTrimmed = (p: AnPeriod) =>
  (p.scale === 'month' && p.to !== endOfMonth(p.from)) || (p.scale === 'quarter' && p.to !== endOfQuarter(p.from)) || (p.scale === 'year' && p.to !== endOfYear(p.from))

export function periodTitle(p: AnPeriod): string {
  if (isAllTime(p)) return 'За всё время'
  return rangeLabel(p.from, p.to)
}

/** Short label of the comparison side: «к сент.», «к III кв.», «к 2025», «к прошлому периоду». */
export function compareLabel(prev: AnPeriod, today: string): string {
  const MONTHS = ['янв.', 'февр.', 'мар.', 'апр.', 'мая', 'июн.', 'июл.', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.']
  if (prev.scale === 'month') return `к ${MONTHS[Number(prev.from.slice(5, 7)) - 1]}${prev.from.slice(0, 4) === today.slice(0, 4) ? '' : ` ${prev.from.slice(0, 4)}`}`
  if (prev.scale === 'quarter') return `к ${rangeLabel(prev.from, prev.to)}`
  if (prev.scale === 'year') return `к ${prev.from.slice(0, 4)}`
  return 'к прошлому периоду'
}

/** Is `today` inside the period (period not finished yet)? */
export const isCurrent = (p: AnPeriod, today: string) => today >= p.from && today <= p.to

/** Months [YYYY-MM-01] ending with the month of `end`, `n` items. */
export function lastMonths(end: string, n: number): string[] {
  const s = startOfMonth(end)
  return Array.from({ length: n }, (_, i) => addMonths(s, i - n + 1))
}

/** Short side label for the comparison card: «Октябрь», «1–27 окт», «III кв. 2026», «Октябрь 2025». */
export function sideLabel(p: AnPeriod, today: string, running: boolean): string {
  const year = p.from.slice(0, 4)
  const withYear = (s: string) => (year === today.slice(0, 4) || /\d{4}/.test(s) ? s : `${s} ${year}`)
  if (running && p.scale !== 'custom') return rangeLabel(p.from, today)
  if (isTrimmed(p)) return withYear(rangeLabel(p.from, p.to))
  if (p.scale === 'month') return year === today.slice(0, 4) ? monthLabel(p.from).replace(/ \d{4}$/, '') : monthLabel(p.from)
  return withYear(periodTitle(p))
}
