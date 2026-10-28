// Date helpers in the user's timezone (DESIGN §3.3). Dates are 'YYYY-MM-DD' strings; arithmetic
// is done on UTC-midnight Date objects so the browser timezone never leaks in.
import type { PeriodPreset } from '@/lib/api'

export const DEFAULT_TZ = 'Asia/Ho_Chi_Minh'

export const MONTHS_NOM = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']
export const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
export const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
export const MONTHS_SHORT_NOM = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
export const WEEKDAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
export const WD_SHORT = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']

export interface DateSpan {
  from: string
  to: string
}

const pad = (n: number) => String(n).padStart(2, '0')

export function parseISO(d: string): Date {
  const [y, m, dd] = d.split('-').map(Number)
  return new Date(Date.UTC(y, (m || 1) - 1, dd || 1))
}

export function toISO(d: Date): string {
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}

export function addDays(d: string, n: number): string {
  const x = parseISO(d)
  x.setUTCDate(x.getUTCDate() + n)
  return toISO(x)
}

export function addMonths(d: string, n: number): string {
  const x = parseISO(d)
  const day = x.getUTCDate()
  x.setUTCDate(1)
  x.setUTCMonth(x.getUTCMonth() + n)
  const last = new Date(Date.UTC(x.getUTCFullYear(), x.getUTCMonth() + 1, 0)).getUTCDate()
  x.setUTCDate(Math.min(day, last))
  return toISO(x)
}

export const startOfMonth = (d: string) => `${d.slice(0, 7)}-01`
export function endOfMonth(d: string): string {
  const x = parseISO(d)
  return toISO(new Date(Date.UTC(x.getUTCFullYear(), x.getUTCMonth() + 1, 0)))
}
/** Monday-based week start. */
export function startOfWeek(d: string): string {
  const wd = (parseISO(d).getUTCDay() + 6) % 7
  return addDays(d, -wd)
}
export const startOfYear = (d: string) => `${d.slice(0, 4)}-01-01`
export const endOfYear = (d: string) => `${d.slice(0, 4)}-12-31`
export function startOfQuarter(d: string): string {
  const m = Number(d.slice(5, 7))
  const qm = Math.floor((m - 1) / 3) * 3 + 1
  return `${d.slice(0, 4)}-${pad(qm)}-01`
}
export const endOfQuarter = (d: string) => endOfMonth(addMonths(startOfQuarter(d), 2))

export function daysBetween(from: string, to: string): number {
  return Math.round((parseISO(to).getTime() - parseISO(from).getTime()) / 86_400_000)
}

/** Local date 'YYYY-MM-DD' of an instant in a timezone. */
export function dateInTz(instant: Date, tz: string): string {
  try {
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(instant)
    const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
    return `${get('year')}-${get('month')}-${get('day')}`
  } catch {
    return toISO(instant)
  }
}

export const todayInTz = (tz: string = DEFAULT_TZ, now: Date = new Date()) => dateInTz(now, tz)

/** 'HH:mm' of an ISO datetime in tz. */
export function formatTime(iso: string | null | undefined, tz: string = DEFAULT_TZ): string {
  if (!iso) return ''
  try {
    return new Intl.DateTimeFormat('ru-RU', { timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(iso))
  } catch {
    return iso.slice(11, 16)
  }
}

export function formatDateTime(iso: string | null | undefined, tz: string = DEFAULT_TZ, today?: string): string {
  if (!iso) return '—'
  const d = dateInTz(new Date(iso), tz)
  return `${formatDateShort(d, today ?? todayInTz(tz))}, ${formatTime(iso, tz)}`
}

/** «24 окт», «24 окт 2025» (other year), «сегодня», «вчера». */
export function formatDateShort(d: string, today: string, relative = true): string {
  if (relative) {
    if (d === today) return 'сегодня'
    if (d === addDays(today, -1)) return 'вчера'
  }
  const x = parseISO(d)
  const base = `${x.getUTCDate()} ${MONTHS_SHORT[x.getUTCMonth()]}`
  return d.slice(0, 4) === today.slice(0, 4) ? base : `${base} ${d.slice(0, 4)}`
}

/** Day group header: «Сегодня · пн, 27 октября», «Вчера · вс, 26 октября», «Пятница, 24 октября». */
export function dayGroupLabel(d: string, today: string): string {
  const x = parseISO(d)
  const wd = (x.getUTCDay() + 6) % 7
  const dm = `${x.getUTCDate()} ${MONTHS_GEN[x.getUTCMonth()]}${d.slice(0, 4) === today.slice(0, 4) ? '' : ` ${d.slice(0, 4)}`}`
  if (d === today) return `Сегодня · ${WD_SHORT[wd]}, ${dm}`
  if (d === addDays(today, -1)) return `Вчера · ${WD_SHORT[wd]}, ${dm}`
  return `${WEEKDAYS[wd]}, ${dm}`
}

export function monthLabel(d: string): string {
  const x = parseISO(d)
  return `${MONTHS_NOM[x.getUTCMonth()]} ${x.getUTCFullYear()}`
}

/** «Октябрь 2026», «1–27 окт», «20 сен–5 окт», «2026», «III кв. 2026». */
export function rangeLabel(from: string, to: string): string {
  if (from === startOfYear(from) && to === endOfYear(from)) return from.slice(0, 4)
  if (from === startOfMonth(from) && to === endOfMonth(from)) return monthLabel(from)
  if (from === startOfQuarter(from) && to === endOfQuarter(from)) {
    const q = Math.floor((Number(from.slice(5, 7)) - 1) / 3)
    return `${['I', 'II', 'III', 'IV'][q]} кв. ${from.slice(0, 4)}`
  }
  const a = parseISO(from)
  const b = parseISO(to)
  const sameYear = from.slice(0, 4) === to.slice(0, 4)
  if (from === to) return `${a.getUTCDate()} ${MONTHS_SHORT[a.getUTCMonth()]}${sameYear ? '' : ` ${from.slice(0, 4)}`}`
  if (sameYear && a.getUTCMonth() === b.getUTCMonth()) return `${a.getUTCDate()}–${b.getUTCDate()} ${MONTHS_SHORT[a.getUTCMonth()]}`
  const left = `${a.getUTCDate()} ${MONTHS_SHORT[a.getUTCMonth()]}${sameYear ? '' : ` ${from.slice(0, 4)}`}`
  const right = `${b.getUTCDate()} ${MONTHS_SHORT[b.getUTCMonth()]}${sameYear ? '' : ` ${to.slice(0, 4)}`}`
  return `${left}–${right}`
}

/** Resolve a stats preset into concrete dates (week starts on Monday). `all_time` → null. */
export function resolvePreset(preset: PeriodPreset | string, today: string): DateSpan | null {
  switch (preset) {
    case 'today':
      return { from: today, to: today }
    case 'yesterday': {
      const y = addDays(today, -1)
      return { from: y, to: y }
    }
    case 'this_week':
      return { from: startOfWeek(today), to: addDays(startOfWeek(today), 6) }
    case 'last_week': {
      const s = addDays(startOfWeek(today), -7)
      return { from: s, to: addDays(s, 6) }
    }
    case 'last_7_days':
      return { from: addDays(today, -6), to: today }
    case 'last_30_days':
      return { from: addDays(today, -29), to: today }
    case 'this_month':
      return { from: startOfMonth(today), to: endOfMonth(today) }
    case 'last_month': {
      const s = addMonths(startOfMonth(today), -1)
      return { from: s, to: endOfMonth(s) }
    }
    case 'this_year':
      return { from: startOfYear(today), to: endOfYear(today) }
    case 'last_year': {
      const y = String(Number(today.slice(0, 4)) - 1)
      return { from: `${y}-01-01`, to: `${y}-12-31` }
    }
    default:
      return null
  }
}

export const PRESET_LABELS: Record<string, string> = {
  today: 'Сегодня',
  yesterday: 'Вчера',
  this_week: 'Эта неделя',
  last_week: 'Прошлая неделя',
  last_7_days: '7 дней',
  last_30_days: '30 дней',
  this_month: 'Этот месяц',
  last_month: 'Прошлый месяц',
  this_year: 'Этот год',
  last_year: 'Прошлый год',
  all_time: 'Всё время',
}
