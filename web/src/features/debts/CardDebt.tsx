// Debt parts of assistant cards (DEBTS.md §8, §10) — additive: a card without `debt` /
// `debts` renders exactly as before. Used by the AI line on Home and by Quick Capture (compact).
import { HandCoins } from 'lucide-react'
import type { DebtBrief, DebtDirection, DebtsOut } from '@/lib/api/types'
import { localDateISO } from '@/lib/format'
import { cn } from '@/lib/utils'
import { debtBriefLine, excessText, sideBalances, subtitleHasDebtLine, titleLinksDebt } from './lib'
import { PersonRow } from './PersonRow'

const nameLink = 'font-medium text-foreground underline decoration-muted-foreground/40 underline-offset-2 hover:decoration-foreground'

/** Card title «500 000 ₫ · Дал в долг: Коля» with the name as a link to the card of the person (the
 *  server puts the name after the colon, nominative). Any other title is returned as is. */
export function DebtTitle({ title, brief, onOpenDebt }: { title: string; brief: DebtBrief | null; onOpenDebt?: (id: number) => void }) {
  if (!brief || !onOpenDebt || !titleLinksDebt(title, brief)) return <>{title}</>
  return (
    <>
      {title.slice(0, title.length - brief.name.length)}
      <button type="button" onClick={() => onOpenDebt(brief.debt_id)} className={nameLink} data-testid="card-debt-name">{brief.name}</button>
    </>
  )
}

/** «Коля: долг тебе 300 000 ₫» (+ the overpayment highlighted). The name opens the card of the person unless
 *  the title already links it. When the server subtitle already says the balance («Карта · Коля: долг
 *  тебе 400 000 ₫», «осталось …», «долг закрыт»), the line is not repeated — only the excess stays. */
export function DebtCardLine({ brief, compact, onOpenDebt, subtitle, nameLinked }: {
  brief: DebtBrief; compact?: boolean; onOpenDebt?: (id: number) => void; subtitle?: string | null; nameLinked?: boolean
}) {
  const line = debtBriefLine(brief)
  const excess = excessText(brief)
  const showLine = !subtitleHasDebtLine(subtitle, brief)
  if (!showLine && !excess) return null
  const nameAt = line.indexOf(brief.name)
  const name = onOpenDebt && !nameLinked && nameAt >= 0 ? (
    <button type="button" onClick={() => onOpenDebt(brief.debt_id)} className={nameLink}>
      {brief.name}
    </button>
  ) : <span className="font-medium text-foreground">{brief.name}</span>
  return (
    <div className="mt-1 flex flex-col gap-1" data-testid="card-debt">
      {showLine && (
        <p className="flex min-w-0 items-center gap-1.5 text-small text-muted-foreground" data-testid="card-debt-line">
          <HandCoins className="size-3.5 shrink-0" aria-hidden />
          <span className="num min-w-0 truncate" translate="no">
            {nameAt >= 0 ? <>{line.slice(0, nameAt)}{name}{line.slice(nameAt + brief.name.length)}</> : line}
          </span>
        </p>
      )}
      {excess && (
        <p className={cn('num rounded-sm bg-warning-soft px-2 py-1 text-small text-warning', compact && 'px-1.5 py-0.5')} data-testid="card-debt-excess" translate="no">
          {excess}
        </p>
      )}
    </div>
  )
}

const SIDE_TITLE: Record<DebtDirection, string> = { owed_to_me: 'Мне должны', i_owe: 'Я должен' }

/** «кто мне должен?» answer: the two lists (people → balances per currency). */
export function DebtsAnswer({ debts, compact, tz, onOpenDebt }: { debts: DebtsOut; compact?: boolean; tz?: string; onOpenDebt?: (id: number) => void }) {
  const today = localDateISO(new Date(), tz)
  const max = compact ? 3 : 5
  const sides = (['owed_to_me', 'i_owe'] as const).filter((s) => debts[s].length > 0)
  if (!sides.length) return <p className="mt-1 text-small text-muted-foreground">Долгов нет</p>
  return (
    <div className="mt-1.5 flex flex-col gap-2" data-testid="card-debts">
      {sides.map((s) => (
        <div key={s}>
          <p className="text-caption text-muted-foreground">{SIDE_TITLE[s]}</p>
          <ul className="-mx-2 flex flex-col">
            {debts[s].slice(0, max).map((d) => (
              <li key={d.id}>
                <PersonRow debt={d} balances={sideBalances(d, s)} today={today} dense onOpen={onOpenDebt ? () => onOpenDebt(d.id) : undefined} />
              </li>
            ))}
          </ul>
          {debts[s].length > max && <p className="text-caption text-muted-foreground">и ещё {debts[s].length - max}</p>}
        </div>
      ))}
    </div>
  )
}
