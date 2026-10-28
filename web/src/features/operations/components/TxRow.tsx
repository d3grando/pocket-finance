// One operation row (DESIGN §9.4): desktop 44 px with columns, mobile 56 px two-line with swipe actions.
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { motion, useAnimationControls } from 'motion/react'
import { Copy, Ellipsis, Pencil, Tag, Trash2, WalletCards } from 'lucide-react'
import type { AccountOut, TransactionOut } from '@/lib/api'
import { cn } from '@/lib/utils'
import { errorMessage, useAssignAccount } from '../api'
import { formatTime } from '../lib/dates'
import { isFiatCurrency } from '@/lib/format'
import { txAmountView, txTitle } from '../lib/tx'
import { TxIcon } from '../ui/icons'
import { Button } from '../ui/kit'
import { Menu, Popover, notify } from '../ui/overlays'
import { useReducedMotion } from '../ui/hooks'
import { accountQuestion, debtRealAccount } from '@/features/debts/lib'

export interface RowActions {
  onOpen: (tx: TransactionOut, focus?: 'amount' | 'category') => void
  onDelete: (tx: TransactionOut) => void
  onDuplicate: (tx: TransactionOut) => void
}

const toneClass = { foreground: 'text-foreground', income: 'text-income', transfer: 'text-transfer' }

export function AccountChooser({ tx, accounts, compact }: { tx: TransactionOut; accounts: AccountOut[]; compact?: boolean }) {
  const [open, setOpen] = useState(false)
  const assign = useAssignAccount()
  const live = accounts.filter((a) => !a.is_archived)
  const fit = live.filter((a) => a.is_multi_asset || a.currency === tx.currency)
  // a coin cannot be charged from a single-currency account (ledger 422)
  const rest = isFiatCurrency(tx.currency) ? live.filter((a) => !fit.includes(a)) : []
  const choose = (id: number | null) =>
    assign.mutate(
      { id: tx.id, accountId: id, version: tx.version },
      {
        onSuccess: () => {
          setOpen(false)
          notify.info(id === null ? 'Записал без счёта' : `Счёт: ${live.find((a) => a.id === id)?.name ?? ''}`)
        },
        onError: (e) => notify.error(errorMessage(e)),
      },
    )
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      className="w-60 p-1"
      trigger={
        <button
          type="button"
          onClick={(e) => e.stopPropagation()}
          className={cn('inline-flex h-6 items-center gap-1 rounded-full bg-warning-soft px-2 text-xs font-medium text-warning hover:brightness-95 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none', compact && 'h-5 px-1.5')}
        >
          <WalletCards className="size-3" aria-hidden /> Выбрать счёт
        </button>
      }
    >
      {/* The popover is portalled, but React events still bubble to the row («open edit form») — stop them here. */}
      <div onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      <p className="px-2 pt-1 pb-1.5 text-xs font-medium text-muted-foreground">{accountQuestion(tx)}</p>
      {[...fit, ...rest].map((a, i) => (
        <button key={a.id} type="button" disabled={assign.isPending} onClick={() => choose(a.id)} className="flex h-9 w-full items-center gap-2 rounded-sm px-2 text-left text-sm hover:bg-accent disabled:opacity-50">
          <span className="flex-1 truncate">{a.name}</span>
          {i < 9 && <span className="text-[11px] text-muted-foreground">{a.is_multi_asset ? 'кошелёк' : a.currency}</span>}
        </button>
      ))}
      <div className="my-1 h-px bg-border" />
      <button type="button" disabled={assign.isPending} onClick={() => choose(null)} className="flex h-9 w-full items-center rounded-sm px-2 text-left text-sm text-muted-foreground hover:bg-accent">
        Без счёта
      </button>
      </div>
    </Popover>
  )
}

function AmountCell({ tx, className }: { tx: TransactionOut; className?: string }) {
  const v = txAmountView(tx)
  return (
    <div className={cn('flex min-w-0 flex-col items-end text-right', className)}>
      <span className={cn('num text-sm font-medium whitespace-nowrap max-md:text-[15px]', toneClass[v.tone])} translate="no">
        {v.main}
      </span>
      {v.sub && tx.kind !== 'transfer' && tx.kind !== 'exchange' && (
        <span className="num truncate text-xs text-muted-foreground" translate="no">
          {v.sub}
        </span>
      )}
    </div>
  )
}

function rowMenu(tx: TransactionOut, a: RowActions, offline: boolean) {
  const regular = tx.kind === 'expense' || tx.kind === 'income' || tx.kind === 'refund'
  return [
    { label: 'Изменить', icon: <Pencil />, onSelect: () => a.onOpen(tx), disabled: offline, hint: <span className="text-[11px] text-muted-foreground">⏎</span> },
    ...(regular ? [{ label: 'Сменить категорию', icon: <Tag />, onSelect: () => a.onOpen(tx, 'category'), disabled: offline, hint: <span className="text-[11px] text-muted-foreground">E</span> }] : []),
    ...(regular ? [{ label: 'Дублировать', icon: <Copy />, onSelect: () => a.onDuplicate(tx), disabled: offline }] : []),
    'sep' as const,
    { label: 'Удалить', icon: <Trash2 />, onSelect: () => a.onDelete(tx), danger: true, disabled: offline, hint: <span className="text-[11px] text-muted-foreground">Del</span> },
  ]
}

/** «Дал в долг: Коля» with the name linking to the card of the person (DEBTS.md §10); other titles as is. */
function TitleText({ tx, title, className }: { tx: TransactionOut; title: string; className?: string }) {
  if (!tx.debt) return <span className={className}>{title}</span>
  const name = tx.debt.name
  const head = title.endsWith(name) ? title.slice(0, title.length - name.length) : `${title} · `
  return (
    <span className={className}>
      {head}
      <Link
        to={`/accounts?debt=${tx.debt.id}`}
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
        className="underline decoration-muted-foreground/40 underline-offset-2 hover:decoration-foreground"
        title={`Открыть долг: ${name}`}
      >
        {name}
      </Link>
    </span>
  )
}

/** Account column: transfers «A → B»; debt operations show only the real account (the other side is the person). */
function accountText(tx: TransactionOut): string | null {
  if (tx.status === 'needs_account') return null
  if (tx.debt) return tx.debt.op === 'forgive' ? 'долг' : (debtRealAccount(tx)?.name ?? 'без счёта')
  if (tx.kind === 'transfer' || tx.kind === 'exchange') return `${tx.account?.name ?? '—'} → ${tx.counter_account?.name ?? '—'}`
  return tx.account?.name ?? 'без счёта'
}

export function TxRow({ tx, tz, accounts, selected, actions, offline, mobile, compact, below }: { tx: TransactionOut; tz: string; accounts: AccountOut[]; selected?: boolean; actions: RowActions; offline: boolean; mobile: boolean; compact?: boolean; below?: ReactNode }) {
  const { title, category, categoryDistinct } = txTitle(tx)
  const time = formatTime(tx.occurred_at, tz)
  const accountLabel = accountText(tx)
  const isNew = !!tx.category?.is_new
  if (mobile) return <MobileRow {...{ tx, title, category: categoryDistinct ? category : null, time, accountLabel, accounts, actions, offline, selected, isNew }} below={below} />
  return (
    <div data-tx-id={tx.id} className={cn('group relative', selected && 'bg-surface-2')}>
      <div
        role="button"
        tabIndex={-1}
        onClick={() => !offline && actions.onOpen(tx)}
        className={cn('grid cursor-pointer grid-cols-[28px_minmax(0,1fr)_120px_56px_minmax(140px,200px)_64px] items-center gap-3 rounded-md px-3 transition-colors duration-[80ms] hover:bg-surface-2 lg:grid-cols-[28px_minmax(0,1fr)_150px_120px_56px_minmax(140px,200px)_64px]', compact ? 'min-h-9 py-1' : 'min-h-11 py-1.5', selected && 'ring-1 ring-ring/30')}
      >
        <TxIcon tx={tx} />
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-1.5">
            <TitleText tx={tx} title={title} className="truncate text-sm font-medium" />
            {isNew && <span title="Новая категория, создана автоматически">🆕</span>}
          </div>
          {categoryDistinct && !compact && <div className="truncate text-[13px] text-muted-foreground lg:hidden">{category}</div>}
        </div>
        <div className="hidden truncate text-[13px] text-muted-foreground lg:block">{category ?? '—'}</div>
        <div className="min-w-0 truncate text-[13px] text-muted-foreground">{accountLabel ?? <AccountChooser tx={tx} accounts={accounts} />}</div>
        <div className="num text-[13px] text-muted-foreground">{time}</div>
        <AmountCell tx={tx} />
        <div className="flex justify-end gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 max-[1023px]:opacity-100" onClick={(e) => e.stopPropagation()}>
          <Button size="iconSm" variant="ghost" aria-label="Изменить" title={offline ? 'Нет связи' : 'Изменить'} disabled={offline} onClick={() => actions.onOpen(tx)}>
            <Pencil />
          </Button>
          <Menu label="Действия" items={rowMenu(tx, actions, offline)} trigger={<Button size="iconSm" variant="ghost" aria-label="Ещё действия"><Ellipsis /></Button>} />
        </div>
      </div>
      {below}
    </div>
  )
}

function MobileRow({ tx, title, category, time, accountLabel, accounts, actions, offline, selected, isNew, below }: { tx: TransactionOut; title: string; category: string | null; time: string; accountLabel: string | null; accounts: AccountOut[]; actions: RowActions; offline: boolean; selected?: boolean; isNew: boolean; below?: ReactNode }) {
  const controls = useAnimationControls()
  const reduce = useReducedMotion()
  const [revealed, setRevealed] = useState(false)
  const [dragging, setDragging] = useState(false)
  const spring = reduce ? { duration: 0 } : { type: 'spring' as const, stiffness: 380, damping: 38 }
  const close = () => {
    setRevealed(false)
    void controls.start({ x: 0, transition: spring })
  }
  const meta = [category ?? (tx.debt ? 'Долг' : tx.kind === 'transfer' ? 'Перевод' : tx.kind === 'exchange' ? 'Обмен' : null), accountLabel, time].filter(Boolean).join(' · ')
  return (
    <div data-tx-id={tx.id} className={cn('relative overflow-hidden', selected && 'bg-surface-2')}>
      <div className={cn('absolute inset-y-0 right-0 flex', !revealed && !dragging && 'invisible')} aria-hidden={!revealed}>
        <button type="button" tabIndex={revealed ? 0 : -1} disabled={offline} onClick={() => { close(); actions.onOpen(tx) }} className="w-[72px] bg-primary text-sm font-medium text-primary-foreground">
          Изменить
        </button>
        <button type="button" tabIndex={revealed ? 0 : -1} disabled={offline} onClick={() => { close(); actions.onDelete(tx) }} className="w-[72px] bg-destructive text-sm font-medium text-destructive-foreground">
          Удалить
        </button>
      </div>
      <motion.div
        drag={offline ? false : 'x'}
        dragConstraints={{ left: -144, right: 0 }}
        dragElastic={0.08}
        dragDirectionLock
        animate={controls}
        onDragStart={() => setDragging(true)}
        onDragEnd={(_, info) => {
          setDragging(false)
          const open = info.offset.x < -58 || info.velocity.x < -400
          setRevealed(open)
          void controls.start({ x: open ? -144 : 0, transition: spring })
        }}
        onClick={() => (revealed ? close() : !offline && actions.onOpen(tx))}
        className="relative grid min-h-14 grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-3 bg-background px-1 py-2 active:bg-surface-2"
        role="button"
      >
        <TxIcon tx={tx} />
        <div className="min-w-0">
          <div className="flex items-center gap-1">
            <TitleText tx={tx} title={title} className="truncate text-[15px] font-medium" />
            {isNew && <span>🆕</span>}
          </div>
          <div className="flex min-w-0 items-center gap-1.5 truncate text-[13px] text-muted-foreground">
            <span className="truncate">{meta}</span>
            {tx.status === 'needs_account' && <AccountChooser tx={tx} accounts={accounts} compact />}
          </div>
        </div>
        <AmountCell tx={tx} className="max-w-[45vw]" />
      </motion.div>
      {below}
    </div>
  )
}
