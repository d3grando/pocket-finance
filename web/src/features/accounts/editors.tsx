// Account property editors (aliases, default currencies, card fee, name/kind/icon). Used by the account
// panel and by Settings → Счета. PATCH /accounts/{id} with `version` (409 → refetch + message).
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { api, type AccountKind, type AccountOut, type AccountPatch } from '@/lib/api'
import { errorMessage, invalidateEntities } from '@/features/operations/api'
import { ACCOUNT_ICON_CHOICES, iconByName } from '@/features/operations/ui/icons'
import { Button, Chip, Field, Input, NativeSelect } from '@/features/operations/ui/kit'
import { notify } from '@/features/operations/ui/overlays'
import { KIND_LABEL } from './forms'

export function usePatchAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: AccountPatch }) => api.patch<AccountOut>(`/accounts/${id}`, patch),
    onSuccess: () => invalidateEntities(qc, ['accounts']),
    onError: (e) => {
      invalidateEntities(qc, ['accounts'])
      notify.error(errorMessage(e, 'Не удалось сохранить счёт'))
    },
  })
}

export function AliasEditor({ account, compact }: { account: AccountOut; compact?: boolean }) {
  const [draft, setDraft] = useState('')
  const patch = usePatchAccount()
  const save = (aliases: string[]) => patch.mutate({ id: account.id, patch: { version: account.version, aliases } })
  const add = () => {
    const v = draft.trim().toLowerCase()
    if (!v) return
    if (!account.aliases.includes(v)) save([...account.aliases, v])
    setDraft('')
  }
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap gap-1.5" aria-label={`Псевдонимы ${account.name}`}>
        {account.aliases.length === 0 && <span className="text-sm text-muted-foreground">Псевдонимов нет</span>}
        {account.aliases.map((a) => (
          <span key={a} className="inline-flex h-7 items-center gap-1 rounded-sm bg-muted pr-1 pl-2.5 text-[13px]">
            {a}
            <button type="button" aria-label={`Удалить псевдоним ${a}`} disabled={patch.isPending} onClick={() => save(account.aliases.filter((x) => x !== a))} className="relative grid size-6 place-items-center rounded-xs text-muted-foreground after:absolute after:-inset-1 after:content-[''] hover:bg-surface-2 hover:text-foreground">
              <X className="size-3" />
            </button>
          </span>
        ))}
      </div>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          add()
        }}
      >
        <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={compact ? 'новый псевдоним' : 'например «налом», «Кошелёк» — Enter'} aria-label="Новый псевдоним" maxLength={40} className="h-8" />
        <Button type="submit" size="sm" disabled={!draft.trim()} loading={patch.isPending}>
          Добавить
        </Button>
      </form>
    </div>
  )
}

const DEFAULT_CHOICES = ['VND', 'RUB', 'USD', 'USDT']
const CUR_SIGN: Record<string, string> = { VND: '₫', RUB: '₽', USD: '$', USDT: 'USDT' }

/** «По умолчанию для ₫/₽»: one currency cannot be default for two accounts (backend → 422). */
export function DefaultCurrencies({ account, accounts }: { account: AccountOut; accounts: AccountOut[] }) {
  const patch = usePatchAccount()
  const takenBy = (c: string) => accounts.find((a) => a.id !== account.id && !a.is_archived && a.default_for_currencies.includes(c))
  const toggle = (c: string) => {
    const on = account.default_for_currencies.includes(c)
    patch.mutate({ id: account.id, patch: { version: account.version, default_for_currencies: on ? account.default_for_currencies.filter((x) => x !== c) : [...account.default_for_currencies, c] } })
  }
  const choices = account.is_multi_asset ? DEFAULT_CHOICES : DEFAULT_CHOICES.filter((c) => c === account.currency || c === 'VND' || c === 'RUB')
  return (
    <div className="grid gap-1.5">
      <div className="flex flex-wrap gap-1.5">
        {choices.map((c) => {
          const other = takenBy(c)
          return (
            <Chip key={c} active={account.default_for_currencies.includes(c)} onClick={() => toggle(c)} disabled={patch.isPending} title={other ? `Сейчас по умолчанию: ${other.name}` : undefined}>
              {CUR_SIGN[c] ?? c}
              {other && <span className="text-xs text-muted-foreground">· {other.name}</span>}
            </Chip>
          )
        })}
      </div>
      <p className="text-xs text-muted-foreground">Траты в этой валюте без указания счёта спишутся отсюда. Одна валюта — один счёт.</p>
    </div>
  )
}

type WithFee = AccountOut & { card_fee_percent?: string | null }

/** Card fee %: fixed 0% (debit by Vietcombank sell rate). Editable only once the backend exposes `card_fee_percent`. */
export function CardFee({ account }: { account: AccountOut }) {
  const a = account as WithFee
  const supported = Object.prototype.hasOwnProperty.call(a, 'card_fee_percent')
  const [value, setValue] = useState(a.card_fee_percent ?? '0')
  const patch = usePatchAccount()
  if (!supported) {
    return (
      <p className="text-sm">
        <span className="num font-medium">0%</span>
        <span className="text-muted-foreground"> — списание в валюте счёта по курсу продажи Vietcombank, без комиссии</span>
      </p>
    )
  }
  return (
    <form
      className="flex items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        const v = value.replace(',', '.').trim()
        if (!/^\d{1,2}(\.\d{1,2})?$/.test(v)) return notify.error('Комиссия — число от 0 до 99.99')
        patch.mutate({ id: account.id, patch: { version: account.version, card_fee_percent: v } as AccountPatch })
      }}
    >
      <Field label="Комиссия, %" htmlFor={`fee-${account.id}`}>
        <Input id={`fee-${account.id}`} inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} className="num h-8 w-24" />
      </Field>
      <Button type="submit" size="sm" loading={patch.isPending}>
        Сохранить
      </Button>
    </form>
  )
}

export function AccountBasics({ account }: { account: AccountOut }) {
  const [name, setName] = useState(account.name)
  const patch = usePatchAccount()
  const changed = name.trim() && name.trim() !== account.name
  return (
    <div className="grid gap-3">
      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (changed) patch.mutate({ id: account.id, patch: { version: account.version, name: name.trim() } })
        }}
      >
        <Field label="Название" htmlFor={`an-${account.id}`} className="flex-1">
          <Input id={`an-${account.id}`} value={name} onChange={(e) => setName(e.target.value)} maxLength={64} className="h-8" />
        </Field>
        <Button type="submit" size="sm" disabled={!changed} loading={patch.isPending}>
          Переименовать
        </Button>
      </form>
      <Field label="Тип" htmlFor={`ak-${account.id}`}>
        <NativeSelect id={`ak-${account.id}`} value={account.kind} onChange={(e) => patch.mutate({ id: account.id, patch: { version: account.version, kind: e.target.value as AccountKind } })} className="h-8">
          {Object.entries(KIND_LABEL).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field label="Иконка">
        <div className="flex flex-wrap gap-1.5">
          {ACCOUNT_ICON_CHOICES.map((n) => {
            const I = iconByName(n)
            return (
              <Chip key={n} active={account.icon === n} aria-label={n} onClick={() => patch.mutate({ id: account.id, patch: { version: account.version, icon: n } })} className="w-9 justify-center px-0">
                <I />
              </Chip>
            )
          })}
        </div>
      </Field>
    </div>
  )
}
