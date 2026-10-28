// Account card (DESIGN §10.6): balance only when the opening balance is known («остаток не задан»
// otherwise, never 0); multi-asset wallets list coins with «≈ $» (display only, no revaluation).
import type { AccountOut, AssetBalanceOut } from '@/lib/api'
import { cn } from '@/lib/utils'
import { decAdd, decSign } from '@/features/operations/lib/decimal'
import { formatMoney, plural } from '@/features/operations/lib/format'
import { isCrypto } from '@/features/operations/lib/tx'
import { accountIcon, IconTile } from '@/features/operations/ui/icons'
import { Badge, Button } from '@/features/operations/ui/kit'

const SIGN: Record<string, string> = { VND: '₫', RUB: '₽', USD: '$' }

export function approxOf(a: AssetBalanceOut, preferUsd: boolean): string | null {
  if (a.balance === null) return null
  if (a.asset === 'USD') return a.approx_vnd ? formatMoney(a.approx_vnd, 'VND', { approx: true }) : null
  if (preferUsd || isCrypto(a.asset)) return a.approx_usd ? formatMoney(a.approx_usd, 'USD', { approx: true }) : null
  return a.approx_vnd && a.asset !== 'VND' ? formatMoney(a.approx_vnd, 'VND', { approx: true }) : a.approx_usd ? formatMoney(a.approx_usd, 'USD', { approx: true }) : null
}

/** Sum of known ≈ $ of a wallet; null when nothing is known. */
export function walletApproxUsd(a: AccountOut): string | null {
  const known = a.assets.filter((x) => x.balance !== null && x.approx_usd !== null)
  return known.length ? decAdd(...known.map((x) => x.approx_usd)) : null
}

export function AccountCard({ account, onOpen, onSetOpening }: { account: AccountOut; onOpen: () => void; onSetOpening: (asset: string) => void }) {
  const single = !account.is_multi_asset
  const main = single ? (account.assets.find((x) => x.asset === account.currency) ?? account.assets[0]) : null
  const defaults = account.default_for_currencies.map((c) => SIGN[c] ?? c)
  const total = account.is_multi_asset ? walletApproxUsd(account) : null
  const badges = [
    defaults.length > 0 && <Badge key="def" className="max-w-full min-w-0"><span className="truncate">по умолчанию для {defaults.join(', ')}</span></Badge>,
    account.is_archived && <Badge key="arch" tone="warning">в архиве</Badge>,
  ].filter(Boolean)
  return (
    <article className={cn('relative min-w-0 rounded-lg border border-border bg-card transition-colors hover:bg-surface-2/60', account.is_archived && 'opacity-70')}>
      <button type="button" onClick={onOpen} className="absolute inset-0 rounded-lg focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none" aria-label={`Открыть счёт ${account.name}`} />
      {/* grid-cols-1 = minmax(0,1fr): long alias lists truncate instead of widening the card (390 px). */}
      <div className="pointer-events-none relative grid grid-cols-1 gap-3 p-4 md:p-5">
        {/* Badges: own line under the name on phones (basis-full), top-right on wider cards (≤ 50%). */}
        <header className="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-1.5">
          <IconTile icon={accountIcon(account.icon, account.kind)} size={32} />
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-[15px] leading-5 font-semibold">{account.name}</h3>
            {account.aliases.length > 0 && <p className="truncate text-xs font-medium text-muted-foreground" title={account.aliases.join(', ')}>{account.aliases.map((a) => `«${a}»`).join(', ')}</p>}
          </div>
          {badges.length > 0 && <div className="flex min-w-0 basis-full flex-wrap gap-1 pl-11 sm:max-w-[50%] sm:basis-auto sm:justify-end sm:pl-0">{badges}</div>}
        </header>
        {single && main ? (
          main.balance === null ? (
            <div className="grid gap-2">
              <p className="text-[15px] text-muted-foreground">Остаток не задан</p>
              <p className="text-xs text-muted-foreground">Укажите, сколько сейчас на счёте, — дальше посчитаем сами.</p>
            </div>
          ) : (
            <div>
              <p className="num text-[22px] leading-7 font-semibold tracking-[-0.015em]" translate="no">
                {formatMoney(main.balance, main.asset)}
              </p>
              <p className="num text-[13px] text-muted-foreground">{approxOf(main, false) ?? '\u00a0'}</p>
            </div>
          )
        ) : (
          <div className="grid gap-1.5">
            <p className="num text-[22px] leading-7 font-semibold tracking-[-0.015em]" translate="no">
              {total !== null ? formatMoney(total, 'USD', { approx: true }) : <span className="text-[15px] font-normal text-muted-foreground">Остаток не задан</span>}
            </p>
            {account.assets.length > 0 ? (
              <ul className="grid gap-0.5 text-[13px]">
                {account.assets.map((x) => (
                  <li key={x.asset} className="flex items-center justify-between gap-2">
                    <span className="num font-medium">{x.balance === null ? x.asset : formatMoney(x.balance, x.asset)}</span>
                    <span className="num text-muted-foreground">{x.balance === null ? 'остаток не задан' : (approxOf(x, true) ?? '')}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">Монет пока нет</p>
            )}
            <p className="text-xs text-muted-foreground">+ монета добавится при первом упоминании</p>
          </div>
        )}
        {single && main && main.balance === null && (
          <div className="pointer-events-auto relative">
            <Button size="sm" variant="soft" onClick={() => onSetOpening(main.asset)}>
              Задать остаток
            </Button>
          </div>
        )}
      </div>
    </article>
  )
}

/** Accounts left out of the known total: no asset with a known balance. Same rule as Home's balances card
 *  (backend `unknown_count` counts asset rows, so a wallet with no coins yet was missing there). */
export function unknownAccountsCount(accounts: AccountOut[]): number {
  return accounts.filter((a) => !a.is_archived && !a.assets.some((x) => x.balance !== null)).length
}

export function totalsText(b: { total_usd: string | null; total_vnd: string | null; accounts: AccountOut[] }): { main: string; note: string | null } {
  const parts: string[] = []
  if (b.total_usd !== null && decSign(b.total_usd) !== 0) parts.push(formatMoney(b.total_usd, 'USD', { approx: true }))
  if (b.total_vnd !== null && decSign(b.total_vnd) !== 0) parts.push(formatMoney(b.total_vnd, 'VND', { approx: true }))
  const main = parts.length ? parts.join(' · ') : '—'
  const n = unknownAccountsCount(b.accounts)
  const note = n > 0 ? `${n} ${plural(n, ['счёт', 'счёта', 'счетов'])} без остатка не ${plural(n, ['учтён', 'учтены', 'учтены'])}` : null
  return { main, note }
}
