// Account side panel: balances per asset (opening balance / reconcile), aliases, default currencies,
// card fee, basics, recent operations of the account, archive/delete.
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { Archive, ArchiveRestore, Scale } from 'lucide-react'
import { api, type AccountOut } from '@/lib/api'
import { errorMessage, invalidateEntities, useToday, useTransactionList } from '@/features/operations/api'
import { formatDateShort } from '@/features/operations/lib/dates'
import { formatMoney } from '@/features/operations/lib/format'
import { txAmountView, txTitle } from '@/features/operations/lib/tx'
import { TxIcon } from '@/features/operations/ui/icons'
import { Button } from '@/features/operations/ui/kit'
import { Dialog, notify, Sheet } from '@/features/operations/ui/overlays'
import { approxOf } from './AccountCard'
import { AccountBasics, AliasEditor, CardFee, DefaultCurrencies, usePatchAccount } from './editors'
import { AdjustForm, OpeningForm } from './forms'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-2 border-b border-border py-4 first:pt-0 last:border-0">
      <h3 className="text-[13px] font-semibold text-muted-foreground">{title}</h3>
      {children}
    </section>
  )
}

export function AccountDetails({ account, accounts, open, onOpenChange, initialAsset }: { account: AccountOut | null; accounts: AccountOut[]; open: boolean; onOpenChange: (v: boolean) => void; initialAsset?: string | null }) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title={account?.name ?? 'Счёт'} width={460}>
      {account && <DetailsBody key={account.id} account={account} accounts={accounts} initialAsset={initialAsset ?? null} onClose={() => onOpenChange(false)} />}
    </Sheet>
  )
}

function DetailsBody({ account, accounts, initialAsset, onClose }: { account: AccountOut; accounts: AccountOut[]; initialAsset: string | null; onClose: () => void }) {
  const qc = useQueryClient()
  const { today } = useToday()
  const [editAsset, setEditAsset] = useState<string | null>(initialAsset)
  const [adjustAsset, setAdjustAsset] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const patch = usePatchAccount()
  const recent = useTransactionList({ account_id: [account.id], limit: 10, sort: 'date_desc' })
  const del = useMutation({
    mutationFn: () => api.delete(`/accounts/${account.id}`),
    onSuccess: () => {
      invalidateEntities(qc, ['accounts', 'transactions'])
      notify.info(`Счёт «${account.name}» удалён (если были операции — перенесён в архив)`)
      onClose()
    },
    onError: (e) => notify.error(errorMessage(e, 'Не удалось удалить счёт')),
  })
  const assets = account.assets.length ? account.assets : account.currency ? [{ asset: account.currency, opening_balance: null, opening_at: null, balance: null, approx_usd: null, approx_vnd: null }] : []
  return (
    <div>
      <Section title="Остатки">
        {assets.length === 0 && <p className="text-sm text-muted-foreground">Монет пока нет — появятся при первом упоминании («3 тона с кошелька»).</p>}
        <ul className="grid gap-3">
          {assets.map((x) => (
            <li key={x.asset} className="grid gap-2 rounded-md bg-surface-2 p-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="num text-[15px] font-semibold">{x.balance === null ? <span className="font-normal text-muted-foreground">{x.asset}: остаток не задан</span> : formatMoney(x.balance, x.asset)}</p>
                  <p className="num text-xs text-muted-foreground">
                    {approxOf(x, account.is_multi_asset) ?? ''}
                    {x.opening_balance !== null && ` · начальный ${formatMoney(x.opening_balance, x.asset)}${x.opening_at ? ` на ${formatDateShort(x.opening_at.slice(0, 10), today, false)}` : ''}`}
                  </p>
                </div>
                <div className="flex shrink-0 gap-1">
                  <Button size="sm" variant="ghost" onClick={() => { setEditAsset(editAsset === x.asset ? null : x.asset); setAdjustAsset(null) }}>
                    {x.opening_balance === null ? 'Задать остаток' : 'Начальный'}
                  </Button>
                  {x.opening_balance !== null && (
                    <Button size="sm" variant="ghost" onClick={() => { setAdjustAsset(adjustAsset === x.asset ? null : x.asset); setEditAsset(null) }}>
                      <Scale /> Сверка
                    </Button>
                  )}
                </div>
              </div>
              {editAsset === x.asset && <OpeningForm account={account} asset={x.asset} onDone={() => setEditAsset(null)} />}
              {adjustAsset === x.asset && <AdjustForm accounts={accounts} defaultAccount={account.id} defaultAsset={x.asset} onDone={() => setAdjustAsset(null)} />}
            </li>
          ))}
        </ul>
        <p className="text-xs text-muted-foreground">Колебание курса — не доход и не расход: монеты хранятся в монетах, ≈ $ — только для отображения.</p>
      </Section>
      <Section title="Псевдонимы">
        <AliasEditor account={account} />
      </Section>
      <Section title="По умолчанию для валют">
        <DefaultCurrencies account={account} accounts={accounts} />
      </Section>
      {(account.kind === 'card' || account.kind === 'bank') && (
        <Section title="Комиссия карты">
          <CardFee account={account} />
        </Section>
      )}
      <Section title="Последние операции">
        {recent.isLoading ? (
          <p className="text-sm text-muted-foreground">Загружаю…</p>
        ) : recent.data?.items.length ? (
          <ul className="grid gap-1">
            {recent.data.items.map((t) => {
              const v = txAmountView(t)
              return (
                <li key={t.id}>
                  <Link to={`/operations?tx=${t.id}`} className="grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-2 rounded-sm px-1 py-1 hover:bg-surface-2">
                    <TxIcon tx={t} />
                    <span className="min-w-0">
                      <span className="block truncate text-[13px] font-medium">{txTitle(t).title}</span>
                      <span className="block text-xs text-muted-foreground">{formatDateShort(t.occurred_on, today)}</span>
                    </span>
                    <span className={`num text-[13px] font-medium ${v.tone === 'income' ? 'text-income' : v.tone === 'transfer' ? 'text-transfer' : ''}`}>{v.main}</span>
                  </Link>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">Операций по счёту пока нет</p>
        )}
        <Link to={`/operations?acc=${account.id}&period=all_time`} className="text-[13px] font-medium text-primary hover:underline">
          Все операции счёта →
        </Link>
      </Section>
      <Section title="Название и тип">
        <AccountBasics account={account} />
      </Section>
      <Section title="Архив">
        <div className="flex flex-wrap gap-2">
          {account.is_archived ? (
            <Button size="sm" onClick={() => patch.mutate({ id: account.id, patch: { version: account.version, is_archived: false } })} loading={patch.isPending}>
              <ArchiveRestore /> Вернуть из архива
            </Button>
          ) : (
            <Button size="sm" onClick={() => patch.mutate({ id: account.id, patch: { version: account.version, is_archived: true } })} loading={patch.isPending}>
              <Archive /> В архив
            </Button>
          )}
          <Button size="sm" variant="ghost" className="text-destructive" onClick={() => setConfirmDelete(true)}>
            Удалить счёт
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">Счёт с операциями не удаляется, а уходит в архив — история сохраняется.</p>
      </Section>
      <Dialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Удалить «${account.name}»?`}
        description="Если по счёту есть операции, он будет перенесён в архив."
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Отмена
            </Button>
            <Button variant="destructive" loading={del.isPending} onClick={() => del.mutate()}>
              Удалить
            </Button>
          </>
        }
      />
    </div>
  )
}
