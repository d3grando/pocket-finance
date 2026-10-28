// «Счета» → «Долги» (DEBTS.md §10): a separate block under the accounts (not in the
// known total): «Мне должны» / «Я должен» with balances per currency (≈ $), the age of each debt,
// «Дать в долг» / «Занять». `?debt=all` scrolls here, `?debt=<id>` opens the card of the person.
import { useEffect, useRef, useState } from 'react'
import { keepPreviousData } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { HandCoins, Plus } from 'lucide-react'
import type { DebtDirection, DebtsOut } from '@/lib/api/types'
import { isOfflineEmpty, loadErrorMessage, useSettings, useToday } from '@/features/operations/api'
import { Button, Card, EmptyState, ErrorNote, OfflineState, Skeleton, Switch } from '@/features/operations/ui/kit'
import { Sheet } from '@/features/operations/ui/overlays'
import { useIsMobile } from '@/features/operations/ui/hooks'
import { debtsUnsupported, useDebts } from './api'
import { DebtDetails } from './DebtDetails'
import { DebtOpForm } from './DebtOpForm'
import { hasOpenDebts, sideBalances, sideTotal } from './lib'
import { PersonRow } from './PersonRow'

const SIDE: Record<DebtDirection, { title: string; empty: string }> = {
  owed_to_me: { title: 'Мне должны', empty: 'Никто не должен' },
  i_owe: { title: 'Я должен', empty: 'Ты никому не должен' },
}

function SideList({ data, side, today, summary, onOpen }: { data: DebtsOut; side: DebtDirection; today: string; summary: 'VND' | 'USD'; onOpen: (id: number) => void }) {
  const people = data[side]
  const t = sideTotal(data.totals, side, summary)
  return (
    <div className="min-w-0" data-testid={`debts-${side}`}>
      <div className="flex flex-wrap items-baseline gap-x-2 px-2 pb-1">
        <h3 className="text-[13px] font-semibold text-muted-foreground">{SIDE[side].title}</h3>
        <span className="flex-1" />
        {people.length > 0 && (
          <span className="num text-[15px] font-semibold" translate="no">
            {t.main ?? '—'}
            {t.approx && <span className="ml-1.5 text-xs font-normal text-muted-foreground">{t.approx}</span>}
          </span>
        )}
      </div>
      {people.length === 0 ? (
        <p className="px-2 py-2 text-sm text-muted-foreground">{SIDE[side].empty}</p>
      ) : (
        <ul className="grid grid-cols-1 gap-0.5">
          {people.map((d) => (
            <li key={d.id}><PersonRow debt={d} balances={sideBalances(d, side)} today={today} onOpen={() => onOpen(d.id)} /></li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** `ready`: the blocks above have their final height (accounts loaded) — `?debt=all` scrolls only then. */
export function DebtsSection({ ready = true }: { ready?: boolean }) {
  const mobile = useIsMobile()
  const [sp, setSp] = useSearchParams()
  const [showSettled, setShowSettled] = useState(false)
  const [form, setForm] = useState<'lend' | 'borrow' | null>(null)
  const { today } = useToday()
  const settings = useSettings()
  const summary = settings.data?.summary_currency === 'USD' ? 'USD' : 'VND'
  const q = useDebts(showSettled, { retry: false, placeholderData: keepPreviousData })
  const ref = useRef<HTMLElement>(null)
  const param = sp.get('debt')
  const openId = param && /^\d+$/.test(param) ? Number(param) : null

  const scrolled = useRef(false)
  useEffect(() => {
    if (param !== 'all') { scrolled.current = false; return }
    if (!ready || !q.isSuccess || scrolled.current) return
    // marked inside the frame: a cancelled frame (StrictMode re-run) scrolls on the next run
    const id = requestAnimationFrame(() => {
      scrolled.current = true
      ref.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' })
    })
    return () => cancelAnimationFrame(id)
  }, [param, ready, q.isSuccess])

  if (debtsUnsupported(q.error)) return null

  const setOpen = (id: number | null) =>
    setSp((prev) => {
      const n = new URLSearchParams(prev)
      if (id) n.set('debt', String(id))
      else n.delete('debt')
      return n
    })
  const data = q.data
  const settled = data?.settled ?? []
  const btn = (k: 'lend' | 'borrow', label: string, icon: React.ReactNode) => (
    <Button size={mobile ? 'sm' : 'md'} variant={k === 'lend' ? 'outline' : 'ghost'} onClick={() => setForm(k)}>
      {icon}
      {label}
    </Button>
  )

  return (
    <Card
      ref={ref}
      id="debts"
      aria-label="Долги"
      data-testid="debts-section"
      className="scroll-mt-20"
      title={<span className="flex items-center gap-2"><HandCoins className="size-4 text-muted-foreground" /> Долги</span>}
      busy={q.isFetching && !q.isLoading}
      actions={<>{btn('lend', 'Дать в долг', <HandCoins />)}{btn('borrow', 'Занять', <Plus />)}</>}
    >
      {isOfflineEmpty(q) ? (
        <OfflineState what="Долги" onRetry={() => void q.refetch()} className="py-6" />
      ) : q.error ? (
        <ErrorNote error={new Error(loadErrorMessage(q.error, 'Не удалось загрузить долги'))} onRetry={() => void q.refetch()} />
      ) : !data ? (
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
      ) : !hasOpenDebts(data) && !settled.length ? (
        <EmptyState
          icon={<HandCoins />}
          title="Долгов нет"
          text="Напишите «дал в долг Коле 500к» или «занял у Пети 1м» — остаток посчитаю сам. Долги не входят в доходы и расходы."
          className="py-6"
        />
      ) : (
        // explicit minmax(0,1fr) columns: an implicit `auto` track grows to the nowrap min-content of a long
        // name / several currencies and pushed the page wider than a 390 px phone (truncate never kicked in)
        <div className="grid min-w-0 grid-cols-1 gap-4">
          {hasOpenDebts(data) && (
            <div className="grid min-w-0 grid-cols-1 gap-4 md:grid-cols-2">
              <SideList data={data} side="owed_to_me" today={today} summary={summary} onOpen={setOpen} />
              <SideList data={data} side="i_owe" today={today} summary={summary} onOpen={setOpen} />
            </div>
          )}
          {showSettled && settled.length > 0 && (
            <div className="min-w-0" data-testid="debts-settled">
              <h3 className="px-2 pb-1 text-[13px] font-semibold text-muted-foreground">Закрытые</h3>
              <ul className="grid grid-cols-1 gap-0.5">
                {settled.map((d) => (
                  <li key={d.id}>
                    <button type="button" onClick={() => setOpen(d.id)} className="flex min-h-10 w-full items-center gap-3 rounded-sm px-2 text-left text-sm hover:bg-surface-2">
                      <span className="flex-1 truncate">{d.name}</span>
                      <span className="text-xs text-muted-foreground">{d.is_archived ? 'в архиве' : 'закрыт'}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      {data && (
        <label className="mt-3 flex items-center gap-2 px-2 text-sm text-muted-foreground">
          <Switch checked={showSettled} onCheckedChange={setShowSettled} label="Показать закрытые" /> Показать закрытые
        </label>
      )}
      <DebtDetails debtId={openId} open={openId !== null} onOpenChange={(v) => !v && setOpen(null)} />
      <Sheet open={form !== null} onOpenChange={(v) => !v && setForm(null)} title={form === 'borrow' ? 'Занять' : 'Дать в долг'}>
        {form && <DebtOpForm key={form} op={form} onDone={() => setForm(null)} />}
      </Sheet>
    </Card>
  )
}
