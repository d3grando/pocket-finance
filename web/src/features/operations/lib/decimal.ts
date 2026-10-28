// Exact decimal-string helpers (pages). Money arrives as Decimal strings (API.md §0);
// we never use float for money. BigInt scaled by 1e8 covers NUMERIC(28,8) exactly.
// Number() is used only for chart geometry (bar heights, pie angles), never for displayed sums.

const SCALE = 8
const F = 10n ** BigInt(SCALE)

const DEC_RE = /^([-+−]?)(\d*)(?:\.(\d*))?$/

/** Decimal string → scaled BigInt units (truncates beyond 8 decimals). Invalid → 0n. */
export function toUnits(s: string | null | undefined): bigint {
  if (s === null || s === undefined) return 0n
  const m = DEC_RE.exec(String(s).trim())
  if (!m || (m[2] === '' && (m[3] ?? '') === '')) return 0n
  const neg = m[1] === '-' || m[1] === '−'
  const int = BigInt(m[2] || '0')
  const frac = BigInt(((m[3] ?? '') + '0'.repeat(SCALE)).slice(0, SCALE))
  const v = int * F + frac
  return neg ? -v : v
}

/** Scaled units → canonical decimal string without trailing zeros ("12.4", "-3", "0"). */
export function fromUnits(u: bigint): string {
  const neg = u < 0n
  const a = neg ? -u : u
  const int = a / F
  let frac = (a % F).toString().padStart(SCALE, '0').replace(/0+$/, '')
  frac = frac ? `.${frac}` : ''
  return `${neg ? '-' : ''}${int.toString()}${frac}`
}

export function decAdd(...xs: (string | null | undefined)[]): string {
  return fromUnits(xs.reduce<bigint>((acc, x) => acc + toUnits(x), 0n))
}

export function decSub(a: string | null | undefined, b: string | null | undefined): string {
  return fromUnits(toUnits(a) - toUnits(b))
}

export function decNeg(a: string): string {
  return fromUnits(-toUnits(a))
}

export function decAbs(a: string): string {
  const u = toUnits(a)
  return fromUnits(u < 0n ? -u : u)
}

export function decCmp(a: string | null | undefined, b: string | null | undefined): -1 | 0 | 1 {
  const x = toUnits(a)
  const y = toUnits(b)
  return x === y ? 0 : x < y ? -1 : 1
}

export function decSign(a: string | null | undefined): -1 | 0 | 1 {
  return decCmp(a, '0')
}

export function isDecimal(s: string): boolean {
  return /^-?\d+(\.\d+)?$/.test(s)
}

/** Multiply a decimal string by 10^k exactly (k ≥ 0) — for «к»/«м» suffixes. */
export function shiftDecimal(s: string, k: number): string {
  return fromUnits(toUnits(s) * 10n ** BigInt(k))
}

/** Chart geometry only. */
export function decToNumber(s: string | null | undefined): number {
  if (s === null || s === undefined || s === '') return 0
  const n = Number(String(s).replace('−', '-'))
  return Number.isFinite(n) ? n : 0
}

/**
 * Normalise a human-typed amount into a positive decimal string:
 * "150 000" → "150000", "1,5м" → "1500000", "85к" → "85000", "12.40" → "12.4".
 * Returns null when the input is not a positive number.
 */
export function normalizeAmountInput(raw: string): string | null {
  let s = raw.replace(/[\s\u00a0\u202f']/g, '').replace(',', '.').toLowerCase()
  let shift = 0
  const suf = /(млн|kk|кк|к|k|м|m)$/.exec(s)
  if (suf) {
    shift = ['к', 'k'].includes(suf[1]) ? 3 : 6
    s = s.slice(0, -suf[1].length)
  }
  if (!/^\d+(\.\d+)?$/.test(s) && !/^\.\d+$/.test(s)) return null
  const v = shiftDecimal(s, shift)
  if (decSign(v) <= 0) return null
  return v
}
