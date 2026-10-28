// Inline edit of one operation (DESIGN §9.4/§10.4): Enter — save, Esc — cancel. PATCH sends only
// changed fields + version (optimistic locking → 409 shows a clear message and refetches).
import { useMemo, useState, type KeyboardEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight } from 'lucide-react'
import { api, type AccountOut, type AuditEntryOut, type CategoryOut, type TransactionOut, type TransactionPatch } from '@/lib/api'
import { cn } from '@/lib/utils'
import { errorMessage, usePatchTransaction, useToday } from '../api'
import { decCmp, normalizeAmountInput } from '../lib/decimal'
import { addDays, formatDateTime } from '../lib/dates'
import { formatMoney } from '../lib/format'
import { Badge, Button, Field, Input, Kbd, NativeSelect, Segmented } from '../ui/kit'
import { CategoryPicker } from './CategoryPicker'
import { CurrencySelect } from './CurrencySelect'
import { useUndoToast } from './useUndoToast'
import { debtRealAccount, debtTxTitle } from '@/features/debts/lib'

type AccChoice = string // account id | 'none' | 'pending'

const EDITABLE_KINDS = ['expense', 'income', 'refund'] as const

/** Accounts offered for an operation: same-currency and multi-asset first, then the rest. */
export function accountChoices(accounts: AccountOut[], currency: string): AccountOut[] {
  const live = accounts.filter((a) => !a.is_archived)
  const fits = (a: AccountOut) => a.is_multi_asset || a.currency === currency
  return [...live.filter(fits), ...live.filter((a) => !fits(a))]
}

export function TxEditForm({ tx, categories, accounts, onDone, onCancel, focus = 'amount', className }: { tx: TransactionOut; categories: CategoryOut[]; accounts: AccountOut[]; onDone: () => void; onCancel: () => void; focus?: 'amount' | 'category'; className?: string }) {
  const regular = (EDITABLE_KINDS as readonly string[]).includes(tx.kind)
  // debt operations (DEBTS.md §4 «Правка»): no kind / account / counterparty change for «forgive»;
  // a loan / repayment is a transfer whose editable account is the real side
  const debt = tx.debt ?? null
  const debtTransfer = !!debt && debt.op !== 'forgive'
  const realAcc = debtTransfer ? debtRealAccount(tx) : tx.account
  const [kind, setKind] = useState(tx.kind)
  const [amount, setAmount] = useState(tx.amount)
  const [currency, setCurrency] = useState(tx.currency)
  const [categoryId, setCategoryId] = useState<number | null>(tx.category?.id ?? null)
  const [account, setAccount] = useState<AccChoice>(tx.status === 'needs_account' ? 'pending' : realAcc ? String(realAcc.id) : 'none')
  const [accountAmount, setAccountAmount] = useState(tx.account_amount ?? '')
  const [date, setDate] = useState(tx.occurred_on)
  const [description, setDescription] = useState(tx.description ?? '')
  const [counterparty, setCounterparty] = useState(tx.counterparty ?? '')
  const [showHistory, setShowHistory] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const patchM = usePatchTransaction()
  const undoToast = useUndoToast()
  const { tz, today } = useToday()

  const category = categories.find((c) => c.id === categoryId) ?? null
  const catKind = kind === 'income' ? 'income' : 'expense'
  const showAccountAmount = !!tx.account_asset && tx.account_asset !== tx.currency && regular

  const patch = useMemo((): TransactionPatch | string => {
    const p: TransactionPatch = { version: tx.version }
    const amt = normalizeAmountInput(amount)
    if (!amt) return 'Сумма должна быть больше нуля'
    if (decCmp(amt, tx.amount) !== 0) p.amount = amt
    if (currency !== tx.currency) p.currency = currency
    const wasAccount = tx.status === 'needs_account' ? 'pending' : realAcc ? String(realAcc.id) : 'none'
    if (debtTransfer && account !== wasAccount) {
      if (account === 'none') p.account_none = true
      else if (account !== 'pending') p.account_id = Number(account)
    }
    if (regular && debt) {
      if (!categoryId) return 'Выберите категорию'
      if (categoryId !== (tx.category?.id ?? null)) p.category_id = categoryId
    }
    if (regular && !debt) {
      if (kind !== tx.kind) p.kind = kind as TransactionPatch['kind']
      if (!categoryId) return 'Выберите категорию'
      if (category && category.kind !== catKind) return kind === 'income' ? 'Для дохода нужна категория дохода' : 'Для расхода нужна категория расхода'
      if (categoryId !== (tx.category?.id ?? null)) p.category_id = categoryId
      const was = tx.status === 'needs_account' ? 'pending' : tx.account ? String(tx.account.id) : 'none'
      if (account !== was) {
        if (account === 'none') p.account_none = true
        else if (account !== 'pending') p.account_id = Number(account)
      }
      if (showAccountAmount && accountAmount && accountAmount !== (tx.account_amount ?? '')) {
        const aa = normalizeAmountInput(accountAmount)
        if (!aa) return 'Сумма списания должна быть больше нуля'
        if (decCmp(aa, tx.account_amount ?? '0') !== 0) p.account_amount = aa
      }
      if (counterparty !== (tx.counterparty ?? '')) p.counterparty = counterparty || null
    }
    if (date !== tx.occurred_on) {
      if (!date) return 'Укажите дату'
      if (date > addDays(today, 1)) return 'Дата не может быть в будущем больше чем на сутки'
      p.occurred_on = date
    }
    if (description !== (tx.description ?? '')) p.description = description || null
    return p
  }, [amount, currency, kind, categoryId, category, catKind, account, accountAmount, showAccountAmount, date, description, counterparty, tx, regular, today, debt, debtTransfer, realAcc])

  const changed = typeof patch !== 'string' && Object.keys(patch).length > 1

  const save = () => {
    if (typeof patch === 'string') return setErr(patch)
    if (!changed) return onCancel()
    setErr(null)
    patchM.mutate(
      { id: tx.id, patch },
      {
        onSuccess: (r) => {
          undoToast(`Изменено: ${formatMoney(r.transaction.amount, r.transaction.currency)}${r.transaction.category ? ` · ${r.transaction.category.name}` : ''}`, r.action_id)
          onDone()
        },
        onError: (e) => setErr(errorMessage(e)),
      },
    )
  }

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      onCancel()
    } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey || (e.target as HTMLElement).tagName === 'INPUT')) {
      e.preventDefault()
      save()
    }
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        save()
      }}
      onKeyDown={onKeyDown}
      className={cn('grid gap-3 rounded-md border border-border bg-surface-2 p-3', className)}
      aria-label="Правка операции"
    >
      <div className="flex flex-wrap items-center gap-2">
        {debt ? (
          <Badge tone="transfer">{debtTxTitle(tx)}</Badge>
        ) : regular ? (
          <Segmented
            label="Тип операции"
            size="sm"
            value={kind}
            onChange={(v) => {
              setKind(v)
              const want = v === 'income' ? 'income' : 'expense'
              if (category && category.kind !== want) setCategoryId(null)
            }}
            options={[
              { value: 'expense', label: 'Расход' },
              { value: 'income', label: 'Доход' },
              { value: 'refund', label: 'Возврат' },
            ]}
          />
        ) : (
          <Badge tone="transfer">{{ transfer: 'Перевод', exchange: 'Обмен', adjustment: 'Корректировка' }[tx.kind as 'transfer'] ?? tx.kind}</Badge>
        )}
        {category && <Badge tone={category.is_business ? 'primary' : 'muted'}>{category.is_business ? 'Бизнес' : 'Личное'}</Badge>}
        <span className="text-xs text-muted-foreground">бизнес или личное — по категории</span>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-[minmax(0,140px)_96px_minmax(0,1fr)_minmax(0,160px)_minmax(0,150px)]">
        <Field label="Сумма" htmlFor={`amt-${tx.id}`}>
          <Input id={`amt-${tx.id}`} inputMode="decimal" autoFocus={focus === 'amount'} value={amount} onChange={(e) => setAmount(e.target.value)} className="num" disabled={tx.kind === 'adjustment'} />
        </Field>
        <Field label="Валюта" htmlFor={`cur-${tx.id}`}>
          <CurrencySelect id={`cur-${tx.id}`} value={currency} onChange={setCurrency} disabled={!regular && tx.kind !== 'transfer'} />
        </Field>
        {regular ? (
          <Field label="Категория" className="col-span-2 md:col-span-1">
            <CategoryPicker cats={categories} value={categoryId} kind={catKind} onChange={(c) => setCategoryId(c.id)} />
          </Field>
        ) : (
          <div className="hidden md:block" />
        )}
        {(regular && !debt) || debtTransfer ? (
          <Field label="Счёт" htmlFor={`acc-${tx.id}`}>
            <NativeSelect id={`acc-${tx.id}`} value={account} onChange={(e) => setAccount(e.target.value)}>
              {account === 'pending' && <option value="pending">Не выбран (разобрать)</option>}
              {accountChoices(accounts, currency).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
              <option value="none">Без счёта</option>
            </NativeSelect>
          </Field>
        ) : (
          <div className="hidden md:block" />
        )}
        <Field label="Дата" htmlFor={`date-${tx.id}`}>
          <Input id={`date-${tx.id}`} type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>
      </div>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <Field label="Описание" htmlFor={`desc-${tx.id}`} className={regular ? '' : 'md:col-span-3'}>
          <Input id={`desc-${tx.id}`} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="обед, аккаунт GPT Pro…" maxLength={500} />
        </Field>
        {regular && !debt && (
          <Field label="Получатель / плательщик" htmlFor={`cp-${tx.id}`}>
            <Input id={`cp-${tx.id}`} value={counterparty} onChange={(e) => setCounterparty(e.target.value)} placeholder="@ник, клиент" maxLength={128} />
          </Field>
        )}
        {showAccountAmount && (
          <Field label={`Списано фактически, ${tx.account_asset}`} htmlFor={`aa-${tx.id}`} hint={tx.is_estimated ? 'сейчас по курсу (≈) — укажите факт' : undefined}>
            <Input id={`aa-${tx.id}`} inputMode="decimal" value={accountAmount} onChange={(e) => setAccountAmount(e.target.value)} className="num" />
          </Field>
        )}
      </div>
      {err && (
        <p role="alert" className="rounded-sm bg-expense-soft px-2 py-1.5 text-sm text-destructive">
          {err}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button type="button" className="inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground" onClick={() => setShowHistory((v) => !v)} aria-expanded={showHistory}>
          История изменений <ChevronRight className={cn('size-3.5 transition-transform', showHistory && 'rotate-90')} />
        </button>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={onCancel}>
            Отмена <Kbd>Esc</Kbd>
          </Button>
          <Button type="submit" variant="primary" size="sm" loading={patchM.isPending}>
            Сохранить <Kbd className="border-transparent bg-primary-foreground/20 text-primary-foreground">⏎</Kbd>
          </Button>
        </div>
      </div>
      {showHistory && <TxHistory txId={tx.id} tz={tz} today={today} />}
    </form>
  )
}

const FIELD_NAMES: Record<string, string> = { amount: 'Сумма', currency: 'Валюта', category_id: 'Категория', account_id: 'Счёт', occurred_on: 'Дата', description: 'Описание', counterparty: 'Получатель', kind: 'Тип', account_amount: 'Списано' }
const OP_NAMES: Record<string, string> = { create: 'Создана', update: 'Изменена', delete: 'Удалена', restore: 'Восстановлена', merge: 'Слияние категорий' }

export function TxHistory({ txId, tz, today }: { txId: number; tz: string; today: string }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['transaction', txId, 'history'], queryFn: () => api.get<AuditEntryOut[]>(`/transactions/${txId}/history`) })
  if (isLoading) return <p className="text-sm text-muted-foreground">Загружаю историю…</p>
  if (error) return <p className="text-sm text-destructive">{errorMessage(error, 'Историю загрузить не удалось')}</p>
  if (!data?.length) return <p className="text-sm text-muted-foreground">Изменений не было</p>
  return (
    <ol className="grid gap-1.5 border-t border-border pt-2 text-[13px]">
      {data.map((h) => {
        const keys = Object.keys(h.after ?? {}).filter((k) => FIELD_NAMES[k] && JSON.stringify(h.before?.[k]) !== JSON.stringify(h.after?.[k]))
        return (
          <li key={h.id} className={cn('flex flex-wrap gap-x-2', h.operation === 'delete' && 'line-through decoration-muted-foreground')}>
            <span className="num text-muted-foreground">{formatDateTime(h.created_at, tz, today)}</span>
            <span className="font-medium">{OP_NAMES[h.operation] ?? h.operation}</span>
            <span className="text-muted-foreground">· {h.source === 'telegram' ? 'бот' : h.source === 'desktop' ? 'ПК' : 'веб'}</span>
            {h.operation === 'update' &&
              keys.map((k) => (
                <span key={k}>
                  {FIELD_NAMES[k]}: <span className="font-mono text-xs">{String(h.before?.[k] ?? '—')}</span> → <span className="font-mono text-xs">{String(h.after?.[k] ?? '—')}</span>
                </span>
              ))}
          </li>
        )
      })}
    </ol>
  )
}
