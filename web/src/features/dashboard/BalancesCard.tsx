// «Счета» on Home (DESIGN §10.1): only accounts with a known balance show numbers;
// unknown ones are never shown as 0 — they are counted in the footer with a link to set them.
import { Link } from 'react-router'
import { Section } from '@/components/pf/Page'
import { IconTile, accountIcon } from '@/components/pf/icons'
import { useBalances } from '@/lib/api/hooks'
import type { AccountOut } from '@/lib/api/types'
import { Dec } from '@/lib/decimal'
import { formatMoney, plural } from '@/lib/format'

export interface BalanceLine { account: AccountOut; text: string; approx: string | null }

/** Lines for accounts with at least one known asset balance. */
export function knownBalanceLines(accounts: AccountOut[]): { lines: BalanceLine[]; unknown: number } {
  const lines: BalanceLine[] = []
  let unknown = 0
  for (const a of accounts) {
    if (a.is_archived) continue
    const known = a.assets.filter((x) => x.balance != null)
    if (!known.length) { unknown++; continue }
    const text = known.map((x) => formatMoney(x.balance!, x.asset)).join(' · ')
    let approx: string | null = null
    const single = known.length === 1 ? known[0] : null
    if (single && single.asset !== 'VND' && single.approx_vnd != null && !a.is_multi_asset && single.asset !== 'USD') {
      approx = formatMoney(single.approx_vnd, 'VND', { approx: true })
    } else if (a.is_multi_asset || (single && single.asset !== 'VND' && single.asset !== 'USD')) {
      const usd = known.every((x) => x.approx_usd != null) ? known.reduce((s, x) => s.add(Dec.from(x.approx_usd!)), Dec.ZERO) : null
      approx = usd ? formatMoney(usd.toString(), 'USD', { approx: true }) : null
    }
    lines.push({ account: a, text, approx })
  }
  return { lines, unknown }
}

export function BalancesCard({ className }: { className?: string }) {
  const q = useBalances()
  const { lines, unknown } = knownBalanceLines(q.data?.accounts ?? [])
  return (
    <Section
      title="Счета"
      className={className}
      refreshing={q.isFetching && !q.isPending}
      bodyClassName="px-2 md:px-3"
      actions={<Link to="/accounts" className="text-small text-muted-foreground hover:text-foreground">Все →</Link>}
    >
      <div data-testid="balances">
        {q.isPending ? (
          [0, 1, 2].map((i) => <div key={i} className="flex h-11 items-center gap-3 px-2"><span className="pf-pulse size-7 rounded-sm bg-muted" /><span className="pf-pulse h-3 flex-1 rounded-xs bg-muted" /></div>)
        ) : !lines.length ? (
          <div className="px-2 py-4 text-center">
            <p className="text-title-3">Остаток не задан</p>
            <p className="mt-1 text-small text-muted-foreground">Укажите, сколько сейчас на счетах, — дальше посчитаем сами.</p>
            <Link to="/accounts" className="mt-2 inline-block text-small font-medium text-primary hover:underline">Задать остаток</Link>
          </div>
        ) : (
          <ul className="flex flex-col">
            {lines.map(({ account, text, approx }) => (
              <li key={account.id} className="flex min-h-11 items-center gap-3 rounded-sm px-2 py-1.5">
                <IconTile icon={accountIcon(account)} />
                <span className="min-w-0 flex-1 truncate text-body">{account.name}</span>
                <span className="flex min-w-0 flex-col items-end">
                  <span className="num truncate text-body font-medium" translate="no">{text}</span>
                  {approx && <span className="num text-small text-muted-foreground">{approx}</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
        {q.data && lines.length > 0 && (
          <div className="mt-1 flex items-center gap-2 border-t px-2 pt-2 text-small">
            <span className="flex-1 text-muted-foreground">Всего известное</span>
            <span className="num font-medium">{q.data.total_usd != null ? formatMoney(q.data.total_usd, 'USD', { approx: true }) : '—'}</span>
          </div>
        )}
        {unknown > 0 && lines.length > 0 && (
          <p className="px-2 pt-1 text-caption text-muted-foreground">
            {unknown} {plural(unknown, ['счёт', 'счёта', 'счетов'])} без остатка {plural(unknown, ['не учтён', 'не учтены', 'не учтены'])} · <Link to="/accounts" className="text-primary hover:underline">Задать</Link>
          </p>
        )}
      </div>
    </Section>
  )
}
