// One person in a debts list: initial, name, balances per currency (never converted),
// ≈ $ and the age of the debt («30 дн.»). Shared by Home, «Счета» and the assistant answer card.
import type { DebtBalanceOut, DebtOut } from '@/lib/api/types'
import { cn } from '@/lib/utils'
import { ageLabel, balancesApproxUsd, balancesText, debtAgeDays } from './lib'

export function PersonInitial({ name, size = 28, className }: { name: string; size?: number; className?: string }) {
  const ch = (name.replace(/^@/, '').trim()[0] ?? '?').toUpperCase()
  return (
    <span
      className={cn('grid shrink-0 place-items-center rounded-full bg-muted text-[13px] font-semibold text-muted-foreground', className)}
      style={{ width: size, height: size }}
      aria-hidden
    >
      {ch}
    </span>
  )
}

export function PersonRow({ debt, balances, today, onOpen, dense, className }: {
  debt: DebtOut; balances: DebtBalanceOut[]; today: string; onOpen?: () => void; dense?: boolean; className?: string
}) {
  const approx = balancesApproxUsd(balances)
  const age = ageLabel(debtAgeDays(balances, today))
  const Tag = onOpen ? 'button' : 'div'
  return (
    <Tag
      type={onOpen ? 'button' : undefined}
      onClick={onOpen}
      data-testid="debt-person"
      data-debt-id={debt.id}
      aria-label={onOpen ? `Открыть долг: ${debt.name}` : undefined}
      className={cn(
        'flex w-full min-w-0 items-center gap-3 rounded-sm px-2 text-left',
        dense ? 'min-h-10 py-1' : 'min-h-12 py-1.5',
        onOpen && 'transition-colors hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none',
        className,
      )}
    >
      <PersonInitial name={debt.name} size={dense ? 24 : 28} />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm font-medium">{debt.name}</span>
        {!dense && (age || debt.handle) && (
          <span className="truncate text-xs text-muted-foreground">
            {[debt.handle ? `@${debt.handle.replace(/^@/, '')}` : null, age].filter(Boolean).join(' · ')}
          </span>
        )}
      </span>
      <span className="flex min-w-0 max-w-[60%] flex-col items-end">
        <span className="num truncate text-sm font-medium" translate="no">{balancesText(balances)}</span>
        {(approx || (dense && age)) && (
          <span className="num truncate text-xs text-muted-foreground" translate="no">{[approx, dense ? age : null].filter(Boolean).join(' · ')}</span>
        )}
      </span>
    </Tag>
  )
}
