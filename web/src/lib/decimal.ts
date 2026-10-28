// Tiny fixed-point decimal on BigInt — money never touches float (// «Деньги хранятся в Decimal, float нигде нет»). Values are normalised to SCALE fractional digits.

const SCALE = 12
const ONE = 10n ** BigInt(SCALE)

/** Immutable decimal: value = units / 10^SCALE. */
export class Dec {
  private constructor(readonly units: bigint) {}

  static readonly ZERO = new Dec(0n)

  /** Internal: build from raw scaled units. */
  static fromUnits(units: bigint): Dec { return new Dec(units) }

  static from(v: string | number | bigint | Dec): Dec {
    if (v instanceof Dec) return v
    if (typeof v === 'bigint') return new Dec(v * ONE)
    if (typeof v === 'number') {
      if (!Number.isInteger(v)) throw new Error('Dec.from(number) accepts integers only')
      return new Dec(BigInt(v) * ONE)
    }
    const p = parseDecimal(v)
    if (!p) throw new Error(`Not a decimal: ${v}`)
    return p
  }

  static tryFrom(v: string | null | undefined): Dec | null {
    if (v == null) return null
    return parseDecimal(v)
  }

  add(o: Dec | string | number): Dec { return new Dec(this.units + Dec.from(o).units) }
  sub(o: Dec | string | number): Dec { return new Dec(this.units - Dec.from(o).units) }
  mul(o: Dec | string | number): Dec { return new Dec(divRoundHalfEven(this.units * Dec.from(o).units, ONE)) }
  div(o: Dec | string | number): Dec {
    const d = Dec.from(o).units
    if (d === 0n) throw new Error('Division by zero')
    return new Dec(divRoundHalfEven(this.units * ONE, d))
  }
  neg(): Dec { return new Dec(-this.units) }
  abs(): Dec { return this.units < 0n ? this.neg() : this }
  cmp(o: Dec | string | number): -1 | 0 | 1 {
    const b = Dec.from(o).units
    return this.units < b ? -1 : this.units > b ? 1 : 0
  }
  isZero(): boolean { return this.units === 0n }
  isNeg(): boolean { return this.units < 0n }
  isPos(): boolean { return this.units > 0n }

  /** Round half-even to `places` fractional digits. */
  round(places: number): Dec {
    if (places >= SCALE) return this
    const f = 10n ** BigInt(SCALE - places)
    return new Dec(divRoundHalfEven(this.units, f) * f)
  }

  /** Plain string: integer part + up to `places` digits (trailing zeros trimmed unless `fixed`). */
  toFixedString(places: number, fixed = false): string {
    const r = this.round(places)
    const neg = r.units < 0n
    const abs = neg ? -r.units : r.units
    const int = abs / ONE
    let frac = (abs % ONE).toString().padStart(SCALE, '0').slice(0, places)
    if (!fixed) frac = frac.replace(/0+$/, '')
    const s = frac ? `${int}.${frac}` : `${int}`
    return neg && s !== '0' ? `-${s}` : s
  }

  /** Canonical API string (no trailing zeros), e.g. "2500000", "312.4". */
  toString(): string { return this.toFixedString(SCALE) }
}

function divRoundHalfEven(n: bigint, d: bigint): bigint {
  if (d < 0n) { n = -n; d = -d }
  const q = n / d
  const r = n % d
  if (r === 0n) return q
  const twice = (r < 0n ? -r : r) * 2n
  const away = n < 0n ? q - 1n : q + 1n
  if (twice > d) return away
  if (twice < d) return q
  return q % 2n === 0n ? q : away
}

const DEC_RE = /^([+\-−]?)(\d*)(?:\.(\d*))?$/

/** Parse "1250000", "-12.4", "0.02150000", "+3", "−5" → Dec. Commas/spaces are NOT accepted here. */
export function parseDecimal(s: string): Dec | null {
  const t = s.trim()
  const m = DEC_RE.exec(t)
  if (!m) return null
  const [, sign, int = '', frac = ''] = m
  if (!int && !frac) return null
  const f = frac.slice(0, SCALE).padEnd(SCALE, '0')
  let units = BigInt(int || '0') * ONE + BigInt(f || '0')
  if (frac.length > SCALE) {
    // round the cut-off digits half-even
    const rest = frac.slice(SCALE)
    const first = Number(rest[0])
    const tail = /[1-9]/.test(rest.slice(1))
    if (first > 5 || (first === 5 && (tail || units % 2n === 1n))) units += 1n
  }
  if (sign === '-' || sign === '−') units = -units
  return Dec.fromUnits(units)
}

/** Sum of decimal strings (nulls skipped). */
export function sumDec(values: (string | null | undefined)[]): Dec {
  return values.reduce<Dec>((acc, v) => (v == null ? acc : acc.add(Dec.from(v))), Dec.ZERO)
}
