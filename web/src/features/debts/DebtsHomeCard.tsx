// Home block «Долги» (DEBTS.md §10): only when something is open — «Мне должны ≈ X ₫» /
// «Я должен ≈ Y ₫» (totals in the summary currency, ≈ $ small), up to 3 people per side, «Все →».
// Debts are not in the income/expense KPIs (the server keeps them out). A server without debts (404/501)
// or a failed load simply shows no block — Home never breaks because of it.
import { Link, useNavigate } from 'react-router'
import { HandCoins } from 'lucide-react'
import { Section } from '@/components/pf/Page'
import { useDebts, useSettings } from '@/lib/api/hooks'
import type { DebtDirection, DebtsOut } from '@/lib/api/types'
import { localDateISO } from '@/lib/format'
import { cn } from '@/lib/utils'
import { hasOpenDebts, peopleCount, sideBalances, sideTotal } from './lib'
import { PersonRow } from './PersonRow'

const SIDE_LABEL: Record<DebtDirection, string> = { owed_to_me: 'Мне должны', i_owe: 'Я должен' }
const MAX_PEOPLE = 3

function Side({ data, side, summary, today, onOpen }: { data: DebtsOut; side: DebtDirection; summary: 'VND' | 'USD'; today: string; onOpen: (id: number) => void }) {
  const people = data[side]
  const t = sideTotal(data.totals, side, summary)
  const more = people.length - MAX_PEOPLE
  return (
    <div className="min-w-0" data-testid={`home-debts-${side}`}>
      <div className="flex items-baseline gap-2 px-2">
        <span className="text-small text-muted-foreground">{SIDE_LABEL[side]}</span>
        <span className="flex-1" />
        <span className={cn('num text-title-3', side === 'owed_to_me' ? 'text-income' : 'text-foreground')} translate="no">
          {t.main ?? '—'}
        </span>
      </div>
      {t.approx && <p className="num px-2 text-right text-caption text-muted-foreground" translate="no">{t.approx}</p>}
      <ul className="mt-1 flex flex-col">
        {people.slice(0, MAX_PEOPLE).map((d) => (
          <li key={d.id}>
            <PersonRow debt={d} balances={sideBalances(d, side)} today={today} dense onOpen={() => onOpen(d.id)} />
          </li>
        ))}
      </ul>
      {more > 0 && (
        <Link to="/accounts?debt=all" className="block px-2 pt-0.5 text-caption text-muted-foreground hover:text-foreground">
          и ещё {peopleCount(more)}
        </Link>
      )}
    </div>
  )
}

export function DebtsHomeCard({ className }: { className?: string }) {
  const nav = useNavigate()
  const settings = useSettings()
  const q = useDebts(false, { retry: false })
  const data = q.data
  if (!hasOpenDebts(data)) return null
  const summary = settings.data?.summary_currency === 'USD' ? 'USD' : 'VND'
  const today = localDateISO(new Date(), settings.data?.timezone)
  const open = (id: number) => nav(`/accounts?debt=${id}`)
  const sides = (['owed_to_me', 'i_owe'] as const).filter((s) => data![s].length > 0)
  return (
    <Section
      title={<span className="flex items-center gap-2"><HandCoins className="size-4 text-muted-foreground" aria-hidden /> Долги</span>}
      className={className}
      refreshing={q.isFetching && !q.isPending}
      bodyClassName="px-2 md:px-3"
      actions={<Link to="/accounts?debt=all" className="text-small text-muted-foreground hover:text-foreground">Все →</Link>}
    >
      <div data-testid="home-debts" className="flex flex-col gap-3">
        {sides.map((s, i) => (
          <div key={s} className={cn(i > 0 && 'border-t pt-3')}>
            <Side data={data!} side={s} summary={summary} today={today} onOpen={open} />
          </div>
        ))}
        {data!.totals.missing_rates && (
          <p className="px-2 text-caption text-muted-foreground">Часть валют без курса — итог ≈ неполный</p>
        )}
      </div>
    </Section>
  )
}
