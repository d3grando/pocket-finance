// Money/date formatting — must match backend app/core/money.format_money and
// DESIGN.md §3 (examples there are the unit tests). Money arrives as Decimal strings: never parseFloat
// for arithmetic; format by splitting the string (via lib/decimal BigInt fixed-point).
import { Dec } from './decimal'

export type MoneyString = string

export const NBSP = ' '
export const MINUS = '−'
export const APPROX = '≈'

export interface FormatMoneyOptions {
  /** auto: «−» for negatives only; always: «+»/«−»; never: absolute value */
  sign?: 'auto' | 'always' | 'never'
  /** prefix «≈ » (recalculated amounts) */
  approx?: boolean
}

const FIAT_PREFIX: Record<string, string> = { USD: '$', EUR: '€', GBP: '£' }
const FIAT_SUFFIX: Record<string, string> = { VND: '₫', RUB: '₽' }
const STABLE = new Set(['USDT', 'USDC', 'DAI', 'FDUSD', 'TUSD'])
const FIAT_2 = new Set(['USD', 'EUR', 'GBP', 'THB', 'CNY', 'JPY', 'KZT', 'UAH', 'TRY', 'AED', 'SGD', 'KRW'])

export const currencySymbol = (c: string) => FIAT_PREFIX[c] ?? FIAT_SUFFIX[c] ?? c
/** Fiat (₫, $, ₽, €…) vs a coin (TON, USDT, BTC…): a coin cannot be charged from a single-currency
 * account (the ledger answers 422) — pickers offer only multi-asset wallets and accounts in that coin. */
export const isFiatCurrency = (c: string) => c in FIAT_PREFIX || c in FIAT_SUFFIX || FIAT_2.has(c)

/** Group integer digits with NBSP: "1250000" → "1 250 000" (always, also 4-digit numbers). */
export function groupThousands(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP)
}

/** Digits (no sign) for the currency's display precision. */
function digitsFor(abs: Dec, currency: string): string {
  if (currency === 'VND') return abs.toFixedString(0)
  if (currency === 'RUB') {
    const two = abs.toFixedString(2, true)
    return two.endsWith('.00') ? two.slice(0, -3) : two
  }
  if (currency in FIAT_PREFIX || FIAT_2.has(currency)) return abs.toFixedString(2, true)
  if (STABLE.has(currency)) {
    // "300", "20.50", "20.2552": the card echoes what was written; same as backend money.py
    const six = abs.toFixedString(6)
    if (!six.includes('.')) return six
    const [i, f] = six.split('.')
    return `${i}.${f.padEnd(2, '0')}`
  }
  return abs.toFixedString(8)
}

function signFor(v: Dec, mode: FormatMoneyOptions['sign']): string {
  if (mode === 'never' || v.isZero()) return ''
  if (v.isNeg()) return MINUS
  return mode === 'always' ? '+' : ''
}

function withGroups(digits: string): string {
  const [i, f] = digits.split('.')
  return f !== undefined ? `${groupThousands(i)}.${f}` : groupThousands(i)
}

/**
 * formatMoney("1250000","VND") → "1 250 000 ₫"; formatMoney("-180","USD") → "−$180.00";
 * formatMoney("300","USDT",{sign:"always"}) → "+300 USDT"; formatMoney("12.4","USD",{approx:true}) → "≈ $12.40".
 */
export function formatMoney(amount: MoneyString | null | undefined, currency: string, opts: FormatMoneyOptions = {}): string {
  if (amount == null || amount === '') return '—'
  const v = Dec.tryFrom(String(amount))
  if (!v) return '—'
  const cur = (currency || '').toUpperCase()
  const num = withGroups(digitsFor(v.abs(), cur))
  const sign = signFor(v, opts.sign ?? 'auto')
  const prefix = opts.approx ? `${APPROX}${NBSP}` : ''
  if (FIAT_PREFIX[cur]) return `${prefix}${sign}${FIAT_PREFIX[cur]}${num}`
  return `${prefix}${sign}${num}${NBSP}${FIAT_SUFFIX[cur] ?? cur}`
}

/** Compact for axes/tiles: "1.25м ₫", "450к ₫", "$1.2k". Values below 1000 use formatMoney. */
export function formatCompact(amount: MoneyString | null | undefined, currency: string, opts: FormatMoneyOptions = {}): string {
  if (amount == null || amount === '') return '—'
  const v = Dec.tryFrom(String(amount))
  if (!v) return '—'
  const cur = (currency || '').toUpperCase()
  const abs = v.abs()
  const cyr = cur === 'VND' || cur === 'RUB'
  const steps: [Dec, string, string][] = [
    [Dec.from('1000000000'), 'млрд', 'b'],
    [Dec.from('1000000'), 'м', 'm'],
    [Dec.from('1000'), 'к', 'k'],
  ]
  for (const [base, ru, en] of steps) {
    if (abs.cmp(base) >= 0) {
      const scaled = abs.div(base).toFixedString(cyr ? 2 : 1)
      const sign = signFor(v, opts.sign ?? 'auto')
      const prefix = opts.approx ? `${APPROX}${NBSP}` : ''
      if (FIAT_PREFIX[cur]) return `${prefix}${sign}${FIAT_PREFIX[cur]}${scaled}${en}`
      return `${prefix}${sign}${scaled}${cyr ? ru : en}${NBSP}${FIAT_SUFFIX[cur] ?? cur}`
    }
  }
  return formatMoney(amount, cur, opts)
}

/** Axis label without currency: "450к", "1.2м", "$1.2k". */
export function formatAxis(amount: MoneyString | number, currency: string): string {
  const s = typeof amount === 'number' ? String(Math.round(amount)) : amount
  const full = formatCompact(s, currency)
  const sym = FIAT_SUFFIX[currency.toUpperCase()]
  return sym ? full.replace(`${NBSP}${sym}`, '') : full
}

/** formatPercent("0.183",{sign:"always"}) → "+18%"; "0.045" → "+4.5%" (1 digit only when |x| < 10%). */
export function formatPercent(ratio: MoneyString | number | null | undefined, opts: { sign?: 'auto' | 'always' } = {}): string {
  if (ratio == null || ratio === '') return '—'
  const v = typeof ratio === 'number' ? Dec.tryFrom(ratio.toFixed(6)) : Dec.tryFrom(String(ratio))
  if (!v) return '—'
  const pct = v.mul(100)
  const places = pct.abs().cmp(10) < 0 ? 1 : 0
  const digits = pct.abs().toFixedString(places)
  const rounded = Dec.from(digits)
  const sign = rounded.isZero() ? '' : v.isNeg() ? MINUS : opts.sign === 'always' ? '+' : ''
  return `${sign}${digits}%`
}

/** Money for screen readers: «150 000 донгов». */
export function moneyAria(amount: MoneyString | null | undefined, currency: string): string {
  if (amount == null) return 'нет данных'
  const v = Dec.tryFrom(amount)
  if (!v) return 'нет данных'
  const words: Record<string, string> = { VND: 'донгов', USD: 'долларов', RUB: 'рублей' }
  const n = withGroups(digitsFor(v.abs(), currency)).replaceAll(NBSP, ' ')
  return `${v.isNeg() ? 'минус ' : ''}${n} ${words[currency] ?? currency}`
}

// ------------------------------------------------------------------------------ dates

export const DEFAULT_TZ = 'Asia/Ho_Chi_Minh'
const WD_SHORT = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб']
const WD_LONG = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']
const MON_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
const MON_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
const MON_NOM = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']

interface LocalParts { y: number; m: number; d: number; hh: number; mm: number }

const dtfCache = new Map<string, Intl.DateTimeFormat>()
function dtf(tz: string): Intl.DateTimeFormat {
  let f = dtfCache.get(tz)
  if (!f) {
    f = new Intl.DateTimeFormat('en-CA', {
      timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    })
    dtfCache.set(tz, f)
  }
  return f
}

/** Wall-clock parts of an instant in the user's timezone (never the browser's). */
export function localParts(instant: Date | string, tz = DEFAULT_TZ): LocalParts {
  const d = typeof instant === 'string' ? new Date(instant) : instant
  const parts = Object.fromEntries(dtf(tz).formatToParts(d).map((p) => [p.type, p.value]))
  return { y: +parts.year, m: +parts.month, d: +parts.day, hh: +parts.hour % 24, mm: +parts.minute }
}

const pad = (n: number) => String(n).padStart(2, '0')

/** 'YYYY-MM-DD' of the instant in tz. */
export function localDateISO(instant: Date | string = new Date(), tz = DEFAULT_TZ): string {
  const p = localParts(instant, tz)
  return `${p.y}-${pad(p.m)}-${pad(p.d)}`
}

/** Calendar arithmetic on 'YYYY-MM-DD' (UTC-noon based, DST-proof). */
export function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number)
  const t = new Date(Date.UTC(y, m - 1, d, 12) + days * 86_400_000)
  return `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())}`
}

function weekday(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d, 12)).getUTCDay()
}

export function formatTime(instant: string, tz = DEFAULT_TZ): string {
  const p = localParts(instant, tz)
  return `${pad(p.hh)}:${pad(p.mm)}`
}

/** «пт, 24 окт» (current year) or «24 окт 2025». */
export function formatShortDate(iso: string, now: Date = new Date(), tz = DEFAULT_TZ): string {
  const [y, m, d] = iso.split('-').map(Number)
  const thisYear = localParts(now, tz).y
  if (y !== thisYear) return `${d} ${MON_SHORT[m - 1]} ${y}`
  return `${WD_SHORT[weekday(iso)]}, ${d} ${MON_SHORT[m - 1]}`
}

/** «сегодня, 14:32», «вчера, 09:10», «пт, 24 окт», «24 окт 2025». */
export function formatRelative(instant: string, now: Date = new Date(), tz = DEFAULT_TZ): string {
  const day = localDateISO(instant, tz)
  const today = localDateISO(now, tz)
  const time = formatTime(instant, tz)
  if (day === today) return `сегодня, ${time}`
  if (day === addDays(today, -1)) return `вчера, ${time}`
  return formatShortDate(day, now, tz)
}

/** Day group header: «Сегодня», «Вчера», «Пятница, 24 октября» (+ year if not current). */
export function formatDayHeader(iso: string, now: Date = new Date(), tz = DEFAULT_TZ): string {
  const today = localDateISO(now, tz)
  if (iso === today) return 'Сегодня'
  if (iso === addDays(today, -1)) return 'Вчера'
  const [y, m, d] = iso.split('-').map(Number)
  const base = `${WD_LONG[weekday(iso)]}, ${d} ${MON_GEN[m - 1]}`
  return y === localParts(now, tz).y ? base : `${base} ${y}`
}

/** «Октябрь 2026». */
export function formatMonth(iso: string): string {
  const [y, m] = iso.split('-').map(Number)
  return `${MON_NOM[m - 1]} ${y}`
}

/** «21 окт» */
export function formatDayMonth(iso: string): string {
  const [, m, d] = iso.split('-').map(Number)
  return `${d} ${MON_SHORT[m - 1]}`
}

/** Russian plural: plural(3, ['операция','операции','операций']). */
export function plural(n: number, forms: [string, string, string]): string {
  const a = Math.abs(n) % 100
  const b = a % 10
  if (a > 10 && a < 20) return forms[2]
  if (b > 1 && b < 5) return forms[1]
  if (b === 1) return forms[0]
  return forms[2]
}
