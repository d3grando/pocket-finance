// Money / number / date formatting for the pages (DESIGN.md §3).
// Uses the shell's shared `@/lib/format` when it is implemented; until then (or if it throws) falls back
// to the local implementation below, which follows the same DESIGN §3 examples.
import * as shared from '@/lib/format'
import { toUnits } from './decimal'

export const NBSP = '\u00a0'
export const MINUS = '−'

export interface MoneyOpts {
  sign?: 'auto' | 'always' | 'never'
  approx?: boolean
}

const FIAT_2 = new Set(['EUR', 'GBP', 'THB', 'CNY', 'JPY', 'KRW', 'SGD', 'AUD', 'CAD', 'CHF', 'KZT', 'UAH', 'TRY', 'AED', 'INR'])
const PREFIX: Record<string, string> = { USD: '$', EUR: '€', GBP: '£' }
const SUFFIX: Record<string, string> = { VND: '₫', RUB: '₽' }
export const CURRENCY_SYMBOL: Record<string, string> = { VND: '₫', RUB: '₽', USD: '$', EUR: '€', GBP: '£' }

function groupInt(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP)
}

/** Round |units| (scale 1e8) half-even to `d` decimals → [int, frac] strings. */
function roundUnits(abs: bigint, d: number): [string, string] {
  const div = 10n ** BigInt(8 - d)
  let q = abs / div
  const r = abs % div
  const half = div / 2n
  if (div > 1n && (r > half || (r === half && q % 2n === 1n))) q += 1n
  const p = 10n ** BigInt(d)
  const int = (q / p).toString()
  const frac = d > 0 ? (q % p).toString().padStart(d, '0') : ''
  return [int, frac]
}

function decimalsFor(cur: string, abs: bigint): { d: number; trim: boolean; intOnlyIfWhole?: boolean } {
  if (cur === 'VND') return { d: 0, trim: false }
  if (cur === 'USD' || FIAT_2.has(cur)) return { d: 2, trim: false }
  if (cur === 'RUB' || cur === 'USDT' || cur === 'USDC') {
    const whole = abs % 10n ** 8n === 0n
    return { d: whole ? 0 : 2, trim: false }
  }
  return { d: 8, trim: true }
}

export function localFormatMoney(amount: string | null | undefined, currency: string, opts: MoneyOpts = {}): string {
  if (amount === null || amount === undefined || amount === '') return '—'
  const cur = (currency || '').toUpperCase()
  const u = toUnits(amount)
  const neg = u < 0n
  const abs = neg ? -u : u
  const { d, trim } = decimalsFor(cur, abs)
  const [int, frac0] = roundUnits(abs, d)
  let frac = frac0
  if (cur === 'RUB' && frac && /^0+$/.test(frac)) frac = ''
  if (trim) frac = frac.replace(/0+$/, '')
  const isZero = /^0*$/.test(int + frac)
  const num = groupInt(int) + (frac ? `.${frac}` : '')
  const mode = opts.sign ?? 'auto'
  let sign = ''
  if (mode !== 'never' && !isZero) {
    if (neg) sign = MINUS
    else if (mode === 'always') sign = '+'
  }
  const body = PREFIX[cur] ? `${sign}${PREFIX[cur]}${num}` : `${sign}${num}${NBSP}${SUFFIX[cur] ?? cur}`
  return opts.approx ? `≈${NBSP}${body}` : body
}

function compactParts(amount: string, cur: string): { sign: string; num: string; unit: string } {
  const u = toUnits(amount)
  const neg = u < 0n
  const v = Math.abs(Number(String(amount).replace(MINUS, '-')))
  const latin = !!PREFIX[cur]
  let num: string
  let unit = ''
  const fmt = (x: number, dec: number) => {
    const s = x.toFixed(dec)
    return s.includes('.') ? s.replace(/\.?0+$/, '') : s
  }
  if (v >= 1e9) {
    num = fmt(v / 1e9, 2)
    unit = latin ? 'b' : 'млрд'
  } else if (v >= 1e6) {
    num = fmt(v / 1e6, 2)
    unit = latin ? 'm' : 'м'
  } else if (v >= 1e3) {
    num = fmt(v / 1e3, latin ? 1 : 1)
    unit = latin ? 'k' : 'к'
  } else {
    num = fmt(v, cur === 'VND' ? 0 : 2)
  }
  return { sign: neg && v !== 0 ? MINUS : '', num, unit }
}

export function localFormatCompact(amount: string | null | undefined, currency: string): string {
  if (amount === null || amount === undefined || amount === '') return '—'
  const cur = (currency || '').toUpperCase()
  const { sign, num, unit } = compactParts(amount, cur)
  if (PREFIX[cur]) return `${sign}${PREFIX[cur]}${num}${unit}`
  return `${sign}${num}${unit}${NBSP}${SUFFIX[cur] ?? cur}`
}

/** Axis ticks: «450к», «1.2м», «$1.2k». */
export function formatAxis(value: number, currency: string): string {
  const cur = currency.toUpperCase()
  const { sign, num, unit } = compactParts(String(value), cur)
  return PREFIX[cur] ? `${sign}${PREFIX[cur]}${num}${unit}` : `${sign}${num}${unit}`
}

export function formatMoney(amount: string | null | undefined, currency: string, opts: MoneyOpts = {}): string {
  if (amount === null || amount === undefined || amount === '') return '—'
  try {
    return shared.formatMoney(amount, currency, opts)
  } catch {
    return localFormatMoney(amount, currency, opts)
  }
}

export function formatCompact(amount: string | null | undefined, currency: string): string {
  if (amount === null || amount === undefined || amount === '') return '—'
  try {
    return shared.formatCompact(amount, currency)
  } catch {
    return localFormatCompact(amount, currency)
  }
}

/** "0.183" → "18%"; |x| < 10% keeps one decimal ("4.5%"); sign "always" adds +/−. */
export function formatPercent(ratio: string | null | undefined, opts: { sign?: 'auto' | 'always' } = {}): string {
  if (ratio === null || ratio === undefined || ratio === '') return '—'
  const u = toUnits(ratio) * 100n
  const neg = u < 0n
  const abs = neg ? -u : u
  const small = abs < 10n * 10n ** 8n
  const [int, frac0] = roundUnits(abs, small ? 1 : 0)
  const frac = frac0 === '0' ? '' : frac0
  const isZero = /^0*$/.test(int + frac)
  const sign = isZero ? '' : neg ? MINUS : opts.sign === 'always' ? '+' : ''
  return `${sign}${int}${frac ? `.${frac}` : ''}%`
}

export function plural(n: number, forms: [string, string, string]): string {
  const a = Math.abs(n) % 100
  const b = a % 10
  if (a > 10 && a < 20) return forms[2]
  if (b > 1 && b < 5) return forms[1]
  if (b === 1) return forms[0]
  return forms[2]
}

export const countLabel = (n: number, forms: [string, string, string]) => `${n.toLocaleString('ru-RU').replace(/\s/g, NBSP)} ${plural(n, forms)}`

/** Screen-reader currency words (DESIGN §15). */
export function currencyWord(cur: string): string {
  return ({ VND: 'донгов', USD: 'долларов', RUB: 'рублей', USDT: 'USDT', TON: 'TON' } as Record<string, string>)[cur] ?? cur
}
