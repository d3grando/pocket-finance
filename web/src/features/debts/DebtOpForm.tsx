// Form for POST /debts/operations (DEBTS.md §10): «Дать в долг» / «Занять» (person with
// autocomplete from /debts?include_settled=true, amount, currency, account, date) and «Записать возврат»
// for a known person (part or «весь долг» = amount null). Nothing is shown as saved before a 2xx.
import { useMemo, useState } from 'react'
import type { AccountOut, DebtOpCreate, DebtOpKind, DebtOut } from '@/lib/api/types'
import { parseAmountInput } from '@/lib/amountInput'
import { formatMoney } from '@/lib/format'
import { errorMessage, useAccounts, useSettings, useToday } from '@/features/operations/api'
import { CurrencySelect } from '@/features/operations/components/CurrencySelect'
import { useUndoToast } from '@/features/operations/components/useUndoToast'
import { addDays } from '@/features/operations/lib/dates'
import { Button, Chip, Field, Input, NativeSelect } from '@/features/operations/ui/kit'
import { notify } from '@/features/operations/ui/overlays'
import { useDebtOperation, useDebts } from './api'
import { accountQuestion, accountsFor, allPeople, excessText, isInflowOp, matchPeople, opVerb } from './lib'

export type FormOp = 'lend' | 'borrow' | 'repay'

const DEFAULT_HINT: Record<string, string> = { VND: 'Для ₫ — Карта', RUB: 'Для ₽ — Карта RUB' }
const SUBMIT: Record<DebtOpKind, string> = { lend: 'Дать в долг', borrow: 'Занять', repay_in: 'Записать возврат', repay_out: 'Я вернул', forgive: 'Списать' }

function PersonField({ value, onChange, picked, onPick }: { value: string; onChange: (v: string) => void; picked: DebtOut | null; onPick: (d: DebtOut | null) => void }) {
  const known = useDebts(true, { retry: false })
  const [focused, setFocused] = useState(false)
  const people = useMemo(() => allPeople(known.data), [known.data])
  const hits = picked ? [] : matchPeople(people, value)
  const exact = people.find((d) => d.name.toLowerCase() === value.trim().toLowerCase()) ?? null
  return (
    <Field label="Кто" htmlFor="debt-person" hint={picked ? 'Известный человек — остаток посчитаю вместе' : value.trim() && !exact ? 'Новый человек — запомню' : 'Имя или @ник'}>
      <div className="relative">
        <Input
          id="debt-person"
          value={value}
          autoComplete="off"
          maxLength={56}
          placeholder="Коля"
          role="combobox"
          aria-expanded={focused && hits.length > 0}
          aria-controls="debt-person-list"
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 120)}
          onChange={(e) => { onChange(e.target.value); onPick(null) }}
        />
        {focused && hits.length > 0 && (
          <ul id="debt-person-list" role="listbox" className="absolute inset-x-0 top-full z-10 mt-1 max-h-56 overflow-auto rounded-md border border-border bg-popover p-1 shadow-md">
            {hits.map((d) => (
              <li key={d.id} role="option" aria-selected={false}>
                <button
                  type="button"
                  className="flex h-9 w-full items-center gap-2 rounded-sm px-2 text-left text-sm hover:bg-accent"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => { onChange(d.name); onPick(d); setFocused(false) }}
                >
                  <span className="flex-1 truncate">{d.name}</span>
                  {d.handle && <span className="text-xs text-muted-foreground">@{d.handle.replace(/^@/, '')}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Field>
  )
}

function AccountSelect({ id, accounts, currency, value, onChange, question }: { id: string; accounts: AccountOut[]; currency: string; value: string; onChange: (v: string) => void; question: string }) {
  const list = accountsFor(accounts, currency)
  return (
    <Field label={question.replace('?', '')} htmlFor={id} hint={value === '' ? DEFAULT_HINT[currency] ?? `Спрошу «${question}» кнопками` : undefined}>
      <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">По умолчанию</option>
        {list.map((a) => (
          <option key={a.id} value={a.id}>{a.name}{a.is_multi_asset ? ' (кошелёк)' : a.currency ? ` · ${a.currency}` : ''}</option>
        ))}
        <option value="none">Без счёта</option>
      </NativeSelect>
    </Field>
  )
}

export interface DebtOpFormProps {
  /** lend / borrow (new or known person) or repay for `debt` (direction by the balance in the currency) */
  op: FormOp
  debt?: DebtOut | null
  /** repay: currencies with an open balance (signed amounts) */
  open?: { currency: string; amount: string }[]
  onDone: (r: { debtId: number }) => void
}

export function DebtOpForm({ op, debt = null, open = [], onDone }: DebtOpFormProps) {
  const { today } = useToday()
  const settings = useSettings()
  const accounts = useAccounts(false)
  const undoToast = useUndoToast()
  const mutation = useDebtOperation()
  const [name, setName] = useState(debt?.name ?? '')
  const [picked, setPicked] = useState<DebtOut | null>(debt)
  const [amount, setAmount] = useState('')
  const [currency, setCurrency] = useState(open[0]?.currency ?? 'VND')
  const [all, setAll] = useState(op === 'repay')
  const [account, setAccount] = useState('')
  const [date, setDate] = useState(today)
  const [description, setDescription] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const small = settings.data?.small_amount_thousands ?? true
  const parsed = amount.trim() ? parseAmountInput(amount, currency, small) : null
  const cur = parsed?.asset ?? currency
  const balance = open.find((b) => b.currency === cur)
  const realOp: DebtOpKind = op === 'repay' ? (balance && balance.amount.startsWith('-') ? 'repay_out' : 'repay_in') : op
  const question = accountQuestion({ kind: 'transfer', debt: { id: 0, name: '', op: realOp } })
  const remaining = balance ? formatMoney(balance.amount.replace(/^-/, ''), cur) : null

  const submit = () => {
    const who = name.trim()
    if (!picked && !who) return setErr('Укажите, кто')
    if (!(op === 'repay' && all)) {
      if (!parsed || parsed.amount.startsWith('-') || parsed.amount === '0') return setErr('Укажите сумму больше нуля')
    }
    if (op === 'repay' && !balance) return setErr(`Долга в ${cur} нет`)
    const body: DebtOpCreate = {
      op: realOp,
      ...(picked ? { debt_id: picked.id } : { person_name: who.replace(/^@/, '') }),
      ...(!picked && who.startsWith('@') ? { handle: who.slice(1) } : {}),
      amount: op === 'repay' && all ? null : parsed!.amount,
      currency: cur,
      account_id: account && account !== 'none' ? Number(account) : null,
      account_none: account === 'none',
      description: description.trim() || null,
      occurred_on: date,
    }
    setErr(null)
    mutation.mutate(body, {
      onSuccess: (r) => {
        const money = formatMoney(r.transaction.amount, r.transaction.currency)
        const extra = excessText({ debt_id: r.debt.id, name: r.debt.name, op: realOp, currency: cur, balance_after: r.balance_after, excess: r.excess })
        undoToast(`${opVerb(realOp)}: ${r.debt.name} · ${money}${r.needs_account ? ' · счёт не выбран — «Разобрать»' : ''}`, r.action_id)
        if (extra) notify.info(extra)
        onDone({ debtId: r.debt.id })
      },
      onError: (e) => setErr(errorMessage(e)),
    })
  }

  const inflow = isInflowOp(realOp)
  return (
    <form className="grid gap-3" aria-label={op === 'repay' ? 'Возврат долга' : SUBMIT[op]} onSubmit={(e) => { e.preventDefault(); submit() }}>
      {op !== 'repay' && !debt && <PersonField value={name} onChange={setName} picked={picked} onPick={setPicked} />}
      {op === 'repay' && open.length > 1 && (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Валюта долга">
          {open.map((b) => (
            <Chip key={b.currency} active={cur === b.currency} onClick={() => { setCurrency(b.currency); setAmount('') }}>{b.currency}</Chip>
          ))}
        </div>
      )}
      {op === 'repay' && (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Сколько">
          <Chip active={all} onClick={() => setAll(true)}>Весь долг{remaining ? ` · ${remaining}` : ''}</Chip>
          <Chip active={!all} onClick={() => setAll(false)}>Часть</Chip>
        </div>
      )}
      {!(op === 'repay' && all) && (
        <div className="grid grid-cols-[minmax(0,1fr)_120px] gap-2">
          <Field label="Сумма" htmlFor="debt-amount" hint={parsed ? `= ${formatMoney(parsed.amount, cur)}` : 'например 500к или 50 USDT'}>
            <Input id="debt-amount" inputMode="decimal" autoFocus={op === 'repay'} value={amount} onChange={(e) => setAmount(e.target.value)} className="num" placeholder="0" />
          </Field>
          <Field label="Валюта" htmlFor="debt-currency">
            {op === 'repay' ? <Input id="debt-currency" value={cur} disabled /> : <CurrencySelect id="debt-currency" value={cur} onChange={setCurrency} />}
          </Field>
        </div>
      )}
      <div className="grid grid-cols-2 gap-2">
        <AccountSelect id="debt-account" accounts={accounts.data ?? []} currency={cur} value={account} onChange={setAccount} question={question} />
        <Field label="Дата" htmlFor="debt-date">
          <Input id="debt-date" type="date" value={date} max={addDays(today, 1)} onChange={(e) => setDate(e.target.value)} />
        </Field>
      </div>
      <Field label="Комментарий" htmlFor="debt-desc">
        <Input id="debt-desc" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} />
      </Field>
      <p className="text-xs text-muted-foreground">
        {inflow ? 'Деньги придут на счёт, ' : 'Деньги уйдут со счёта, '}но это не доход и не расход — только долг.
      </p>
      {err && <p role="alert" className="rounded-sm bg-expense-soft px-2 py-1.5 text-sm text-destructive">{err}</p>}
      <Button type="submit" variant="primary" loading={mutation.isPending}>{SUBMIT[realOp]}</Button>
    </form>
  )
}
