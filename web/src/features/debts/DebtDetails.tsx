// Card of a person (DEBTS.md §10): balances per currency, history (date, what, signed
// amount, balance after), «Записать возврат» (part / весь долг), «Простить остаток» (confirm), rename,
// archive (only at zero), delete (only without operations), undo of the last operation.
import { useState } from 'react'
import { Link } from 'react-router'
import { Archive, ArchiveRestore, HandCoins, Pencil, Undo2 } from 'lucide-react'
import type { DebtBalanceOut, DebtDetailOut, DebtOut, DebtPatch } from '@/lib/api/types'
import { Dec } from '@/lib/decimal'
import { formatMoney } from '@/lib/format'
import { errorMessage, loadErrorMessage, useToday, useUndo } from '@/features/operations/api'
import { formatDateShort } from '@/features/operations/lib/dates'
import { Button, ErrorNote, Field, Input, Skeleton } from '@/features/operations/ui/kit'
import { Dialog, notify, Sheet } from '@/features/operations/ui/overlays'
import { newIdempotencyKey } from '@/lib/api'
import { useDebt, useDebtOperation, useDeleteDebt, usePatchDebt, useUndoLastDebtOp } from './api'
import { DebtOpForm, type FormOp } from './DebtOpForm'
import { ageLabel, balanceDirectionText, daysBetween, historyLabel } from './lib'
import { PersonInitial } from './PersonRow'

function Block({ title, children, actions }: { title: string; children: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <section className="grid gap-2 border-b border-border py-4 first:pt-0 last:border-0">
      <div className="flex items-center gap-2">
        <h3 className="flex-1 text-[13px] font-semibold text-muted-foreground">{title}</h3>
        {actions}
      </div>
      {children}
    </section>
  )
}

export function DebtDetails({ debtId, open, onOpenChange }: { debtId: number | null; open: boolean; onOpenChange: (v: boolean) => void }) {
  const q = useDebt(debtId, { retry: false })
  const title = q.data?.debt.name ?? 'Долг'
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title={title} width={460}>
      <div data-testid="debt-details">
        {q.error && !q.data ? (
          <ErrorNote error={new Error(loadErrorMessage(q.error, 'Не удалось загрузить долг'))} onRetry={() => void q.refetch()} />
        ) : !q.data ? (
          <div className="grid gap-3"><Skeleton className="h-20" /><Skeleton className="h-40" /></div>
        ) : (
          <DetailsBody key={q.data.debt.id} data={q.data} onClose={() => onOpenChange(false)} />
        )}
      </div>
    </Sheet>
  )
}

function BalanceRow({ b, today, onRepay, onForgive }: { b: DebtBalanceOut; today: string; onRepay: () => void; onForgive: () => void }) {
  const owes = !Dec.from(b.amount).isNeg()
  const age = b.open_since ? ageLabel(Math.max(0, daysBetween(b.open_since, today))) : null
  return (
    <li className="grid gap-2 rounded-md bg-surface-2 p-3" data-testid="debt-balance" data-currency={b.currency}>
      <div className="min-w-0">
        <p className={`num text-[15px] font-semibold ${owes ? 'text-income' : ''}`} translate="no">{balanceDirectionText(b)}</p>
        <p className="num text-xs text-muted-foreground">
          {[b.currency !== 'USD' && b.approx_usd ? formatMoney(b.approx_usd.replace(/^-/, ''), 'USD', { approx: true }) : null,
            b.open_since ? `с ${formatDateShort(b.open_since, today, false)}` : null, age].filter(Boolean).join(' · ')}
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Button size="sm" variant="outline" onClick={onRepay}>{owes ? 'Записать возврат' : 'Вернуть долг'}</Button>
        <Button size="sm" variant="ghost" onClick={onForgive}>{owes ? 'Простить остаток' : 'Мне простили'}</Button>
      </div>
    </li>
  )
}

function DetailsBody({ data, onClose }: { data: DebtDetailOut; onClose: () => void }) {
  const { debt, history, total } = data
  const { today } = useToday()
  const [form, setForm] = useState<{ op: FormOp; currency?: string } | null>(null)
  const [forgive, setForgive] = useState<DebtBalanceOut | null>(null)
  const [renaming, setRenaming] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const op = useDebtOperation()
  const patch = usePatchDebt()
  const del = useDeleteDebt()
  const undo = useUndo()
  const undoLastOp = useUndoLastDebtOp()
  const balances = debt.balances.filter((b) => !Dec.from(b.amount).isZero())
  const settled = balances.length === 0
  const lastTx = history[0]?.transaction ?? null

  const doForgive = (b: DebtBalanceOut) =>
    op.mutate({ op: 'forgive', debt_id: debt.id, currency: b.currency, amount: null }, {
      onSuccess: (r) => {
        setForgive(null)
        const money = formatMoney(r.transaction.amount, r.transaction.currency)
        notify.info(`${Dec.from(b.amount).isNeg() ? 'Долг прощён' : 'Списан долг'}: ${debt.name} · ${money}`, {
          action: r.action_id ? { label: 'Отменить', onClick: () => undo.mutate({ actionId: r.action_id, key: newIdempotencyKey() }, { onError: (e) => notify.error(errorMessage(e, 'Не удалось отменить')) }) } : undefined,
        })
      },
      onError: (e) => notify.error(errorMessage(e)),
    })

  // the latest operation's own action, or — if it was edited since — the newest edit (DEBTS.md §15)
  const undoLast = () => {
    if (!lastTx) return
    undoLastOp.mutate({ ...lastTx, last_action_id: data.last_action_id ?? null }, {
      onSuccess: (r) => notify.info(r.summary || 'Отменено'),
      onError: (e) => notify.error(errorMessage(e, 'Не удалось отменить')),
    })
  }

  return (
    <div>
      <div className="flex items-center gap-3 pb-4">
        <PersonInitial name={debt.name} size={40} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[17px] font-semibold">{debt.name}</p>
          <p className="truncate text-xs text-muted-foreground">
            {[debt.handle ? `@${debt.handle.replace(/^@/, '')}` : null, debt.note, debt.is_archived ? 'в архиве' : null].filter(Boolean).join(' · ') || 'Долги не входят в доходы и расходы'}
          </p>
        </div>
        <Button size="iconSm" variant="ghost" aria-label="Переименовать" title="Переименовать" onClick={() => setRenaming((v) => !v)}><Pencil /></Button>
      </div>
      {renaming && <RenameForm debt={debt} onDone={() => setRenaming(false)} />}

      <Block title="Остаток">
        {settled ? (
          <p className="rounded-md bg-surface-2 p-3 text-sm" data-testid="debt-settled">Долг закрыт — никто никому не должен.</p>
        ) : (
          <ul className="grid gap-2">
            {balances.map((b) => (
              <BalanceRow key={b.currency} b={b} today={today} onRepay={() => setForm({ op: 'repay', currency: b.currency })} onForgive={() => setForgive(b)} />
            ))}
          </ul>
        )}
        {form?.op === 'repay' && (
          <div className="rounded-md border border-border p-3">
            <DebtOpForm
              key={form.currency}
              op="repay"
              debt={debt}
              open={[...balances.filter((b) => b.currency === form.currency), ...balances.filter((b) => b.currency !== form.currency)].map((b) => ({ currency: b.currency, amount: b.amount }))}
              onDone={() => setForm(null)}
            />
          </div>
        )}
        <div className="flex flex-wrap gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => setForm(form?.op === 'lend' ? null : { op: 'lend' })}><HandCoins /> Дать ещё</Button>
          <Button size="sm" variant="ghost" onClick={() => setForm(form?.op === 'borrow' ? null : { op: 'borrow' })}>Занять</Button>
        </div>
        {(form?.op === 'lend' || form?.op === 'borrow') && (
          <div className="rounded-md border border-border p-3">
            <DebtOpForm key={form.op} op={form.op} debt={debt} onDone={() => setForm(null)} />
          </div>
        )}
      </Block>

      <Block
        title={`История${total ? ` · ${total}` : ''}`}
        actions={lastTx ? (
          <Button size="sm" variant="ghost" loading={undoLastOp.isPending} onClick={undoLast} aria-label="Отменить последнюю операцию"><Undo2 /> Отменить последнюю</Button>
        ) : undefined}
      >
        {history.length === 0 ? (
          <p className="text-sm text-muted-foreground">Операций пока нет</p>
        ) : (
          <ol className="grid gap-0.5" data-testid="debt-history">
            {history.map((h) => {
              const neg = Dec.from(h.delta).isNeg()
              return (
                <li key={h.transaction.id}>
                  <Link to={`/operations?tx=${h.transaction.id}`} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-sm px-1 py-1.5 hover:bg-surface-2">
                    <span className="min-w-0">
                      <span className="block truncate text-[13px] font-medium">{historyLabel(h.op, h.delta)}{h.transaction.description ? ` · ${h.transaction.description}` : ''}</span>
                      <span className="block text-xs text-muted-foreground">{formatDateShort(h.transaction.occurred_on, today)}{h.transaction.status === 'needs_account' ? ' · счёт не выбран' : ''}</span>
                    </span>
                    <span className="flex flex-col items-end">
                      <span className={`num text-[13px] font-medium ${neg ? '' : 'text-income'}`} translate="no">{formatMoney(h.delta, h.currency, { sign: 'always' })}</span>
                      <span className="num text-xs text-muted-foreground" translate="no">остаток {formatMoney(h.balance_after, h.currency)}</span>
                    </span>
                  </Link>
                </li>
              )
            })}
          </ol>
        )}
        {total > history.length && (
          <Link to={`/operations?q=${encodeURIComponent(debt.name)}&period=all_time`} className="text-[13px] font-medium text-primary hover:underline">Все операции →</Link>
        )}
      </Block>

      <Block title="Архив">
        <div className="flex flex-wrap gap-2">
          {debt.is_archived ? (
            <Button size="sm" loading={patch.isPending} onClick={() => patch.mutate({ id: debt.id, patch: { version: debt.version, is_archived: false } }, { onError: (e) => notify.error(errorMessage(e)) })}>
              <ArchiveRestore /> Вернуть из архива
            </Button>
          ) : (
            <Button size="sm" disabled={!settled} title={settled ? undefined : 'Только при нулевом остатке'} loading={patch.isPending}
              onClick={() => patch.mutate({ id: debt.id, patch: { version: debt.version, is_archived: true } }, { onSuccess: () => { notify.info(`${debt.name} — в архиве`); onClose() }, onError: (e) => notify.error(errorMessage(e)) })}>
              <Archive /> Архивировать
            </Button>
          )}
          {total === 0 && (
            <Button size="sm" variant="ghost" className="text-destructive" onClick={() => setConfirmDelete(true)}>Удалить</Button>
          )}
        </div>
        {!settled && <p className="text-xs text-muted-foreground">Архивировать можно, когда долг закрыт.</p>}
      </Block>

      <Dialog
        open={forgive !== null}
        onOpenChange={(v) => !v && setForgive(null)}
        title={forgive && Dec.from(forgive.amount).isNeg() ? `Закрыть мой долг: ${debt.name}?` : `Простить долг: ${debt.name}?`}
        description={forgive ? (Dec.from(forgive.amount).isNeg()
          ? `Мой долг ${formatMoney(forgive.amount.replace(/^-/, ''), forgive.currency)} закроется и станет доходом «Прочее».`
          : `${formatMoney(forgive.amount, forgive.currency)} не вернутся — остаток станет расходом «Переводы людям». Можно отменить.`) : undefined}
        footer={
          <>
            <Button variant="ghost" onClick={() => setForgive(null)}>Отмена</Button>
            <Button variant="destructive" loading={op.isPending} onClick={() => forgive && doForgive(forgive)}>
              {forgive && Dec.from(forgive.amount).isNeg() ? 'Закрыть долг' : 'Простить'}
            </Button>
          </>
        }
      />
      <Dialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Удалить «${debt.name}»?`}
        description="Операций с этим человеком нет — удалю его из списка."
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>Отмена</Button>
            <Button variant="destructive" loading={del.isPending} onClick={() => del.mutate(debt.id, { onSuccess: () => { setConfirmDelete(false); onClose() }, onError: (e) => notify.error(errorMessage(e)) })}>Удалить</Button>
          </>
        }
      />
    </div>
  )
}

function RenameForm({ debt, onDone }: { debt: DebtOut; onDone: () => void }) {
  const patch = usePatchDebt()
  const [name, setName] = useState(debt.name)
  const [handle, setHandle] = useState(debt.handle ? `@${debt.handle.replace(/^@/, '')}` : '')
  const [note, setNote] = useState(debt.note ?? '')
  const [err, setErr] = useState<string | null>(null)
  const submit = () => {
    const n = name.trim()
    if (!n) return setErr('Имя не может быть пустым')
    const h = handle.trim().replace(/^@/, '')
    if (h && !/^[A-Za-z0-9_]{1,64}$/.test(h)) return setErr('Ник — латиница, цифры и _')
    // only what changed: an untouched field is never cleared by mistake
    const body: DebtPatch = { version: debt.version }
    if (n !== debt.name) body.name = n
    if ((h || null) !== (debt.handle?.replace(/^@/, '') || null)) body.handle = h || null
    if ((note.trim() || null) !== (debt.note || null)) body.note = note.trim() || null
    if (Object.keys(body).length === 1) return onDone()
    setErr(null)
    patch.mutate(
      { id: debt.id, patch: body },
      { onSuccess: () => { notify.info('Сохранено'); onDone() }, onError: (e) => setErr(errorMessage(e)) },
    )
  }
  return (
    <form aria-label="Переименовать" className="mb-4 grid gap-2 rounded-md border border-border p-3" onSubmit={(e) => { e.preventDefault(); submit() }}>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Имя" htmlFor="debt-name"><Input id="debt-name" value={name} maxLength={56} onChange={(e) => setName(e.target.value)} autoFocus /></Field>
        <Field label="Ник" htmlFor="debt-handle"><Input id="debt-handle" value={handle} maxLength={65} placeholder="@ник" onChange={(e) => setHandle(e.target.value)} /></Field>
      </div>
      <Field label="Заметка" htmlFor="debt-note"><Input id="debt-note" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} /></Field>
      {err && <p role="alert" className="text-sm text-destructive">{err}</p>}
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onDone}>Отмена</Button>
        <Button size="sm" type="submit" variant="primary" loading={patch.isPending}>Сохранить</Button>
      </div>
    </form>
  )
}
