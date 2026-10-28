// Compact inline edit of one operation inside a result card (DESIGN §10.10.3):
// сумма · валюта · категория · счёт · дата · описание, Enter — сохранить, Esc — отмена.
// Sends only the changed fields + version (PATCH /transactions/{id}).
import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/pf/Spinner'
import { useAccounts, useCategories, useSettings } from '@/lib/api/hooks'
import type { TransactionOut, TransactionPatch } from '@/lib/api/types'
import { parseAmountInput } from '@/lib/amountInput'
import { Dec } from '@/lib/decimal'
import { formatMoney } from '@/lib/format'
import { cn } from '@/lib/utils'

interface Props {
  tx: TransactionOut
  busy?: boolean
  onSave: (patch: TransactionPatch) => void
  onCancel: () => void
  compact?: boolean
}

const CURRENCIES = ['VND', 'USD', 'USDT', 'RUB', 'TON']
const selectCls = 'h-8 min-w-0 rounded-sm border border-input bg-card px-2 text-small outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30'

export function buildPatch(
  tx: TransactionOut,
  f: { amount: string; currency: string; categoryId: string; accountId: string; date: string; description: string },
  smallThousands = true,
): TransactionPatch | { error: string } {
  const patch: TransactionPatch = { version: tx.version }
  let currency = f.currency
  if (f.amount.trim() !== tx.amount) {
    const parsed = parseAmountInput(f.amount, f.currency, smallThousands)
    const value = parsed ? Dec.tryFrom(parsed.amount) : null
    if (!parsed || !value || !value.isPos()) return { error: 'Не понял сумму' }
    currency = parsed.asset ?? f.currency
    if (value.cmp(Dec.from(tx.amount)) !== 0) patch.amount = parsed.amount
  }
  if (currency !== tx.currency) patch.currency = currency
  if (f.categoryId && Number(f.categoryId) !== tx.category?.id) patch.category_id = Number(f.categoryId)
  const curAcc = tx.status === 'needs_account' ? '' : tx.account ? String(tx.account.id) : 'none'
  if (f.accountId !== curAcc && f.accountId !== '') {
    if (f.accountId === 'none') { patch.account_none = true; patch.account_id = null }
    else { patch.account_id = Number(f.accountId); patch.account_none = false }
  }
  if (f.date && f.date !== tx.occurred_on) patch.occurred_on = f.date
  const desc = f.description.trim() || null
  if (desc !== (tx.description ?? null)) patch.description = desc
  return patch
}

export function TxEditForm({ tx, busy, onSave, onCancel, compact }: Props) {
  const { data: accounts } = useAccounts()
  const { data: categories } = useCategories()
  const { data: settings } = useSettings()
  const [amount, setAmount] = useState(tx.amount)
  const [currency, setCurrency] = useState(tx.currency)
  const [categoryId, setCategoryId] = useState(tx.category ? String(tx.category.id) : '')
  const [accountId, setAccountId] = useState(tx.status === 'needs_account' ? '' : tx.account ? String(tx.account.id) : 'none')
  const [date, setDate] = useState(tx.occurred_on)
  const [description, setDescription] = useState(tx.description ?? '')
  const [error, setError] = useState<string | null>(null)

  const kind = tx.category?.kind ?? (tx.kind === 'income' ? 'income' : 'expense')
  const cats = useMemo(() => (categories ?? []).filter((c) => c.kind === kind && !c.is_archived), [categories, kind])
  const currencies = CURRENCIES.includes(tx.currency) ? CURRENCIES : [tx.currency, ...CURRENCIES]
  const hasCategory = tx.kind === 'expense' || tx.kind === 'income' || tx.kind === 'refund'
  const preview = amount.trim() === tx.amount ? { amount: tx.amount, asset: currency } : parseAmountInput(amount, currency, settings?.small_amount_thousands ?? true)

  const save = () => {
    const p = buildPatch(tx, { amount, currency, categoryId, accountId, date, description }, settings?.small_amount_thousands ?? true)
    if ('error' in p) { setError(p.error); return }
    if (Object.keys(p).length === 1) { onCancel(); return }
    setError(null)
    onSave(p)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); save() }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onCancel() }
  }

  return (
    <div className="mt-2 flex flex-col gap-2" onKeyDown={onKeyDown} data-testid="tx-edit-form">
      <div className={cn('grid gap-2', compact ? 'grid-cols-2' : 'grid-cols-2 sm:grid-cols-4')}>
        <label className="col-span-1 flex flex-col gap-1">
          <span className="text-caption text-muted-foreground">Сумма</span>
          <div className="flex gap-1">
            <Input
              autoFocus
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
              className="num h-8 min-w-0"
              aria-invalid={!!error || undefined}
              data-testid="edit-amount"
            />
            <select value={currency} onChange={(e) => setCurrency(e.target.value)} className={cn(selectCls, 'w-[76px] min-w-[76px] shrink-0')} aria-label="Валюта" data-testid="edit-currency">
              {currencies.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        </label>
        {hasCategory && (
          <label className="flex flex-col gap-1">
            <span className="text-caption text-muted-foreground">Категория</span>
            <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)} className={selectCls} data-testid="edit-category">
              {!categoryId && <option value="">—</option>}
              {cats.map((c) => <option key={c.id} value={c.id}>{c.name}{c.is_new ? ' 🆕' : ''}</option>)}
              {tx.category && !cats.some((c) => c.id === tx.category!.id) && <option value={tx.category.id}>{tx.category.name}</option>}
            </select>
          </label>
        )}
        <label className="flex flex-col gap-1">
          <span className="text-caption text-muted-foreground">Счёт</span>
          <select value={accountId} onChange={(e) => setAccountId(e.target.value)} className={selectCls} data-testid="edit-account">
            {accountId === '' && <option value="">не выбран</option>}
            {(accounts ?? []).filter((a) => !a.is_archived).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            <option value="none">Без счёта</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-caption text-muted-foreground">Дата</span>
          <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="h-8" />
        </label>
      </div>
      {!compact && (
        <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Описание" className="h-8" />
      )}
      <div className="flex items-center gap-2">
        <span className={cn('num flex-1 truncate text-caption', error ? 'text-destructive' : 'text-muted-foreground')}>
          {error ?? (preview ? `= ${formatMoney(preview.amount, preview.asset ?? currency)}` : 'Не понял сумму')}
        </span>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>Отмена</Button>
        <Button type="button" size="sm" onClick={save} disabled={busy} aria-busy={busy || undefined} data-testid="edit-save">
          {busy && <Spinner size={14} />} Сохранить
        </Button>
      </div>
    </div>
  )
}
