// Transaction row (DESIGN §9.4) — compact read-only variant used on Home and in
// Quick Capture. the pages may reuse it in «Операции» (props are stable).
import { Inbox } from 'lucide-react'
import type { TransactionOut } from '@/lib/api/types'
import { DEFAULT_TZ, formatRelative, formatTime } from '@/lib/format'
import { txAccountLabel, txAmount, txApprox, txTitle } from '@/lib/tx'
import { cn } from '@/lib/utils'
import { IconTile, txIcon } from './icons'

interface Props {
  tx: TransactionOut
  tz?: string
  /** «time» = only HH:MM (inside a day group), «relative» = «вчера, 22:40» */
  when?: 'time' | 'relative'
  dense?: boolean
  highlight?: boolean
  onClick?: () => void
  onChooseAccount?: () => void
  className?: string
}

export function TxRow({ tx, tz = DEFAULT_TZ, when = 'relative', dense, highlight, onClick, onChooseAccount, className }: Props) {
  const amount = txAmount(tx)
  const approx = txApprox(tx)
  const title = txTitle(tx)
  const cat = tx.category?.name
  const acc = txAccountLabel(tx)
  const time = when === 'time' ? formatTime(tx.occurred_at, tz) : formatRelative(tx.occurred_at, new Date(), tz)
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      data-testid="tx-row"
      className={cn(
        'flex w-full items-center gap-3 rounded-sm px-3 text-left transition-colors',
        dense ? 'min-h-10 py-1.5' : 'min-h-14 py-2 md:min-h-11',
        onClick && 'hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none',
        highlight && 'pf-highlight',
        className,
      )}
    >
      <IconTile icon={txIcon(tx)} business={tx.is_business} size={dense ? 24 : 28} />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-body font-medium">{title}</span>
          {tx.category?.is_new && (
            <span className="shrink-0 rounded-full bg-primary-soft px-1.5 text-caption text-primary" title="Новая категория, создана автоматически">🆕</span>
          )}
        </span>
        {!dense && (
          <span className="flex min-w-0 items-center gap-1 truncate text-small text-muted-foreground">
            {cat && cat !== title && <span className="truncate">{cat}</span>}
            {cat && cat !== title && <span aria-hidden>·</span>}
            {acc ? <span className="truncate">{acc}</span> : (
              <span
                role={onChooseAccount ? 'button' : undefined}
                onClick={onChooseAccount ? (e) => { e.stopPropagation(); onChooseAccount() } : undefined}
                className="inline-flex items-center gap-1 rounded-full bg-warning-soft px-1.5 text-caption text-warning"
              >
                <Inbox className="size-3" /> Выбрать счёт
              </span>
            )}
            <span aria-hidden>·</span>
            <span className="num shrink-0">{time}</span>
          </span>
        )}
      </span>
      <span className="flex shrink-0 flex-col items-end">
        <span
          className={cn(
            'num text-body font-medium whitespace-nowrap',
            amount.tone === 'income' && 'text-income',
            amount.tone === 'transfer' && 'text-transfer',
          )}
          translate="no"
        >
          {amount.text}
        </span>
        {dense ? (
          <span className="num text-caption text-muted-foreground">{time}</span>
        ) : (
          approx && <span className="num text-small text-muted-foreground" translate="no">{approx}</span>
        )}
      </span>
    </Tag>
  )
}

export function TxRowSkeleton({ dense }: { dense?: boolean }) {
  return (
    <div className={cn('flex items-center gap-3 px-3', dense ? 'h-10' : 'h-14 md:h-11')}>
      <span className="pf-pulse size-7 shrink-0 rounded-sm bg-muted" />
      <span className="flex flex-1 flex-col gap-1.5">
        <span className="pf-pulse h-3 w-2/5 rounded-xs bg-muted" />
        {!dense && <span className="pf-pulse h-2.5 w-1/4 rounded-xs bg-muted" />}
      </span>
      <span className="pf-pulse h-3 w-18 rounded-xs bg-muted" />
    </div>
  )
}
