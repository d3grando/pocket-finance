// «Счета» (DESIGN §10.6): known total, «Разобрать» queue (operations without account), account cards,
// transfer / exchange / reconcile / new account forms, account panel.
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ArrowLeftRight, Inbox, Plus, Repeat, Scale, WalletCards } from 'lucide-react'
import type { AccountOut, TransactionOut } from '@/lib/api'
import { errorMessage, isOfflineEmpty, loadErrorMessage, useAccounts, useAssignAccount, useBalances, useToday, useTransactionList } from '@/features/operations/api'
import { formatDateShort } from '@/features/operations/lib/dates'
import { txAmountView, txTitle } from '@/features/operations/lib/tx'
import { TxIcon } from '@/features/operations/ui/icons'
import { Button, Card, EmptyState, ErrorNote, Loading, OfflineState, Page, PageHeader, Skeleton, Switch } from '@/features/operations/ui/kit'
import { notify, PageToaster, Sheet } from '@/features/operations/ui/overlays'
import { useIsMobile } from '@/features/operations/ui/hooks'
import { AccountCard, totalsText } from './AccountCard'
import { AccountDetails } from './AccountDetails'
import { AccountCreateForm, AdjustForm, ExchangeForm, TransferForm } from './forms'
import { DebtsSection } from '@/features/debts/DebtsSection'
import { accountQuestion, debtTxTitle } from '@/features/debts/lib'

type FormKind = 'transfer' | 'exchange' | 'adjust' | 'create' | null
const FORM_TITLES: Record<Exclude<FormKind, null>, string> = { transfer: 'Перевод между счетами', exchange: 'Обмен валют', adjust: 'Сверка остатка', create: 'Новый счёт' }

/** Account buttons for «С какого?»: multi-asset wallets and same-currency accounts first, then all the
 *  others ($ from Карта is debited at the VCB rate), as the assistant card and the row popover do. */
export function inboxOptions(tx: TransactionOut, accounts: AccountOut[]): AccountOut[] {
  const live = accounts.filter((a) => !a.is_archived)
  const fit = live.filter((a) => a.is_multi_asset || a.currency === tx.currency)
  return [...fit, ...live.filter((a) => !fit.includes(a))]
}

function InboxRow({ tx, accounts, today }: { tx: TransactionOut; accounts: AccountOut[]; today: string }) {
  const assign = useAssignAccount()
  const v = txAmountView(tx)
  const choose = (id: number | null, label: string) =>
    assign.mutate({ id: tx.id, accountId: id, version: tx.version }, { onSuccess: () => notify.info(`${v.main} · ${label}`), onError: (e) => notify.error(errorMessage(e)) })
  return (
    <li className="grid gap-2 rounded-md border border-border p-3">
      <div className="grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-3">
        <TxIcon tx={tx} />
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{debtTxTitle(tx) ?? txTitle(tx).title}</p>
          <p className="truncate text-xs text-muted-foreground">
            {[tx.category?.name, formatDateShort(tx.occurred_on, today), tx.raw_text ? `«${tx.raw_text}»` : null].filter(Boolean).join(' · ')}
          </p>
        </div>
        <span className="num text-sm font-medium">{v.main}</span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={accountQuestion(tx)}>
        <span className="text-xs text-muted-foreground">{accountQuestion(tx) === 'На какой счёт?' ? 'На какой счёт?' : 'С какого?'}</span>
        {inboxOptions(tx, accounts).map((a) => (
          <Button key={a.id} size="sm" variant="outline" disabled={assign.isPending} onClick={() => choose(a.id, a.name)}>
            {a.name}
          </Button>
        ))}
        <Button size="sm" variant="ghost" disabled={assign.isPending} onClick={() => choose(null, 'без счёта')}>
          Без счёта
        </Button>
      </div>
    </li>
  )
}

/** Debt operations waiting for an account are not expenses (DEBTS.md §0 п.8) — «уже учтены в расходах»
 *  is said only about the others. */
function inboxHint(items: TransactionOut[]): string {
  const debts = items.filter((t) => t.debt).length
  if (!debts) return 'Уже учтены в расходах, но не в остатках — выберите счёт.'
  if (debts === items.length) return 'Ждут выбора счёта — в остатках пока не учтены.'
  return 'Ждут выбора счёта — в остатках пока не учтены. Расходы уже в статистике, долги в неё не входят.'
}

export function InboxCard({ accounts }: { accounts: AccountOut[] }) {
  const { today } = useToday()
  const q = useTransactionList({ status: 'needs_account', limit: 50, sort: 'date_desc' })
  const items = q.data?.items ?? []
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Inbox className="size-4 text-warning" /> Разобрать{q.data ? `: ${q.data.total}` : ''}
        </span>
      }
      busy={q.isFetching && !q.isLoading}
      actions={items.length ? <Link to="/operations?status=needs_account" className="text-[13px] font-medium text-primary hover:underline">В операциях →</Link> : undefined}
    >
      {isOfflineEmpty(q) ? (
        <OfflineState what="Операции без счёта" onRetry={() => void q.refetch()} className="py-6" />
      ) : q.error ? (
        <ErrorNote error={new Error(loadErrorMessage(q.error, 'Не удалось загрузить'))} onRetry={() => void q.refetch()} />
      ) : q.isLoading ? (
        <div className="h-20" />
      ) : items.length === 0 ? (
        <EmptyState icon={<Inbox />} title="Всё разобрано" text="Операции без счёта появятся здесь." className="py-6" />
      ) : (
        <>
          <p className="mb-2 text-[13px] text-muted-foreground" data-testid="inbox-hint">{inboxHint(items)}</p>
          <ul className="grid gap-2">
            {items.map((t) => (
              <InboxRow key={t.id} tx={t} accounts={accounts} today={today} />
            ))}
          </ul>
        </>
      )}
    </Card>
  )
}

export function AccountsView() {
  const mobile = useIsMobile()
  const [sp, setSp] = useSearchParams()
  const [showArchived, setShowArchived] = useState(false)
  const balances = useBalances()
  const all = useAccounts(true)
  const [form, setForm] = useState<FormKind>(null)
  const [openingAsset, setOpeningAsset] = useState<string | null>(null)
  const openId = Number(sp.get('account')) || null
  const accounts = useMemo(() => {
    const byId = new Map((balances.data?.accounts ?? []).map((a) => [a.id, a]))
    return (all.data ?? []).map((a) => byId.get(a.id) ?? a).sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
  }, [balances.data, all.data])
  const visible = accounts.filter((a) => showArchived || !a.is_archived)
  const archivedCount = accounts.filter((a) => a.is_archived).length
  const openAccount = accounts.find((a) => a.id === openId) ?? null
  const setOpen = (id: number | null, asset: string | null = null) => {
    setOpeningAsset(asset)
    setSp((prev) => {
      const n = new URLSearchParams(prev)
      if (id) n.set('account', String(id))
      else n.delete('account')
      return n
    })
  }
  const t = balances.data ? totalsText(balances.data) : null
  const btn = (k: Exclude<FormKind, null>, icon: React.ReactNode, label: string) => (
    <Button size={mobile ? 'icon' : 'md'} variant={k === 'create' ? 'primary' : 'outline'} onClick={() => setForm(k)} aria-label={label} title={label}>
      {icon}
      {!mobile && label}
    </Button>
  )

  return (
    <Page>
      <PageToaster />
      <PageHeader title="Счета" actions={<>{btn('transfer', <ArrowLeftRight />, 'Перевод')}{btn('exchange', <Repeat />, 'Обмен')}{btn('adjust', <Scale />, 'Сверка')}{btn('create', <Plus />, 'Счёт')}</>} />
      <p className="num mt-2 flex flex-wrap items-baseline gap-x-2 text-sm" aria-live="polite">
        <span className="text-muted-foreground">Известный итог</span>
        <span className="text-[15px] font-semibold">{t?.main ?? '—'}</span>
        {t?.note && <span className="text-muted-foreground">· {t.note}</span>}
      </p>

      {(balances.error || all.error) && !(isOfflineEmpty(balances) || isOfflineEmpty(all)) ? (
        <ErrorNote className="mt-4" error={new Error(loadErrorMessage(balances.error ?? all.error, 'Не удалось загрузить счета'))} onRetry={() => { void balances.refetch(); void all.refetch() }} />
      ) : null}

      <div className="mt-4 grid gap-4">
        <InboxCard accounts={accounts} />
        <Loading loading={all.isLoading || balances.isLoading} className="grid gap-3 md:grid-cols-2" fallback={<><Skeleton className="h-36" /><Skeleton className="h-36" /><Skeleton className="h-36" /><Skeleton className="h-36" /></>}>
          {visible.length === 0 && (isOfflineEmpty(all) || isOfflineEmpty(balances)) ? (
            <OfflineState what="Счета" onRetry={() => { void balances.refetch(); void all.refetch() }} className="md:col-span-2" />
          ) : visible.length === 0 ? (
            <EmptyState icon={<WalletCards />} title="Счетов нет" text="Добавьте счёт, чтобы видеть остатки." action={<Button variant="primary" onClick={() => setForm('create')}><Plus /> Счёт</Button>} />
          ) : (
            <div className="grid gap-3 md:grid-cols-2" aria-label="Счета">
              {visible.map((a) => (
                <AccountCard key={a.id} account={a} onOpen={() => setOpen(a.id)} onSetOpening={(asset) => setOpen(a.id, asset)} />
              ))}
            </div>
          )}
        </Loading>
        {archivedCount > 0 && (
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <Switch checked={showArchived} onCheckedChange={setShowArchived} label="Показать архив" /> Показать архив ({archivedCount})
          </label>
        )}
        {/* debts: a separate block, never part of the known total (DEBTS.md §2, §10) */}
        <DebtsSection ready={!all.isLoading && !balances.isLoading} />
      </div>

      <AccountDetails account={openAccount} accounts={accounts} open={!!openAccount} onOpenChange={(v) => !v && setOpen(null)} initialAsset={openingAsset} />
      <Sheet open={form !== null} onOpenChange={(v) => !v && setForm(null)} title={form ? FORM_TITLES[form] : ''}>
        {form === 'transfer' && <TransferForm accounts={accounts} onDone={() => setForm(null)} />}
        {form === 'exchange' && <ExchangeForm accounts={accounts} onDone={() => setForm(null)} />}
        {form === 'adjust' && <AdjustForm accounts={accounts} onDone={() => setForm(null)} />}
        {form === 'create' && <AccountCreateForm onDone={() => setForm(null)} />}
      </Sheet>
    </Page>
  )
}
