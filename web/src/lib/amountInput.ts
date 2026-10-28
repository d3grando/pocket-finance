// Parse amounts typed by the user in forms (onboarding, manual add):
// «34 120 000», «2.5м», «800к», «1,5 млн», «85» (→ 85 000 ₫ when small_amount_thousands),
// and multi-asset lists «312.4 USDT, 3 TON». Same rules as the backend parser (ARCH §4.13).
// Result amounts are canonical decimal strings — no float anywhere.
import { Dec } from './decimal'

const MULT: [RegExp, string][] = [
  [/^(млрд|b|bn)$/i, '1000000000'],
  [/^(м|m|млн|мл|kk|mln|mio)$/i, '1000000'],
  [/^(к|k|тыс|тыщ|т)$/i, '1000'],
]

const ASSET_ALIASES: Record<string, string> = {
  '₫': 'VND', 'đ': 'VND', 'донг': 'VND', 'донгов': 'VND', 'донга': 'VND', vnd: 'VND',
  '$': 'USD', 'usd': 'USD', 'долл': 'USD', 'доллар': 'USD', 'долларов': 'USD', 'доллара': 'USD', 'бакс': 'USD', 'баксов': 'USD', 'бакса': 'USD',
  '₽': 'RUB', 'руб': 'RUB', 'рубль': 'RUB', 'рублей': 'RUB', 'рубля': 'RUB', 'р': 'RUB', 'rub': 'RUB',
  'тон': 'TON', 'тона': 'TON', 'тонов': 'TON', 'ton': 'TON',
  'юсдт': 'USDT', 'usdt': 'USDT', 'тезер': 'USDT',
  'btc': 'BTC', 'биток': 'BTC', 'битка': 'BTC', 'eth': 'ETH', 'эфир': 'ETH', 'эфира': 'ETH',
  'usdc': 'USDC', 'sol': 'SOL', 'trx': 'TRX', 'bnb': 'BNB',
}

export function detectAsset(token: string): string | null {
  const t = token.trim().toLowerCase().replace(/\.$/, '')
  if (!t) return null
  if (ASSET_ALIASES[t]) return ASSET_ALIASES[t]
  if (/^[a-z0-9]{2,10}$/.test(t)) return t.toUpperCase()
  return null
}

export interface ParsedAmount { amount: string; asset: string | null }

/** «1,5» → 1.5; «1,500,000» / «1.500.000» → grouping; «1,500.25» → 1500.25; else null. */
export function normalizeNumber(raw: string): string | null {
  let n = raw
  const sign = n.startsWith('-') || n.startsWith('+') ? n[0] : ''
  if (sign) n = n.slice(1)
  const commas = (n.match(/,/g) ?? []).length
  const dots = (n.match(/\./g) ?? []).length
  const grouped = /^\d{1,3}(,\d{3})+$/
  if (commas && dots) n = n.replace(/,/g, '')
  else if (commas > 1 || grouped.test(n)) {
    if (!grouped.test(n)) return null
    n = n.replace(/,/g, '')
  } else if (commas === 1) n = n.replace(',', '.')
  else if (dots > 1) {
    if (!/^\d{1,3}(\.\d{3})+$/.test(n)) return null
    n = n.replace(/\./g, '')
  }
  if (!/^\d+(\.\d+)?$/.test(n)) return null
  return `${sign === '-' ? '-' : ''}${n}`
}

const AMOUNT_RE = /^([-+]?\s*(?:\d[\d\s\u00A0\u202F',.]*)?\d)\s*([a-zа-яё]+\.?)?$/i

/**
 * Parse one amount. `currency` is the expected asset (for the «<1000 ₫ = thousands» rule).
 * Returns null when the text is not an amount.
 */
export function parseAmountInput(raw: string, currency: string | null = 'VND', smallThousands = true): ParsedAmount | null {
  let text = raw.trim().replace(/−/g, '-')
  if (!text) return null
  let asset: string | null = null
  // leading/trailing currency symbol: «$15», «15$», «15 ₫»
  const sym = /^([$₫₽€])\s*(.+)$/.exec(text) ?? /^(.+?)\s*([$₫₽€đ])$/.exec(text)
  if (sym) {
    const [a, b] = /^[$₫₽€]$/.test(sym[1]) ? [sym[1], sym[2]] : [sym[2], sym[1]]
    asset = detectAsset(a)
    text = b.trim()
  }
  // trailing ticker word «312.4 USDT», «3 тона»
  const words = text.split(/\s+/)
  if (words.length > 1) {
    const last = words[words.length - 1]
    const lastAsset = /\d/.test(last) || MULT.some(([re]) => re.test(last)) ? null : detectAsset(last)
    if (lastAsset) { asset = lastAsset; text = words.slice(0, -1).join(' ') }
  }
  const m = AMOUNT_RE.exec(text)
  if (!m) return null
  const suffix = m[2]?.replace(/\.$/, '') ?? ''
  const num = normalizeNumber(m[1].replace(/[\s\u00A0\u202F']/g, ''))
  if (num === null) return null
  let value = Dec.tryFrom(num)
  if (!value) return null
  let multiplied = false
  if (suffix) {
    const mul = MULT.find(([re]) => re.test(suffix))
    if (mul) { value = value.mul(mul[1]); multiplied = true }
    else {
      const a = detectAsset(suffix)
      if (!a) return null
      asset = a
    }
  }
  const effective = asset ?? currency
  if (!multiplied && smallThousands && effective === 'VND' && value.abs().cmp(1000) < 0 && !value.isZero() && !num.includes('.')) {
    value = value.mul(1000)
  }
  return { amount: value.toString(), asset }
}

/** «312.4 USDT, 3 TON» → [{amount:"312.4",asset:"USDT"},{amount:"3",asset:"TON"}]. */
export function parseAssetList(raw: string, defaultAsset: string | null = null, smallThousands = true): ParsedAmount[] | null {
  const parts = raw.split(/[,;]|\s+и\s+|\s*\+\s*/).map((p) => p.trim()).filter(Boolean)
  if (!parts.length) return null
  const out: ParsedAmount[] = []
  for (const p of parts) {
    const r = parseAmountInput(p, defaultAsset, smallThousands)
    if (!r) return null
    const asset = r.asset ?? defaultAsset
    if (!asset) return null
    out.push({ amount: r.amount, asset })
  }
  return out
}
