// Manual add without AI (DESIGN §9.7): Расход/Доход/Перевод, big amount + currency chips,
// category grid (frequent first) + search, account chips, date chips, description. Enter saves.
// POST /transactions (or /transfers) with one Idempotency-Key per attempt (reused on [Повторить]).
// «Записал» is shown only after the server answered 2xx.
import { useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { CalendarDays, CircleAlert, Search } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { SegmentedControl } from '@/components/pf/SegmentedControl'
import { Spinner } from '@/components/pf/Spinner'
import { accountIcon, categoryIcon } from '@/components/pf/icons'
import { ApiError, NetworkError, errorText, newIdempotencyKey } from '@/lib/api/client'
import { actionsApi, categoriesApi, transactionsApi, transfersApi } from '@/lib/api/endpoints'
import { useAccounts, useSettings } from '@/lib/api/hooks'
import { keysForEntities } from '@/lib/api/sse'
import type { AccountOut, CategoryOut, TransactionCreate, TransferCreate } from '@/lib/api/types'
import { parseAmountInput } from '@/lib/amountInput'
import { addDays, formatMoney, isFiatCurrency, localDateISO } from '@/lib/format'
import { txCardTitle } from '@/features/assistant/logic'
import { cn } from '@/lib/utils'

export type ManualKind = 'expense' | 'income' | 'transfer'
export interface ManualPrefill { amount?: string; currency?: string; text?: string; kind?: ManualKind }

const CURRENCY_CHIPS: { code: string; label: string }[] = [
  { code: 'VND', label: '₫' }, { code: 'USD', label: '$' }, { code: 'USDT', label: 'USDT' }, { code: 'RUB', label: '₽' },
]

/** Account the backend would pick for this currency (₫ → Карта, ₽ → Карта RUB). */
export function defaultAccountFor(accounts: AccountOut[], currency: string): AccountOut | null {
  return accounts.find((a) => !a.is_archived && a.default_for_currencies.includes(currency)) ?? null
}

export function accountsForCurrency(accounts: AccountOut[], currency: string): AccountOut[] {
  const fits = (a: AccountOut) => a.is_multi_asset || a.currency === currency
  const first = accounts.filter((a) => !a.is_archived && fits(a))
  if (!isFiatCurrency(currency)) return first // a coin only from wallets / accounts in that coin
  return [...first, ...accounts.filter((a) => !a.is_archived && !fits(a))]
}

interface Props {
  prefill?: ManualPrefill
  onSaved?: () => void
  autoFocus?: boolean
  className?: string
}

export function ManualTxForm({ prefill, onSaved, autoFocus = true, className }: Props) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const { data: settings } = useSettings()
  const { data: accounts = [] } = useAccounts()
  const catsQuery = useQuery({
    queryKey: ['categories', { with_counts: true }],
    queryFn: () => categoriesApi.list({ with_counts: true }),
    staleTime: 60_000,
  })
  const categories = catsQuery.data ?? []
  // offline with nothing cached: say so instead of «Ничего не нашлось»
  const catsOffline = catsQuery.data === undefined && (catsQuery.fetchStatus === 'paused' || catsQuery.error instanceof NetworkError)
  const tz = settings?.timezone
  const today = localDateISO(new Date(), tz)
  const initial = prefill?.amount ? parseAmountInput(prefill.amount, prefill.currency ?? 'VND', settings?.small_amount_thousands ?? true) : null

  const [kind, setKind] = useState<ManualKind>(prefill?.kind ?? 'expense')
  const [amount, setAmount] = useState(initial?.amount ?? prefill?.amount ?? '')
  const [currency, setCurrency] = useState(initial?.asset ?? prefill?.currency ?? settings?.default_currency ?? 'VND')
  const [otherCurrency, setOtherCurrency] = useState(false)
  const [categoryId, setCategoryId] = useState<number | null>(null)
  const [catQuery, setCatQuery] = useState('')
  const [accountId, setAccountId] = useState<number | 'none' | 'auto'>('auto')
  const [toAccountId, setToAccountId] = useState<number | null>(null)
  const [date, setDate] = useState(today)
  const [description, setDescription] = useState(prefill?.text ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [retryable, setRetryable] = useState(false)
  const attemptKey = useRef<{ sig: string; key: string } | null>(null)

  const parsed = parseAmountInput(amount, currency, settings?.small_amount_thousands ?? true)
  const effCurrency = parsed?.asset ?? currency
  const catKind = kind === 'income' ? 'income' : 'expense'
  const cats = useMemo(() => {
    const list = categories.filter((c) => c.kind === catKind && !c.is_archived)
    const q = catQuery.trim().toLowerCase()
    const filtered = q ? list.filter((c) => c.name.toLowerCase().includes(q) || c.aliases.some((a) => a.includes(q))) : list
    return [...filtered].sort((a, b) => (b.tx_count ?? 0) - (a.tx_count ?? 0) || a.sort_order - b.sort_order)
  }, [categories, catKind, catQuery])
  const autoAccount = defaultAccountFor(accounts, effCurrency)
  const accountChoices = accountsForCurrency(accounts, effCurrency)

  const validate = (): string | null => {
    if (!parsed || !/^\d/.test(parsed.amount) || /^0+(\.0+)?$/.test(parsed.amount)) return 'Сумма должна быть больше нуля'
    if (kind !== 'transfer' && !categoryId) return 'Выберите категорию'
    if (kind === 'transfer') {
      if (typeof accountId !== 'number' || !toAccountId) return 'Выберите счета «откуда» и «куда»'
      if (accountId === toAccountId) return 'Счета перевода должны различаться'
    }
    return null
  }

  const reset = () => {
    setAmount('')
    setDescription('')
    setCategoryId(null)
    setCatQuery('')
    setError(null)
    attemptKey.current = null
  }

  const save = async () => {
    if (busy) return
    const v = validate()
    if (v) { setError(v); return }
    const amt = parsed!.amount
    let body: TransactionCreate | TransferCreate
    if (kind === 'transfer') {
      body = { from_account_id: accountId as number, to_account_id: toAccountId!, amount: amt, currency: effCurrency, description: description.trim() || null, occurred_on: date }
    } else {
      body = {
        kind, amount: amt, currency: effCurrency, category_id: categoryId!,
        account_id: typeof accountId === 'number' ? accountId : null,
        account_none: accountId === 'none',
        description: description.trim() || null, occurred_on: date,
      }
    }
    // same attempt (same body) → same key; anything changed → a new attempt
    const sig = JSON.stringify(body)
    if (!attemptKey.current || attemptKey.current.sig !== sig) attemptKey.current = { sig, key: newIdempotencyKey() }
    const key = attemptKey.current.key
    setBusy(true)
    setError(null)
    setRetryable(false)
    try {
      const r = kind === 'transfer'
        ? await transfersApi.transfer(body as TransferCreate, key)
        : await transactionsApi.create(body as TransactionCreate, key)
      for (const k of keysForEntities(['transactions', 'accounts', 'stats'])) void qc.invalidateQueries({ queryKey: k })
      const title = txCardTitle(r.transaction)
      // no account yet ($ / crypto without one): say so — it waits in «Разобрать» (DESIGN §14)
      const waits = r.transaction.status === 'needs_account'
      const undo = {
        label: 'Отменить',
        onClick: () => {
          actionsApi.undo(r.action_id, newIdempotencyKey())
            .then((u) => { toast(u.summary.startsWith('Отменено') ? u.summary : `Отменено: ${u.summary}`); for (const k of keysForEntities(['transactions'])) void qc.invalidateQueries({ queryKey: k }) })
            .catch((e) => toast.error(errorText(e)))
        },
      }
      if (waits) {
        toast('Записал в расходы. С какого счёта?', {
          description: `${title} — ждёт выбора счёта в «Разобрать»`,
          action: { label: 'Разобрать', onClick: () => nav('/operations?status=needs_account') },
          cancel: undo,
        })
      } else {
        toast(`Записал: ${title}`, { action: undo })
      }
      reset()
      onSaved?.()
    } catch (e) {
      if (e instanceof NetworkError) {
        // offline: nothing left; online without an answer: the server may have saved it — the same key
        // on «Повторить» settles it without a duplicate
        setError(navigator.onLine === false ? 'Не сохранено — нет связи' : 'Нет ответа сервера — не знаю, сохранилось ли. «Повторить» не создаст дубль')
        setRetryable(true)
      }
      else if (e instanceof ApiError && e.status >= 500) { setError('Не удалось сохранить. Ничего не записано'); setRetryable(true) }
      else { setError(errorText(e)); setRetryable(false); attemptKey.current = null }
    } finally {
      setBusy(false)
    }
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && !(e.target instanceof HTMLButtonElement)) { e.preventDefault(); void save() }
  }

  const chip = (active: boolean) => cn(
    'inline-flex h-8 items-center gap-1.5 rounded-sm border px-2.5 text-small transition-colors focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none',
    active ? 'border-primary bg-primary-soft text-primary' : 'bg-card text-muted-foreground hover:bg-surface-2 hover:text-foreground',
  )

  const accountChip = (a: AccountOut, active: boolean, onPick: (v: number) => void) => {
    const Icon = accountIcon(a)
    return (
      <button key={a.id} type="button" className={chip(active)} onClick={() => onPick(a.id)} aria-pressed={active}>
        <Icon className="size-3.5" /> {a.name}
      </button>
    )
  }

  return (
    <div className={cn('flex flex-col gap-5', className)} onKeyDown={onKeyDown} data-testid="manual-form">
      <SegmentedControl
        aria-label="Тип операции"
        size="md"
        value={kind}
        onChange={(k) => { setKind(k); setCategoryId(null); setError(null) }}
        options={[{ value: 'expense', label: 'Расход' }, { value: 'income', label: 'Доход' }, { value: 'transfer', label: 'Перевод' }]}
        className="self-start"
      />

      <div className="flex flex-col gap-2">
        <div className="flex items-end gap-2">
          <input
            autoFocus={autoFocus}
            value={amount}
            onChange={(e) => { setAmount(e.target.value); setError(null) }}
            inputMode="decimal"
            placeholder="0"
            aria-label="Сумма"
            data-testid="manual-amount"
            className="num w-full min-w-0 border-b border-input bg-transparent pb-1 text-display outline-none placeholder:text-subtle-foreground focus:border-ring"
          />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {CURRENCY_CHIPS.map((c) => (
            <button key={c.code} type="button" className={chip(!otherCurrency && currency === c.code)} onClick={() => { setCurrency(c.code); setOtherCurrency(false) }} aria-pressed={currency === c.code}>
              {c.label}
            </button>
          ))}
          {otherCurrency ? (
            <Input autoFocus value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 16))} className="h-8 w-24 uppercase" aria-label="Валюта" />
          ) : (
            <button type="button" className={chip(!CURRENCY_CHIPS.some((c) => c.code === currency))} onClick={() => setOtherCurrency(true)}>
              {CURRENCY_CHIPS.some((c) => c.code === currency) ? 'другая' : currency}
            </button>
          )}
          <span className="num ml-auto text-caption text-muted-foreground" data-testid="manual-parsed">
            {amount ? (parsed ? `= ${formatMoney(parsed.amount, effCurrency)}` : 'Не понял сумму') : ''}
          </span>
        </div>
      </div>

      {kind !== 'transfer' && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-caption text-muted-foreground">Категория</span>
            <label className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input value={catQuery} onChange={(e) => setCatQuery(e.target.value)} placeholder="Поиск" className="h-8 w-36 pl-7 text-base md:h-7 md:text-small" aria-label="Поиск категории" />
            </label>
          </div>
          <div className="grid grid-cols-4 gap-1.5" role="radiogroup" aria-label="Категория" data-testid="manual-categories">
            {cats.slice(0, catQuery ? 24 : 12).map((c: CategoryOut) => {
              const Icon = categoryIcon(c)
              const active = categoryId === c.id
              return (
                <button
                  key={c.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => { setCategoryId(c.id); setError(null) }}
                  className={cn(
                    'flex min-h-16 flex-col items-center justify-center gap-1 rounded-md border px-1 py-2 text-center text-caption transition-colors focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none',
                    active ? 'border-primary bg-primary-soft text-primary' : 'bg-card text-muted-foreground hover:bg-surface-2 hover:text-foreground',
                  )}
                >
                  <Icon className="size-4" />
                  <span className="line-clamp-2 leading-tight">{c.name}</span>
                </button>
              )
            })}
            {!cats.length && <p className="col-span-4 py-2 text-small text-muted-foreground">{catsOffline ? 'Нет связи — категории не загружены' : 'Ничего не нашлось'}</p>}
          </div>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <span className="text-caption text-muted-foreground">{kind === 'transfer' ? 'Откуда' : kind === 'income' ? 'На счёт' : 'Счёт'}</span>
        <div className="flex flex-wrap gap-1.5" data-testid="manual-accounts">
          {kind !== 'transfer' && (
            <button type="button" className={chip(accountId === 'auto')} onClick={() => setAccountId('auto')} aria-pressed={accountId === 'auto'}>
              {autoAccount ? `Авто: ${autoAccount.name}` : 'Спросить потом'}
            </button>
          )}
          {accountChoices.map((a) => accountChip(a, accountId === a.id, (v) => setAccountId(v)))}
          {kind !== 'transfer' && (
            <button type="button" className={chip(accountId === 'none')} onClick={() => setAccountId('none')} aria-pressed={accountId === 'none'}>
              Без счёта
            </button>
          )}
        </div>
      </div>

      {kind === 'transfer' && (
        <div className="flex flex-col gap-2">
          <span className="text-caption text-muted-foreground">Куда</span>
          <div className="flex flex-wrap gap-1.5">
            {accountChoices.filter((a) => a.id !== accountId).map((a) => accountChip(a, toAccountId === a.id, (v) => setToAccountId(v)))}
          </div>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <span className="text-caption text-muted-foreground">Дата</span>
        <div className="flex flex-wrap items-center gap-1.5">
          <button type="button" className={chip(date === today)} onClick={() => setDate(today)}>Сегодня</button>
          <button type="button" className={chip(date === addDays(today, -1))} onClick={() => setDate(addDays(today, -1))}>Вчера</button>
          <label className={cn(chip(date !== today && date !== addDays(today, -1)), 'relative')}>
            <CalendarDays className="size-3.5" />
            <input type="date" value={date} max={addDays(today, 1)} onChange={(e) => e.target.value && setDate(e.target.value)} className="w-[128px] bg-transparent text-base outline-none md:w-[118px] md:text-small" aria-label="Дата" />
          </label>
        </div>
      </div>

      <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Описание (необязательно)" aria-label="Описание" />

      {error && (
        <p className="flex items-center gap-1.5 text-small text-destructive" role="alert" data-testid="manual-error">
          <CircleAlert className="size-4 shrink-0" /> {error}
        </p>
      )}
      <Button type="button" className="h-11" onClick={() => void save()} disabled={busy} aria-busy={busy || undefined} data-testid="manual-save">
        {busy && <Spinner size={14} />}
        {error && retryable ? 'Повторить' : 'Записать'}
      </Button>
    </div>
  )
}
