// Manual entry (DESIGN §9.7 <ManualTxSheet>): Расход / Доход / Перевод, big amount, currency chips,
// category grid, account chips, date chips, description. Enter saves. POST /transactions with an
// Idempotency-Key bound to the request body (retry after a network error reuses the same key).
import { useMemo, useRef, useState } from 'react'
import { CalendarDays } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { api, newIdempotencyKey, type AccountOut, type CategoryOut, type MutationResult, type TransactionCreate } from '@/lib/api'
import { cn } from '@/lib/utils'
import { errorMessage, invalidateEntities, useSettings, useToday } from '../api'
import { addDays } from '../lib/dates'
import { decCmp, normalizeAmountInput, shiftDecimal } from '../lib/decimal'
import { formatMoney, NBSP } from '../lib/format'
import { IconTile, iconByName } from '../ui/icons'
import { Button, Chip, Field, Input } from '../ui/kit'
import { Sheet } from '../ui/overlays'
import { CategoryPicker } from './CategoryPicker'
import { CurrencySelect } from './CurrencySelect'
import { useUndoToast } from './useUndoToast'
import { TransferForm } from '@/features/accounts/forms'

export interface ManualPrefill {
  kind?: 'expense' | 'income'
  amount?: string
  currency?: string
  category_id?: number | null
  account_id?: number | null
  description?: string | null
  counterparty?: string | null
}

const CUR_CHIPS: { code: string; label: string }[] = [
  { code: 'VND', label: '₫' },
  { code: 'USD', label: '$' },
  { code: 'USDT', label: 'USDT' },
  { code: 'RUB', label: '₽' },
]

/** Effective amount: bare number < 1000 in ₫ means thousands when the setting is on. */
export function effectiveAmount(raw: string, currency: string, smallThousands: boolean): string | null {
  const v = normalizeAmountInput(raw)
  if (!v) return null
  const hasSuffix = /[кkмm]|млн/i.test(raw)
  if (currency === 'VND' && smallThousands && !hasSuffix && decCmp(v, '1000') < 0) return shiftDecimal(v, 3)
  return v
}

export function topCategories(cats: CategoryOut[], kind: 'expense' | 'income', n = 12): CategoryOut[] {
  return cats
    .filter((c) => c.kind === kind && !c.is_archived)
    .sort((a, b) => (b.tx_count ?? 0) - (a.tx_count ?? 0) || a.sort_order - b.sort_order)
    .slice(0, n)
}

export function ManualTxForm({ categories, accounts, prefill, onSaved }: { categories: CategoryOut[]; accounts: AccountOut[]; prefill?: ManualPrefill; onSaved: () => void }) {
  const qc = useQueryClient()
  const { data: settings } = useSettings()
  const { today } = useToday()
  const undoToast = useUndoToast()
  const [mode, setMode] = useState<'expense' | 'income' | 'transfer'>(prefill?.kind ?? 'expense')
  const [amount, setAmount] = useState(prefill?.amount ?? '')
  const [currency, setCurrency] = useState(prefill?.currency ?? settings?.default_currency ?? 'VND')
  const [categoryId, setCategoryId] = useState<number | null>(prefill?.category_id ?? null)
  const [account, setAccount] = useState<string>(prefill?.account_id ? String(prefill.account_id) : 'auto')
  const [date, setDate] = useState(today)
  const [description, setDescription] = useState(prefill?.description ?? '')
  const [counterparty] = useState(prefill?.counterparty ?? '')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const attempt = useRef<{ body: string; key: string } | null>(null)
  // Synchronous in-flight flag: a second Enter before React re-renders must not send another POST.
  const inFlight = useRef(false)

  const kind = mode === 'income' ? 'income' : 'expense'
  const eff = effectiveAmount(amount, currency, settings?.small_amount_thousands ?? true)
  const grid = useMemo(() => topCategories(categories, kind), [categories, kind])
  const live = accounts.filter((a) => !a.is_archived)
  const fitting = live.filter((a) => a.is_multi_asset || a.currency === currency)
  const category = categories.find((c) => c.id === categoryId)

  const submit = async () => {
    if (inFlight.current) return
    if (!eff) return setErr('Укажите сумму больше нуля')
    if (!categoryId || category?.kind !== kind) return setErr('Выберите категорию')
    if (date > addDays(today, 1)) return setErr('Дата не может быть в будущем больше чем на сутки')
    const body: TransactionCreate = {
      kind,
      amount: eff,
      currency,
      category_id: categoryId,
      account_id: account === 'auto' || account === 'none' ? null : Number(account),
      account_none: account === 'none',
      description: description.trim() || null,
      counterparty: counterparty.trim() || null,
      occurred_on: date,
    }
    const json = JSON.stringify(body)
    if (!attempt.current || attempt.current.body !== json) attempt.current = { body: json, key: newIdempotencyKey() }
    setErr(null)
    setBusy(true)
    inFlight.current = true
    try {
      const r = await api.post<MutationResult>('/transactions', body, { idempotencyKey: attempt.current.key })
      attempt.current = null
      invalidateEntities(qc, ['transactions', 'accounts', 'stats', 'merchants'])
      const t = r.transaction
      const head = `✓ ${formatMoney(t.amount, t.currency)} · ${t.category?.name ?? ''}`
      undoToast(t.status === 'needs_account' ? `${head}. Записал в расходы — счёт выберите в «Разобрать»` : head, r.action_id)
      onSaved()
    } catch (e) {
      setErr(errorMessage(e))
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  if (mode === 'transfer') {
    return (
      <div className="grid gap-4">
        <ModeSwitch mode={mode} setMode={setMode} />
        <TransferForm accounts={accounts} onDone={onSaved} />
      </div>
    )
  }

  return (
    <form
      className="grid gap-4"
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.target as HTMLElement).tagName === 'INPUT') {
          e.preventDefault()
          void submit()
        }
      }}
      aria-label="Новая операция"
    >
      <ModeSwitch mode={mode} setMode={(m) => { setMode(m); if (category && m !== 'transfer' && category.kind !== m) setCategoryId(null) }} />
      <div>
        <label htmlFor="manual-amount" className="text-xs font-medium text-muted-foreground">
          Сумма
        </label>
        <div className="mt-1 flex items-center gap-2">
          <input
            id="manual-amount"
            autoFocus
            inputMode="decimal"
            autoComplete="off"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="0"
            className="num h-14 w-full min-w-0 rounded-md border border-input bg-card px-3 text-[32px] leading-9 font-semibold tracking-[-0.02em] focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
          />
        </div>
        <p className="num mt-1 h-4 text-xs text-muted-foreground" aria-live="polite">
          {eff && eff !== normalizeAmountInput(amount) ? `= ${formatMoney(eff, currency)} (число меньше 1000 в ₫ — тысячи)` : eff ? `= ${formatMoney(eff, currency)}` : ''}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5" role="radiogroup" aria-label="Валюта">
          {CUR_CHIPS.map((c) => (
            <Chip key={c.code} role="radio" aria-checked={currency === c.code} active={currency === c.code} onClick={() => setCurrency(c.code)}>
              {c.label}
            </Chip>
          ))}
          <div className="w-28">
            <CurrencySelect value={CUR_CHIPS.some((c) => c.code === currency) ? '' : currency} onChange={(v) => v && setCurrency(v)} allowEmpty emptyLabel="другая" />
          </div>
        </div>
      </div>

      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-xs font-medium text-muted-foreground">Категория</span>
          <div className="w-44">
            <CategoryPicker cats={categories} value={categoryId} kind={kind} onChange={(c) => setCategoryId(c.id)} triggerLabel={<span className="flex-1 text-[13px] text-muted-foreground">Все категории…</span>} />
          </div>
        </div>
        <div className="grid grid-cols-4 gap-1.5" role="radiogroup" aria-label="Категория">
          {grid.map((c) => (
            <button
              key={c.id}
              type="button"
              role="radio"
              aria-checked={categoryId === c.id}
              onClick={() => setCategoryId(c.id)}
              className={cn('flex min-h-16 flex-col items-center justify-center gap-1 rounded-md border px-1 py-2 text-center text-[12px] leading-4 font-medium focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none', categoryId === c.id ? 'border-primary bg-primary-soft text-primary' : 'border-border hover:bg-surface-2')}
            >
              <IconTile icon={iconByName(c.icon)} business={c.is_business} size={24} />
              <span className="line-clamp-2">{c.name}</span>
            </button>
          ))}
        </div>
        {category && !grid.includes(category) && <p className="mt-1.5 text-sm">Выбрано: {category.name}</p>}
      </div>

      <Field label="Счёт">
        <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Счёт">
          <Chip role="radio" aria-checked={account === 'auto'} active={account === 'auto'} onClick={() => setAccount('auto')} title="₫ → Карта, ₽ → Карта RUB, крипта и $ — спрошу">
            Авто
          </Chip>
          {fitting.map((a) => (
            <Chip key={a.id} role="radio" aria-checked={account === String(a.id)} active={account === String(a.id)} onClick={() => setAccount(String(a.id))}>
              {a.name}
            </Chip>
          ))}
          <Chip role="radio" aria-checked={account === 'none'} active={account === 'none'} onClick={() => setAccount('none')}>
            Без счёта
          </Chip>
        </div>
      </Field>

      <Field label="Дата">
        <div className="flex flex-wrap items-center gap-1.5">
          <Chip active={date === today} onClick={() => setDate(today)}>
            Сегодня
          </Chip>
          <Chip active={date === addDays(today, -1)} onClick={() => setDate(addDays(today, -1))}>
            Вчера
          </Chip>
          <label className="relative inline-flex items-center">
            <CalendarDays className="pointer-events-none absolute left-2 size-3.5 text-muted-foreground" aria-hidden />
            <Input type="date" aria-label="Другая дата" value={date} max={addDays(today, 1)} onChange={(e) => setDate(e.target.value)} className="h-8 w-40 pl-7 text-[13px]" />
          </label>
        </div>
      </Field>

      <Field label="Описание" htmlFor="manual-desc">
        <Input id="manual-desc" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="обед, Highlands" maxLength={500} />
      </Field>

      {err && (
        <p role="alert" className="rounded-sm bg-expense-soft px-2 py-1.5 text-sm text-destructive">
          {err}
        </p>
      )}
      <Button type="submit" variant="primary" size="lg" loading={busy}>
        Записать{eff ? `${NBSP}${formatMoney(eff, currency)}` : ''}
      </Button>
    </form>
  )
}

function ModeSwitch({ mode, setMode }: { mode: 'expense' | 'income' | 'transfer'; setMode: (m: 'expense' | 'income' | 'transfer') => void }) {
  const opts = [
    { v: 'expense', l: 'Расход' },
    { v: 'income', l: 'Доход' },
    { v: 'transfer', l: 'Перевод' },
  ] as const
  return (
    <div role="radiogroup" aria-label="Тип" className="grid grid-cols-3 rounded-md bg-muted p-0.5">
      {opts.map((o) => (
        <button key={o.v} type="button" role="radio" aria-checked={mode === o.v} onClick={() => setMode(o.v)} className={cn('h-9 rounded-sm text-sm font-medium', mode === o.v ? 'bg-card shadow-xs' : 'text-muted-foreground hover:text-foreground')}>
          {o.l}
        </button>
      ))}
    </div>
  )
}

export function ManualTxSheet({ open, onOpenChange, categories, accounts, prefill }: { open: boolean; onOpenChange: (v: boolean) => void; categories: CategoryOut[]; accounts: AccountOut[]; prefill?: ManualPrefill }) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Новая операция" width={420}>
      {open && <ManualTxForm categories={categories} accounts={accounts} prefill={prefill} onSaved={() => onOpenChange(false)} />}
    </Sheet>
  )
}
