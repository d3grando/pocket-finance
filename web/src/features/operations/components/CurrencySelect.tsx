// Currency/asset selector: common codes + free «другая» ticker (crypto wallets hold any coin).
import { useState } from 'react'
import { Input, NativeSelect } from '../ui/kit'

export const COMMON_CURRENCIES = ['VND', 'USD', 'USDT', 'RUB', 'TON', 'BTC', 'ETH', 'USDC', 'EUR']
const LABEL: Record<string, string> = { VND: '₫ VND', USD: '$ USD', RUB: '₽ RUB', EUR: '€ EUR' }

export function CurrencySelect({ value, onChange, id, disabled, options = COMMON_CURRENCIES, allowEmpty, emptyLabel = 'Любая' }: { value: string; onChange: (v: string) => void; id?: string; disabled?: boolean; options?: string[]; allowEmpty?: boolean; emptyLabel?: string }) {
  const [custom, setCustom] = useState(false)
  const list = value && !options.includes(value) ? [...options, value] : options
  if (custom) {
    return (
      <Input
        id={id}
        autoFocus
        value={value}
        placeholder="TON"
        maxLength={16}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ''))}
        onBlur={() => setCustom(false)}
        aria-label="Код валюты или монеты"
      />
    )
  }
  return (
    <NativeSelect
      id={id}
      value={value}
      disabled={disabled}
      onChange={(e) => {
        if (e.target.value === '__other') setCustom(true)
        else onChange(e.target.value)
      }}
    >
      {allowEmpty && <option value="">{emptyLabel}</option>}
      {list.map((c) => (
        <option key={c} value={c}>
          {LABEL[c] ?? c}
        </option>
      ))}
      <option value="__other">Другая…</option>
    </NativeSelect>
  )
}
