// Онбординг остатков (DESIGN §10.9): one screen, any account can be skipped (no balance
// is shown for it then). Fields understand «2.5м», «800к», «312.4 USDT, 3 TON». POST /accounts/onboarding.
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { CircleAlert, CornerDownLeft, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Page } from '@/components/pf/Page'
import { Spinner } from '@/components/pf/Spinner'
import { IconTile, accountIcon } from '@/components/pf/icons'
import { errorText } from '@/lib/api/client'
import { accountsApi } from '@/lib/api/endpoints'
import { useAccounts, useRates, useSettings } from '@/lib/api/hooks'
import { qk } from '@/lib/api/keys'
import type { AccountOut, OnboardingBalancesIn, RatesLatestOut } from '@/lib/api/types'
import { parseAmountInput, parseAssetList, type ParsedAmount } from '@/lib/amountInput'
import { Dec } from '@/lib/decimal'
import { currencySymbol, formatMoney } from '@/lib/format'
import { cn } from '@/lib/utils'

type Row = { raw: string; skipped: boolean }

export function parseRow(a: AccountOut, raw: string, smallThousands: boolean): ParsedAmount[] | null | 'empty' {
  if (!raw.trim()) return 'empty'
  if (a.is_multi_asset) return parseAssetList(raw, null, smallThousands)
  const p = parseAmountInput(raw, a.currency, smallThousands)
  if (!p) return null
  if (p.asset && p.asset !== a.currency) return null
  return [{ amount: p.amount, asset: a.currency ?? p.asset ?? 'VND' }]
}

/** ≈ USD of known amounts using /rates/latest (display only). */
export function approxUsd(items: ParsedAmount[], rates: RatesLatestOut | undefined): Dec | null {
  if (!rates) return null
  let sum = Dec.ZERO
  for (const it of items) {
    const v = Dec.from(it.amount)
    if (it.asset === 'USD') sum = sum.add(v)
    else if (it.asset === 'VND' && rates.usd_vnd) sum = sum.add(v.div(rates.usd_vnd.rate))
    else if (it.asset === 'RUB' && rates.usd_rub) sum = sum.add(v.div(rates.usd_rub.rate))
    else {
      const r = rates.crypto.find((c) => c.base === it.asset && c.quote === 'USD')
      if (r) sum = sum.add(v.mul(r.rate))
      else if (it.asset === 'USDT' || it.asset === 'USDC') sum = sum.add(v)
    }
  }
  return sum
}

export default function OnboardingPage() {
  const nav = useNavigate()
  const qc = useQueryClient()
  const { data: accounts = [], isPending } = useAccounts()
  const { data: settings } = useSettings()
  const { data: rates } = useRates()
  const small = settings?.small_amount_thousands ?? true
  const [rows, setRows] = useState<Record<number, Row>>({})
  const [busy, setBusy] = useState<'done' | 'skip' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState('')
  const [newCur, setNewCur] = useState('VND')

  const list = accounts.filter((a) => !a.is_archived)
  const parsed = useMemo(() => list.map((a) => {
    const row = rows[a.id] ?? { raw: '', skipped: false }
    return { a, row, res: row.skipped ? 'empty' as const : parseRow(a, row.raw, small) }
  }), [list, rows, small])
  const items = parsed.flatMap((p) => (Array.isArray(p.res) ? p.res.map((x) => ({ account_id: p.a.id, asset: x.asset!, amount: x.amount })) : []))
  const invalid = parsed.some((p) => p.res === null)
  const total = approxUsd(items.map((i) => ({ amount: i.amount, asset: i.asset })), rates)

  const submit = async (skipAll: boolean) => {
    if (busy) return
    if (!skipAll && invalid) { setError('Исправьте суммы, которые не получилось разобрать, или пропустите эти счета'); return }
    setBusy(skipAll ? 'skip' : 'done')
    setError(null)
    try {
      const body: OnboardingBalancesIn = { items: skipAll ? [] : items }
      await accountsApi.onboarding(body)
      qc.setQueryData(qk.me, (m: unknown) => (m && typeof m === 'object' ? { ...m, onboarding_done: true } : m))
      await Promise.all([qc.invalidateQueries({ queryKey: qk.me }), qc.invalidateQueries({ queryKey: qk.balances }), qc.invalidateQueries({ queryKey: qk.accounts })])
      nav('/', { replace: true })
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(null)
    }
  }

  const addAccount = async () => {
    const name = newName.trim()
    if (!name) return
    try {
      const multi = newCur === '*'
      await accountsApi.create({ name, kind: multi ? 'crypto_wallet' : 'other', currency: multi ? null : newCur, is_multi_asset: multi })
      await qc.invalidateQueries({ queryKey: qk.accounts })
      setNewName('')
      setAdding(false)
    } catch (e) {
      setError(errorText(e))
    }
  }

  const set = (id: number, patch: Partial<Row>) => setRows((r) => ({ ...r, [id]: { ...(r[id] ?? { raw: '', skipped: false }), ...patch } }))

  return (
    <Page className="flex min-h-dvh max-w-[640px] flex-col justify-center py-6">
      <div className="rounded-lg border bg-card p-4 md:p-6" data-testid="onboarding">
        <p className="text-caption text-muted-foreground">Шаг 1 из 1</p>
        <h1 className="text-title-2">Сколько сейчас на счетах?</h1>
        <p className="mt-1 text-small text-muted-foreground">Можно пропустить любой — тогда остаток просто не показываем.</p>
        <form
          className="mt-5 flex flex-col gap-1"
          onSubmit={(e) => { e.preventDefault(); void submit(false) }}
        >
          {isPending && [0, 1, 2, 3].map((i) => <div key={i} className="pf-pulse h-11 rounded-sm bg-muted/60" />)}
          {parsed.map(({ a, row, res }, idx) => (
            <div key={a.id} className={cn('grid grid-cols-[28px_1fr] items-start gap-x-3 gap-y-0.5 py-1.5 sm:grid-cols-[28px_140px_1fr_auto]', row.skipped && 'opacity-60')}>
              <IconTile icon={accountIcon(a)} className="mt-1.5" />
              <label htmlFor={`ob-${a.id}`} className="truncate pt-2 text-body">{a.name}</label>
              <div className="col-span-2 flex flex-col sm:col-span-1">
                <div className="flex items-center gap-2">
                  <Input
                    id={`ob-${a.id}`}
                    value={row.raw}
                    onChange={(e) => set(a.id, { raw: e.target.value, skipped: false })}
                    placeholder={a.is_multi_asset ? '312.4 USDT, 3 TON' : a.currency === 'VND' ? '34 120 000 или 2.5м' : '0'}
                    inputMode={a.is_multi_asset ? 'text' : 'decimal'}
                    aria-invalid={res === null || undefined}
                    className="num"
                    data-testid={`ob-input-${idx}`}
                    enterKeyHint={idx === parsed.length - 1 ? 'done' : 'next'}
                    onKeyDown={(e) => {
                      if (e.key !== 'Enter' || idx === parsed.length - 1) return
                      e.preventDefault()
                      document.querySelector<HTMLInputElement>(`[data-testid="ob-input-${idx + 1}"]`)?.focus()
                    }}
                  />
                  {!a.is_multi_asset && <span className="w-8 text-small text-muted-foreground">{currencySymbol(a.currency ?? '')}</span>}
                </div>
                <span className={cn('num min-h-4 text-caption', res === null ? 'text-destructive' : 'text-muted-foreground')}>
                  {res === null ? 'Не понял сумму' : Array.isArray(res) ? `= ${res.map((x) => formatMoney(x.amount, x.asset!)).join(', ')}` : ''}
                </span>
              </div>
              <button type="button" className="hidden pt-2 text-small text-muted-foreground hover:text-foreground sm:block" onClick={() => set(a.id, { raw: '', skipped: true })}>
                Пропустить
              </button>
            </div>
          ))}
          {adding ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Название счёта" className="w-48" autoFocus
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void addAccount() } }} />
              <select value={newCur} onChange={(e) => setNewCur(e.target.value)} className="h-9 rounded-sm border border-input bg-card px-2 text-small" aria-label="Валюта счёта">
                <option value="VND">₫ VND</option><option value="USD">$ USD</option><option value="USDT">USDT</option><option value="RUB">₽ RUB</option><option value="*">Кошелёк (любые монеты)</option>
              </select>
              <Button type="button" size="sm" onClick={() => void addAccount()}>Добавить</Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setAdding(false)}>Отмена</Button>
            </div>
          ) : (
            <Button type="button" variant="ghost" size="sm" className="mt-1 self-start" onClick={() => setAdding(true)}>
              <Plus /> Другой счёт
            </Button>
          )}
          <div className="mt-4 flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center">
            <span className="num flex-1 text-small text-muted-foreground">
              {items.length ? `Итого известно ${total ? formatMoney(total.toString(), 'USD', { approx: true }) : '—'}` : 'Ничего не указано — можно заполнить позже в «Счетах»'}
            </span>
            {error && <span className="flex items-center gap-1 text-small text-destructive sm:hidden"><CircleAlert className="size-4" /> {error}</span>}
            <div className="sticky bottom-[max(8px,env(safe-area-inset-bottom))] flex gap-2">
              <Button type="button" variant="ghost" onClick={() => void submit(true)} disabled={busy !== null} data-testid="ob-skip">
                {busy === 'skip' && <Spinner size={14} />} Пропустить всё
              </Button>
              <Button type="submit" disabled={busy !== null} data-testid="ob-done">
                {busy === 'done' && <Spinner size={14} />} Готово <CornerDownLeft className="opacity-70" />
              </Button>
            </div>
          </div>
          {error && <p className="hidden items-center gap-1 text-small text-destructive sm:flex" role="alert"><CircleAlert className="size-4" /> {error}</p>}
        </form>
      </div>
    </Page>
  )
}
