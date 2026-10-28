// Money formatting for the desktop pages (DESIGN.md §3; same rules as web/src/lib/format.ts).
// Amounts arrive as Decimal strings; formatting is pure string work — no float anywhere.

const NBSP = ' '
const MINUS = '−'

interface Parsed {
  neg: boolean
  int: string // digits, no leading zeros (except "0")
  frac: string // digits, may be ''
}

function parseDecimal(value: string): Parsed | null {
  const m = /^\s*([+-]?)(\d*)(?:\.(\d*))?\s*$/.exec(String(value))
  if (!m || (m[2] === '' && (m[3] ?? '') === '')) return null
  const int = (m[2] || '0').replace(/^0+(?=\d)/, '')
  return { neg: m[1] === '-', int, frac: m[3] ?? '' }
}

/** Round half-even to `digits` decimals on the digit strings. */
function roundTo(p: Parsed, digits: number): Parsed {
  if (p.frac.length <= digits) return { ...p, frac: p.frac.padEnd(digits, '0') }
  const keep = p.frac.slice(0, digits)
  const rest = p.frac.slice(digits)
  const first = Number(rest[0])
  const tail = rest.slice(1).replace(/0+$/, '')
  const lastKept = Number((digits ? keep : p.int).slice(-1))
  const up = first > 5 || (first === 5 && (tail.length > 0 || lastKept % 2 === 1))
  let digitsStr = p.int + keep
  if (up) {
    const arr = digitsStr.split('').map(Number)
    let i = arr.length - 1
    while (i >= 0) {
      if (arr[i] === 9) {
        arr[i] = 0
        i--
      } else {
        arr[i]++
        break
      }
    }
    if (i < 0) arr.unshift(1)
    digitsStr = arr.join('')
  }
  const int = digitsStr.slice(0, digitsStr.length - digits) || '0'
  return { neg: p.neg, int: int.replace(/^0+(?=\d)/, ''), frac: digitsStr.slice(digitsStr.length - digits) }
}

function group(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP)
}

function isZero(p: Parsed): boolean {
  return /^0*$/.test(p.int) && /^0*$/.test(p.frac)
}

export interface MoneyOpts {
  sign?: 'auto' | 'always' | 'never'
  approx?: boolean
}

const SYMBOL_SUFFIX: Record<string, string> = { VND: '₫', RUB: '₽' }

export function formatMoney(value: string | null | undefined, currency: string, opts: MoneyOpts = {}): string {
  if (value === null || value === undefined) return '—'
  const p0 = parseDecimal(value)
  if (!p0) return '—'
  const cur = currency.toUpperCase()
  let p: Parsed
  if (cur === 'VND') p = roundTo(p0, 0)
  else if (cur === 'RUB') {
    const r = roundTo(p0, 2)
    p = /^0*$/.test(r.frac) ? { ...r, frac: '' } : r
  } else if (cur === 'USD' || cur === 'USDT' || cur === 'USDC' || cur === 'EUR') {
    p = roundTo(p0, 2)
    if (cur !== 'USD' && cur !== 'EUR' && /^0*$/.test(p.frac)) p = { ...p, frac: '' }
  } else {
    const r = roundTo(p0, 8)
    p = { ...r, frac: r.frac.replace(/0+$/, '') }
  }
  const zero = isZero(p)
  const sign = opts.sign ?? 'auto'
  let s = ''
  if (!zero && p.neg && sign !== 'never') s = MINUS
  else if (!zero && !p.neg && sign === 'always') s = '+'
  const num = group(p.int) + (p.frac ? `.${p.frac}` : '')
  let out: string
  if (cur === 'USD') out = `${s}$${num}`
  else if (cur === 'EUR') out = `${s}€${num}`
  else out = `${s}${num}${NBSP}${SYMBOL_SUFFIX[cur] ?? cur}`
  return opts.approx ? `≈${NBSP}${out}` : out
}

/** Compact VND: 1250000 → "1.25м ₫", 450000 → "450к ₫"; other currencies fall back to formatMoney. */
export function formatCompact(value: string | null | undefined, currency: string, opts: MoneyOpts = {}): string {
  if (value === null || value === undefined) return '—'
  const p0 = parseDecimal(value)
  if (!p0 || currency.toUpperCase() !== 'VND') return formatMoney(value, currency, opts)
  const p = roundTo(p0, 0)
  const sign = opts.sign ?? 'auto'
  const zero = isZero(p)
  const s = !zero && p.neg && sign !== 'never' ? MINUS : !zero && !p.neg && sign === 'always' ? '+' : ''
  const digits = p.int.length
  let body: string
  if (digits > 6) {
    // millions with up to 2 decimals
    const r = roundTo({ neg: false, int: p.int.slice(0, digits - 6), frac: p.int.slice(digits - 6) }, 2)
    body = `${group(r.int)}${r.frac.replace(/0+$/, '') ? `.${r.frac.replace(/0+$/, '')}` : ''}м`
  } else if (digits > 3) {
    const r = roundTo({ neg: false, int: p.int.slice(0, digits - 3), frac: p.int.slice(digits - 3) }, digits === 4 ? 1 : 0)
    if (r.int.length > 3) body = '1м'
    else body = `${r.int}${r.frac.replace(/0+$/, '') ? `.${r.frac.replace(/0+$/, '')}` : ''}к`
  } else body = p.int
  const out = `${s}${body}${NBSP}₫`
  return opts.approx ? `≈${NBSP}${out}` : out
}

/** "14:32" / "вчера 22:40" / "24 окт" in the user's zone (Asia/Ho_Chi_Minh). */
export function formatWhen(iso: string, now: Date = new Date(), timeZone = 'Asia/Ho_Chi_Minh'): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const day = (x: Date) => new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(x)
  const time = new Intl.DateTimeFormat('ru-RU', { timeZone, hour: '2-digit', minute: '2-digit', hour12: false }).format(d)
  const today = day(now)
  const yesterday = day(new Date(now.getTime() - 86_400_000))
  const dd = day(d)
  if (dd === today) return time
  if (dd === yesterday) return `вчера ${time}`
  return new Intl.DateTimeFormat('ru-RU', { timeZone, day: 'numeric', month: 'short' }).format(d).replace('.', '')
}
