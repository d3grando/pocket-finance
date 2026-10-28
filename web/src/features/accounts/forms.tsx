// Account operations forms: transfer, exchange, reconcile (Сверка), opening balance, new account.
// Transfers/exchanges are neither income nor expense. POSTs carry an Idempotency-Key bound
// to the body, so a retry after a network error never creates a duplicate.
import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, newIdempotencyKey, type AccountCreate, type AccountKind, type AccountOut, type AdjustmentResult, type MutationResult } from '@/lib/api'
import { errorMessage, invalidateEntities, useToday } from '@/features/operations/api'
import { CurrencySelect } from '@/features/operations/components/CurrencySelect'
import { useUndoToast } from '@/features/operations/components/useUndoToast'
import { addDays } from '@/features/operations/lib/dates'
import { decToNumber, normalizeAmountInput } from '@/features/operations/lib/decimal'
import { formatMoney } from '@/features/operations/lib/format'
import { ACCOUNT_ICON_CHOICES, iconByName } from '@/features/operations/ui/icons'
import { Button, Chip, Field, Input, NativeSelect, Switch } from '@/features/operations/ui/kit'

/** POST with a stable key per identical body. */
function useIdemPost() {
  const ref = useRef<{ body: string; key: string } | null>(null)
  return async <T,>(path: string, body: unknown): Promise<T> => {
    const json = JSON.stringify(body)
    if (!ref.current || ref.current.body !== json) ref.current = { body: json, key: newIdempotencyKey() }
    const r = await api.post<T>(path, body, { idempotencyKey: ref.current.key })
    ref.current = null
    return r
  }
}

/** Assets an account can move: its currency, or the coins it already holds (multi-asset). */
export function accountAssets(a: AccountOut | undefined): string[] {
  if (!a) return []
  if (!a.is_multi_asset && a.currency) return [a.currency]
  return a.assets.map((x) => x.asset)
}

function AccountSelect({ id, accounts, value, onChange, exclude, placeholder }: { id: string; accounts: AccountOut[]; value: string; onChange: (v: string) => void; exclude?: string; placeholder: string }) {
  return (
    <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{placeholder}</option>
      {accounts
        .filter((a) => !a.is_archived && String(a.id) !== exclude)
        .map((a) => (
          <option key={a.id} value={a.id}>
            {a.name}
            {a.is_multi_asset ? ' (кошелёк)' : a.currency ? ` · ${a.currency}` : ''}
          </option>
        ))}
    </NativeSelect>
  )
}

function AssetInput({ id, account, value, onChange }: { id: string; account?: AccountOut; value: string; onChange: (v: string) => void }) {
  const assets = accountAssets(account)
  if (account && !account.is_multi_asset && account.currency) return <Input id={id} value={account.currency} disabled />
  return <CurrencySelect id={id} value={value} onChange={onChange} options={assets.length ? Array.from(new Set([...assets, 'USDT', 'TON'])) : undefined} />
}

function FormError({ err }: { err: string | null }) {
  return err ? (
    <p role="alert" className="rounded-sm bg-expense-soft px-2 py-1.5 text-sm text-destructive">
      {err}
    </p>
  ) : null
}

export function TransferForm({ accounts, onDone, defaultFrom }: { accounts: AccountOut[]; onDone: () => void; defaultFrom?: number }) {
  const qc = useQueryClient()
  const { today } = useToday()
  const post = useIdemPost()
  const undoToast = useUndoToast()
  const [from, setFrom] = useState(defaultFrom ? String(defaultFrom) : '')
  const [to, setTo] = useState('')
  const [amount, setAmount] = useState('')
  const [asset, setAsset] = useState('')
  const [date, setDate] = useState(today)
  const [description, setDescription] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const fromAcc = accounts.find((a) => String(a.id) === from)
  const currency = fromAcc && !fromAcc.is_multi_asset && fromAcc.currency ? fromAcc.currency : asset
  const submit = async () => {
    const amt = normalizeAmountInput(amount)
    if (!from || !to) return setErr('Выберите оба счёта')
    if (from === to) return setErr('Счета должны различаться')
    if (!amt) return setErr('Укажите сумму больше нуля')
    if (!currency) return setErr('Укажите валюту или монету')
    setErr(null)
    setBusy(true)
    try {
      const r = await post<MutationResult>('/transfers', { from_account_id: Number(from), to_account_id: Number(to), amount: amt, currency, description: description.trim() || null, occurred_on: date })
      invalidateEntities(qc, ['transactions', 'accounts', 'stats'])
      undoToast(`Перевод ${formatMoney(amt, currency)} · ${fromAcc?.name} → ${accounts.find((a) => String(a.id) === to)?.name}`, r.action_id)
      onDone()
    } catch (e) {
      setErr(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); void submit() }} aria-label="Перевод между счетами">
      <div className="grid grid-cols-2 gap-2">
        <Field label="Откуда" htmlFor="tr-from">
          <AccountSelect id="tr-from" accounts={accounts} value={from} onChange={setFrom} placeholder="Счёт" />
        </Field>
        <Field label="Куда" htmlFor="tr-to">
          <AccountSelect id="tr-to" accounts={accounts} value={to} onChange={setTo} exclude={from} placeholder="Счёт" />
        </Field>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_120px] gap-2">
        <Field label="Сумма" htmlFor="tr-amount">
          <Input id="tr-amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} className="num" placeholder="0" />
        </Field>
        <Field label="Валюта" htmlFor="tr-asset">
          <AssetInput id="tr-asset" account={fromAcc} value={asset} onChange={setAsset} />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Дата" htmlFor="tr-date">
          <Input id="tr-date" type="date" value={date} max={addDays(today, 1)} onChange={(e) => setDate(e.target.value)} />
        </Field>
        <Field label="Комментарий" htmlFor="tr-desc">
          <Input id="tr-desc" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} />
        </Field>
      </div>
      <p className="text-xs text-muted-foreground">Перевод между своими счетами — не доход и не расход.</p>
      <FormError err={err} />
      <Button type="submit" variant="primary" loading={busy}>
        Перевести
      </Button>
    </form>
  )
}

/** «1 USDT = 0.998 USDT» — display only. */
export function rateLabel(amount: string, cur: string, got: string, gotCur: string): string | null {
  const a = decToNumber(amount)
  const b = decToNumber(got)
  if (!a || !b) return null
  const r = b / a
  const s = r >= 100 ? r.toFixed(0) : r >= 1 ? r.toFixed(4).replace(/\.?0+$/, '') : r.toPrecision(4).replace(/\.?0+$/, '')
  return `1 ${cur} = ${s.replace(/\B(?=(\d{3})+(?!\d))/g, '\u00a0')} ${gotCur}`
}

export function ExchangeForm({ accounts, onDone }: { accounts: AccountOut[]; onDone: () => void }) {
  const qc = useQueryClient()
  const { today } = useToday()
  const post = useIdemPost()
  const undoToast = useUndoToast()
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [amount, setAmount] = useState('')
  const [asset, setAsset] = useState('USDT')
  const [got, setGot] = useState('')
  const [gotAsset, setGotAsset] = useState('USDT')
  const [date, setDate] = useState(today)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const fromAcc = accounts.find((a) => String(a.id) === from)
  const toAcc = accounts.find((a) => String(a.id) === to)
  const cur = fromAcc && !fromAcc.is_multi_asset && fromAcc.currency ? fromAcc.currency : asset
  const gotCur = toAcc && !toAcc.is_multi_asset && toAcc.currency ? toAcc.currency : gotAsset
  const a = normalizeAmountInput(amount)
  const g = normalizeAmountInput(got)
  const rate = a && g ? rateLabel(a, cur, g, gotCur) : null
  const submit = async () => {
    if (!from || !to) return setErr('Выберите оба счёта')
    if (!a || !g) return setErr('Укажите обе суммы больше нуля')
    if (from === to && cur === gotCur) return setErr('Обмен в ту же валюту на том же счёте не имеет смысла')
    setErr(null)
    setBusy(true)
    try {
      const r = await post<MutationResult>('/exchanges', { from_account_id: Number(from), to_account_id: Number(to), amount: a, currency: cur, counter_amount: g, counter_currency: gotCur, occurred_on: date })
      invalidateEntities(qc, ['transactions', 'accounts', 'stats'])
      undoToast(`Обмен ${formatMoney(a, cur)} → ${formatMoney(g, gotCur)}`, r.action_id)
      onDone()
    } catch (e) {
      setErr(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); void submit() }} aria-label="Обмен валют">
      <div className="grid grid-cols-2 gap-2">
        <Field label="Отдаю со счёта" htmlFor="ex-from">
          <AccountSelect id="ex-from" accounts={accounts} value={from} onChange={setFrom} placeholder="Счёт" />
        </Field>
        <Field label="Получаю на счёт" htmlFor="ex-to">
          <AccountSelect id="ex-to" accounts={accounts} value={to} onChange={setTo} placeholder="Счёт" />
        </Field>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_120px] gap-2">
        <Field label="Отдал" htmlFor="ex-amount">
          <Input id="ex-amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} className="num" placeholder="0" />
        </Field>
        <Field label="Валюта" htmlFor="ex-cur">
          <AssetInput id="ex-cur" account={fromAcc} value={asset} onChange={setAsset} />
        </Field>
        <Field label="Получил фактически" htmlFor="ex-got">
          <Input id="ex-got" inputMode="decimal" value={got} onChange={(e) => setGot(e.target.value)} className="num" placeholder="0" />
        </Field>
        <Field label="Валюта" htmlFor="ex-gotcur">
          <AssetInput id="ex-gotcur" account={toAcc} value={gotAsset} onChange={setGotAsset} />
        </Field>
      </div>
      <p className="num text-sm text-muted-foreground" aria-live="polite">
        {rate ? `Курс обмена: ${rate}` : 'Курс посчитаю сам из двух сумм'}
      </p>
      <Field label="Дата" htmlFor="ex-date">
        <Input id="ex-date" type="date" value={date} max={addDays(today, 1)} onChange={(e) => setDate(e.target.value)} />
      </Field>
      <p className="text-xs text-muted-foreground">Обмен — не доход и не расход, хранится фактический курс.</p>
      <FormError err={err} />
      <Button type="submit" variant="primary" loading={busy}>
        Записать обмен
      </Button>
    </form>
  )
}

export function adjustResultText(r: AdjustmentResult): string {
  if (r.kind === 'matches') return `Сходится: ${formatMoney(r.balance, r.asset)}`
  if (r.kind === 'opening_set') return `Остаток задан: ${formatMoney(r.balance, r.asset)}`
  return `Корректировка ${formatMoney(r.delta ?? '0', r.asset, { sign: 'always' })} · теперь ${formatMoney(r.balance, r.asset)}`
}

export function AdjustForm({ accounts, onDone, defaultAccount, defaultAsset }: { accounts: AccountOut[]; onDone: () => void; defaultAccount?: number; defaultAsset?: string }) {
  const qc = useQueryClient()
  const post = useIdemPost()
  const undoToast = useUndoToast()
  const [acc, setAcc] = useState(defaultAccount ? String(defaultAccount) : '')
  const [asset, setAsset] = useState(defaultAsset ?? '')
  const [value, setValue] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const account = accounts.find((a) => String(a.id) === acc)
  const cur = account && !account.is_multi_asset && account.currency ? account.currency : asset
  const current = account?.assets.find((x) => x.asset === cur)
  const submit = async () => {
    const clean = value.replace(/[\s\u00a0]/g, '').replace(',', '.')
    const v = clean === '0' ? '0' : normalizeAmountInput(value)
    if (!account) return setErr('Выберите счёт')
    if (!cur) return setErr('Укажите валюту или монету')
    if (v === null) return setErr('Укажите фактический остаток')
    setErr(null)
    setBusy(true)
    try {
      const r = await post<AdjustmentResult>('/adjustments', { account_id: account.id, asset: cur, actual_balance: v })
      invalidateEntities(qc, ['transactions', 'accounts', 'stats'])
      undoToast(`${account.name}: ${adjustResultText(r)}`, r.action_id)
      onDone()
    } catch (e) {
      setErr(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); void submit() }} aria-label="Сверка остатка">
      <div className="grid grid-cols-[minmax(0,1fr)_120px] gap-2">
        <Field label="Счёт" htmlFor="adj-acc">
          <AccountSelect id="adj-acc" accounts={accounts} value={acc} onChange={setAcc} placeholder="Счёт" />
        </Field>
        <Field label="Валюта" htmlFor="adj-asset">
          <AssetInput id="adj-asset" account={account} value={asset} onChange={setAsset} />
        </Field>
      </div>
      <Field label="Сколько на счёте сейчас" htmlFor="adj-value" hint={current ? (current.balance !== null ? `По учёту: ${formatMoney(current.balance, cur)}` : 'Остаток не задан — станет начальным') : undefined}>
        <Input id="adj-value" inputMode="decimal" autoFocus value={value} onChange={(e) => setValue(e.target.value)} className="num" placeholder="5.2м" />
      </Field>
      <p className="text-xs text-muted-foreground">Если остаток уже задан, запишу корректировку на разницу — она не доход и не расход.</p>
      <FormError err={err} />
      <Button type="submit" variant="primary" loading={busy}>
        Сверить
      </Button>
    </form>
  )
}

export function OpeningForm({ account, asset, onDone }: { account: AccountOut; asset: string; onDone: () => void }) {
  const qc = useQueryClient()
  const cur = account.assets.find((x) => x.asset === asset)
  const [value, setValue] = useState(cur?.opening_balance ?? '')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const save = async (clear = false) => {
    const clean = String(value).replace(/[\s\u00a0]/g, '').replace(',', '.')
    const v = clear ? null : clean === '0' ? '0' : normalizeAmountInput(String(value))
    if (!clear && v === null) return setErr('Укажите сумму (можно 0)')
    setErr(null)
    setBusy(true)
    try {
      await api.put<AccountOut>(`/accounts/${account.id}/opening`, { asset, amount: v })
      invalidateEntities(qc, ['accounts', 'stats'])
      onDone()
    } catch (e) {
      setErr(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); void save() }} aria-label={`Начальный остаток ${asset}`}>
      <Field label={`Начальный остаток, ${asset}`} htmlFor={`open-${account.id}-${asset}`} hint="Баланс = начальный остаток + операции после него">
        <Input id={`open-${account.id}-${asset}`} inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} className="num" placeholder="не задан" />
      </Field>
      <FormError err={err} />
      <div className="flex gap-2">
        <Button type="submit" size="sm" variant="primary" loading={busy}>
          Сохранить
        </Button>
        {cur?.opening_balance !== null && cur?.opening_balance !== undefined && (
          <Button size="sm" variant="ghost" onClick={() => void save(true)}>
            Сбросить остаток
          </Button>
        )}
      </div>
    </form>
  )
}

const KINDS: { value: AccountKind; label: string }[] = [
  { value: 'bank', label: 'Банк' },
  { value: 'card', label: 'Карта' },
  { value: 'cash', label: 'Наличные' },
  { value: 'crypto_wallet', label: 'Криптокошелёк' },
  { value: 'exchange', label: 'Биржа' },
  { value: 'other', label: 'Другой' },
]
export const KIND_LABEL: Record<string, string> = Object.fromEntries(KINDS.map((k) => [k.value, k.label]))

export function AccountCreateForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [kind, setKind] = useState<AccountKind>('bank')
  const [multi, setMulti] = useState(false)
  const [currency, setCurrency] = useState('VND')
  const [aliases, setAliases] = useState('')
  const [icon, setIcon] = useState('PiggyBank')
  const [opening, setOpening] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    if (!name.trim()) return setErr('Укажите название')
    const clean = opening.replace(/[\s\u00a0]/g, '')
    const ob = !opening.trim() || multi ? null : clean === '0' ? '0' : normalizeAmountInput(opening)
    if (opening.trim() && !multi && ob === null) return setErr('Начальный остаток — число (можно 0)')
    const body: AccountCreate = {
      name: name.trim(),
      kind,
      is_multi_asset: multi,
      currency: multi ? null : currency,
      aliases: aliases.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean),
      icon,
      opening_balance: ob,
    }
    setErr(null)
    setBusy(true)
    try {
      await api.post<AccountOut>('/accounts', body)
      invalidateEntities(qc, ['accounts'])
      onDone()
    } catch (e) {
      setErr(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); void submit() }} aria-label="Новый счёт">
      <Field label="Название" htmlFor="na-name">
        <Input id="na-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} maxLength={64} placeholder="Wise" />
      </Field>
      <Field label="Тип" htmlFor="na-kind">
        <NativeSelect id="na-kind" value={kind} onChange={(e) => { const k = e.target.value as AccountKind; setKind(k); if (k === 'crypto_wallet' || k === 'exchange') setMulti(true) }}>
          {KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <label className="flex items-center justify-between gap-3 text-sm">
        <span>
          Любые монеты (кошелёк)
          <span className="block text-xs text-muted-foreground">монета добавится при первом упоминании</span>
        </span>
        <Switch checked={multi} onCheckedChange={setMulti} label="Мультивалютный кошелёк" />
      </label>
      {!multi && (
        <div className="grid grid-cols-2 gap-2">
          <Field label="Валюта" htmlFor="na-cur">
            <CurrencySelect id="na-cur" value={currency} onChange={setCurrency} />
          </Field>
          <Field label="Остаток сейчас" htmlFor="na-open" hint="пусто — не задан">
            <Input id="na-open" inputMode="decimal" value={opening} onChange={(e) => setOpening(e.target.value)} className="num" />
          </Field>
        </div>
      )}
      <Field label="Псевдонимы через запятую" htmlFor="na-alias" hint="как вы называете счёт в сообщениях">
        <Input id="na-alias" value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder="вайз, wise" />
      </Field>
      <Field label="Иконка">
        <div className="flex flex-wrap gap-1.5">
          {ACCOUNT_ICON_CHOICES.map((n) => {
            const I = iconByName(n)
            return (
              <Chip key={n} active={icon === n} onClick={() => setIcon(n)} aria-label={n} className="w-9 justify-center px-0">
                <I />
              </Chip>
            )
          })}
        </div>
      </Field>
      <FormError err={err} />
      <Button type="submit" variant="primary" loading={busy}>
        Создать счёт
      </Button>
    </form>
  )
}
