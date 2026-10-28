// <ResultCard> — every Card kind from API.md §9.2 / DESIGN §9.2 (a–i).
import { useEffect, useRef, useState } from 'react'
import {
  ChartPie, CircleAlert, CircleQuestionMark, Clock, CopyCheck, CornerDownLeft, Inbox, Pencil, Trash, Undo2, WifiOff, X,
} from 'lucide-react'
import { Kbd } from '@/components/pf/Kbd'
import { Button as UiButton } from '@/components/ui/button'
import { Spinner } from '@/components/pf/Spinner'
import { TxRow } from '@/components/pf/TxRow'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { Button, PreviewCard, TransactionOut } from '@/lib/api/types'
import { formatDayMonth, formatShortDate } from '@/lib/format'
import { txAmount, txTitle } from '@/lib/tx'
import { cn } from '@/lib/utils'
import { useAssistant, type CardEntry } from './AssistantProvider'
import { CardButton } from './CardButtons'
import { CategoryPicker } from './CategoryPicker'
import { TxEditForm } from './TxEditForm'
import { DebtCardLine, DebtsAnswer, DebtTitle } from '@/features/debts/CardDebt'
import { changesNotInSubtitle, titleLinksDebt } from '@/features/debts/lib'

type Tone = 'success' | 'warning' | 'primary' | 'muted' | 'error'
const TONE: Record<Tone, string> = {
  success: 'bg-income-soft text-income',
  warning: 'bg-warning-soft text-warning',
  primary: 'bg-primary-soft text-primary',
  muted: 'bg-muted text-muted-foreground',
  error: 'bg-expense-soft text-destructive',
}

function Marker({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span className={cn('mt-px flex size-5 shrink-0 items-center justify-center rounded-full [&_svg]:size-3', TONE[tone])} aria-hidden>
      {children}
    </span>
  )
}

/** Animated check drawn by stroke (DESIGN §7.2.2). */
function DrawnCheck() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6 9 17l-5-5" pathLength={1} className="[stroke-dasharray:1] [stroke-dashoffset:1] motion-safe:animate-[pf-draw_180ms_ease-out_forwards] motion-reduce:[stroke-dashoffset:0]" />
      <style>{'@keyframes pf-draw{to{stroke-dashoffset:0}}'}</style>
    </svg>
  )
}

function splitNew(title: string): [string, boolean] {
  return title.includes('🆕') ? [title.replace(/\s*🆕\s*/g, ' ').trim(), true] : [title, false]
}

function NewChip() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="rounded-full bg-primary-soft px-1.5 text-caption text-primary" tabIndex={0} data-testid="chip-new">🆕</span>
      </TooltipTrigger>
      <TooltipContent>Новая категория, создана автоматически</TooltipContent>
    </Tooltip>
  )
}

interface Props {
  entry: CardEntry
  compact?: boolean
  onOpenTx?: (id: number) => void
}

export function ResultCard({ entry, compact, onOpenTx }: Props) {
  const { act, setEditing, applyEdit, setCategory, removeTx, dismiss } = useAssistant()
  const { card } = entry
  const [expanded, setExpanded] = useState(false)
  const click = (b: Button) => () => void act(entry.uid, b)
  // the name of a person opens their card (web: «Счета», Quick: the full panel) — same path as `open`
  const openDebt = (id: number) => void act(entry.uid, { id: `debt-${id}`, label: '', style: 'ghost', hotkey: null, action: { kind: 'open', route: `/accounts?debt=${id}` } })
  const debt = (card.kind === 'created' || card.kind === 'needs_account' || card.kind === 'updated') ? card.debt ?? null : null
  const btn = (b: Button, extra: { autoFocus?: boolean } = {}) => (
    <CardButton key={b.id} b={b} busy={entry.busy === b.id || (entry.busy === 'undo' && b.action.kind === 'undo')} onClick={click(b)} compact={compact} {...extra} />
  )
  const tx: TransactionOut | null = 'transaction' in card && card.transaction ? card.transaction : null

  // (i) undone — one muted line with [Вернуть]
  if (card.kind === 'undone') {
    return (
      <div className="pf-fade-in flex items-center gap-2 px-1 text-small text-muted-foreground" data-testid="result-card" data-kind="undone">
        <Undo2 className="size-3.5 shrink-0" />
        <span className="flex-1 truncate">{card.title}</span>
        {card.buttons.map((b) => btn(b))}
        {entry.error && <span className="text-destructive">{entry.error}</span>}
      </div>
    )
  }

  const [title, isNew] = splitNew(card.title)
  const marker = (() => {
    switch (card.kind) {
      case 'needs_account': return <Marker tone="warning"><Inbox /></Marker>
      case 'preview': return <Marker tone="primary"><CopyCheck /></Marker>
      case 'answer': return <Marker tone="muted"><ChartPie /></Marker>
      case 'unclear': return <Marker tone="muted"><CircleQuestionMark /></Marker>
      case 'error':
        if (entry.network) return <Marker tone="warning"><WifiOff /></Marker>
        if (entry.local === 'in_progress') return <Marker tone="muted"><Clock /></Marker>
        return <Marker tone="error"><CircleAlert /></Marker>
      case 'deleted': return <Marker tone="muted"><Trash /></Marker>
      default: return <Marker tone="success"><DrawnCheck /></Marker>
    }
  })()

  const changeCat = card.buttons.find((b) => b.action.kind === 'change_category')
  // a debt card's «Излишек — мне» (send_text) gets its own row: three buttons do not fit a 390 px phone /
  // the 420 px Quick window, and «Изменить» used to wrap alone under the card, pressed to the left
  const extraButtons = debt && card.kind !== 'needs_account' ? card.buttons.filter((b) => b.action.kind === 'send_text') : []
  const rightButtons = card.buttons.filter((b) => b.action.kind !== 'change_category' && !extraButtons.includes(b))
  const nameLinked = !!debt && titleLinksDebt(title, debt)
  const approx = card.kind === 'created' ? card.approx : card.kind === 'answer' ? card.headline_approx : null

  return (
    <div
      role="group"
      aria-label={card.title}
      data-testid="result-card"
      data-kind={card.kind}
      className={cn(
        'pf-card-in rounded-lg border bg-card shadow-md',
        compact ? 'p-3' : 'p-3 md:p-4',
        card.kind === 'error' && !entry.network && entry.local !== 'in_progress' && 'pf-shake',
      )}
    >
      <div className="flex items-start gap-2.5">
        {marker}
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5">
              <span className={cn('num text-title-3', card.kind === 'answer' && card.headline && 'text-body font-medium')} data-testid="card-title"><DebtTitle title={title} brief={debt} onOpenDebt={openDebt} /></span>
              {isNew && <NewChip />}
            </div>
            {approx && card.kind === 'created' && <span className="num shrink-0 text-small text-muted-foreground">{approx}</span>}
            {(card.kind === 'error' || card.kind === 'unclear' || card.kind === 'answer') && (
              <button type="button" onClick={() => dismiss(entry.uid)} className="-m-1 rounded-sm p-1 text-muted-foreground hover:text-foreground" aria-label="Закрыть">
                <X className="size-3.5" />
              </button>
            )}
          </div>
          <CardBody entry={entry} compact={compact} expanded={expanded} setExpanded={setExpanded} onOpenTx={onOpenTx} onOpenDebt={openDebt} />
          {debt && <DebtCardLine brief={debt} compact={compact} onOpenDebt={openDebt} subtitle={card.subtitle} nameLinked={nameLinked} />}
          {entry.note && !(debt?.excess && entry.note.startsWith('Излишек')) && <p className="mt-1 text-caption text-muted-foreground">{entry.note}</p>}
          {entry.error && <p className="mt-1 flex items-center gap-1 text-small text-destructive"><CircleAlert className="size-3.5" /> {entry.error}</p>}
          {entry.editing && tx && card.kind !== 'created_many' && (
            <TxEditForm
              key={`${tx.id}:${tx.version}`}
              tx={tx}
              compact={compact}
              busy={entry.busy === 'edit'}
              onCancel={() => setEditing(entry.uid, false)}
              onSave={(patch) => void applyEdit(entry.uid, tx.id, patch)}
            />
          )}
          {card.kind === 'created_many' && entry.editing && (
            <ManyEditor entry={entry} compact={compact} onEdit={applyEdit} onRemove={removeTx} />
          )}
          {card.kind === 'preview' ? (
            <PreviewActions entry={entry} card={card} />
          ) : (
            (changeCat || rightButtons.length > 0 || extraButtons.length > 0) && !(entry.editing && card.kind !== 'created_many') && (
              <div className="mt-2 flex flex-wrap items-center justify-end gap-1.5">
                {extraButtons.length > 0 && (
                  <div className="flex w-full flex-wrap justify-end gap-1.5" data-testid="card-extra-actions">{extraButtons.map((b) => btn(b))}</div>
                )}
                {changeCat && tx && (
                  <CategoryPicker
                    transactionId={tx.id}
                    kind={tx.category?.kind ?? 'expense'}
                    label={changeCat.label}
                    busy={entry.busy === 'category'}
                    onPick={(c) => void setCategory(entry.uid, tx.id, c.id, c.name)}
                  />
                )}
                <span className="flex-1" />
                {card.kind === 'needs_account' ? (
                  <NeedsAccountButtons buttons={rightButtons} render={btn} />
                ) : (
                  rightButtons.map((b) => btn(b))
                )}
              </div>
            )
          )}
        </div>
      </div>
    </div>
  )
}

function NeedsAccountButtons({ buttons, render }: { buttons: Button[]; render: (b: Button) => React.ReactNode }) {
  const choices = buttons.filter((b) => b.action.kind === 'choose_account')
  const rest = buttons.filter((b) => b.action.kind !== 'choose_account')
  return (
    <div className="flex w-full flex-col gap-1.5">
      <div className="flex flex-wrap gap-1.5" data-testid="account-choices">
        {choices.map((b, i) => render({ ...b, hotkey: b.hotkey ?? (b.action.account_id === null ? null : String(i + 1)) }))}
      </div>
      <div className="flex justify-end gap-1.5">{rest.map(render)}</div>
    </div>
  )
}

function CardBody({ entry, compact, expanded, setExpanded, onOpenTx, onOpenDebt }: {
  entry: CardEntry; compact?: boolean; expanded: boolean; setExpanded: (v: boolean) => void; onOpenTx?: (id: number) => void
  onOpenDebt?: (id: number) => void
}) {
  const { card } = entry
  const sub = card.subtitle && <p className="mt-0.5 text-small text-muted-foreground">{card.subtitle}</p>
  switch (card.kind) {
    case 'created':
    case 'balance':
    case 'category':
    case 'applied':
    case 'deleted':
      return sub
    case 'updated':
      return (
        <>
          {sub}
          {/* the server subtitle is «Изменено: <changes>» — list only what it does not already say */}
          {changesNotInSubtitle(card.changes, card.subtitle).length > 0 && (
            <ul className="mt-1 text-small text-muted-foreground">
              {changesNotInSubtitle(card.changes, card.subtitle).map((c) => <li key={c} className="num">{c}</li>)}
            </ul>
          )}
        </>
      )
    case 'created_many':
      return (
        <>
          {sub ?? (
            <p className="mt-0.5 text-small text-muted-foreground">
              {card.transactions.map((t) => `${t.category?.name ?? txTitle(t)} ${txAmount(t).text.replace(/^−/, '')}`).join(' · ')}
            </p>
          )}
        </>
      )
    case 'needs_account':
      return (
        <>
          {sub}
          <p className="mt-0.5 text-small">{card.question}</p>
        </>
      )
    case 'preview':
      return <PreviewBody card={card} expanded={expanded} setExpanded={setExpanded} compact={compact} />
    case 'answer':
      return (
        <>
          {card.headline && (
            <div className="mt-0.5 flex items-baseline gap-3">
              <span className="num text-title-2">{card.headline}</span>
              {card.headline_approx && <span className="num text-small text-muted-foreground">{card.headline_approx}</span>}
            </div>
          )}
          {sub}
          {card.text && <p className="mt-1 text-body whitespace-pre-line">{card.text}</p>}
          {card.debts ? (
            <DebtsAnswer debts={card.debts} compact={compact} onOpenDebt={onOpenDebt} />
          ) : card.answer_type === 'list' ? (
            <ol className="mt-1.5 flex flex-col">
              {(card.transactions.length ? card.transactions.slice(0, 5) : []).map((t, i) => (
                <li key={t.id} className="flex items-center gap-2">
                  <span className="num w-4 text-caption text-muted-foreground">{i + 1}</span>
                  <TxRow tx={t} dense onClick={onOpenTx ? () => onOpenTx(t.id) : undefined} className="px-1" />
                </li>
              ))}
              {!card.transactions.length && card.lines.slice(0, 5).map((l, i) => (
                <li key={`${l.label}${i}`} className="flex items-center gap-2 py-0.5 text-small">
                  <span className="num w-4 text-caption text-muted-foreground">{i + 1}</span>
                  <button type="button" disabled={!l.transaction_id || !onOpenTx} onClick={() => l.transaction_id && onOpenTx?.(l.transaction_id)} className="flex-1 truncate text-left enabled:hover:underline">{l.label}</button>
                  <span className="num">{l.value}</span>
                </li>
              ))}
            </ol>
          ) : (
            card.lines.length > 0 && (
              <p className="mt-1 text-small text-muted-foreground">
                {card.lines.slice(0, 3).map((l) => `${l.label} ${l.value}`).join(' · ')}
                {card.lines.length > 3 && ` · ещё ${card.lines.length - 3}`}
              </p>
            )
          )}
        </>
      )
    case 'unclear':
      return <p className="mt-0.5 text-small text-muted-foreground">{card.question || card.subtitle}</p>
    case 'error':
      return (card.text || card.subtitle) ? <p className="mt-0.5 text-small text-muted-foreground">{card.text ?? card.subtitle}</p> : null
    default:
      return sub
  }
}

function PreviewBody({ card, expanded, setExpanded, compact }: { card: PreviewCard; expanded: boolean; setExpanded: (v: boolean) => void; compact?: boolean }) {
  const del = card.operation === 'bulk_delete'
  const limit = expanded ? card.items.length : compact ? 5 : 8
  const shown = card.items.slice(0, limit)
  const more = card.total_count - shown.length
  return (
    <div className="mt-0.5">
      <p className="text-small text-muted-foreground">{card.change_summary}</p>
      <div className="my-2 border-t" />
      <table className="w-full text-small" data-testid="preview-items">
        <tbody>
          {shown.map(({ transaction: t, before, after }) => (
            <tr key={t.id} className="h-6">
              <td className="num pr-3 whitespace-nowrap text-muted-foreground">{compact ? formatDayMonth(t.occurred_on) : formatShortDate(t.occurred_on)}</td>
              <td className="w-full max-w-0 truncate pr-3">{txTitle(t)}</td>
              <td className={cn('num text-right whitespace-nowrap', del && 'text-muted-foreground line-through')}>
                {before && after && before !== after && /\d/.test(before) ? `${before} → ${after}` : txAmount(t).text.replace(/^−/, '')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {more > 0 && (
        <button type="button" onClick={() => setExpanded(true)} className="text-small text-muted-foreground hover:text-foreground" disabled={expanded}>
          и ещё {more}
        </button>
      )}
      <div className="my-2 border-t" />
      {card.total && <p className="num text-small font-medium">{card.total}</p>}
    </div>
  )
}

/** Preview: focus on «Применить»; Enter applies, Esc cancels (DESIGN §9.2 e). */
function PreviewActions({ entry, card }: { entry: CardEntry; card: PreviewCard }) {
  const { act } = useAssistant()
  const ref = useRef<HTMLDivElement>(null)
  const cancel = card.buttons.find((b) => b.action.kind === 'cancel_preview') ??
    { id: 'cancel', label: 'Отмена', style: 'ghost', hotkey: 'Esc', action: { kind: 'cancel_preview', preview_token: card.preview_token } } as Button
  const confirm = card.buttons.find((b) => b.action.kind === 'confirm_preview') ??
    { id: 'confirm', label: 'Применить', style: 'primary', hotkey: 'Enter', action: { kind: 'confirm_preview', preview_token: card.preview_token } } as Button
  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>('[data-action="confirm_preview"]')?.focus()
  }, [])
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); void act(entry.uid, cancel) }
  }
  return (
    <div ref={ref} className="flex justify-end gap-1.5" onKeyDown={onKeyDown}>
      <UiButton type="button" size="sm" variant="ghost" onClick={() => void act(entry.uid, cancel)} data-action="cancel_preview" data-testid="btn-cancel-preview">
        {cancel.label} <Kbd className="hidden md:inline-flex">Esc</Kbd>
      </UiButton>
      <UiButton
        type="button" size="sm" variant={card.operation === 'bulk_delete' ? 'destructive' : 'default'}
        onClick={() => void act(entry.uid, confirm)} disabled={entry.busy === 'confirm'} aria-busy={entry.busy === 'confirm' || undefined}
        data-action="confirm_preview" data-testid="btn-confirm-preview"
      >
        {entry.busy === 'confirm' ? <Spinner size={14} /> : null}
        {confirm.label} <CornerDownLeft className="opacity-70" />
      </UiButton>
    </div>
  )
}

function ManyEditor({ entry, compact, onEdit, onRemove }: {
  entry: CardEntry; compact?: boolean
  onEdit: (uid: string, txId: number, patch: import('@/lib/api/types').TransactionPatch) => Promise<boolean>
  onRemove: (uid: string, tx: TransactionOut) => Promise<boolean>
}) {
  const [editId, setEditId] = useState<number | null>(null)
  if (entry.card.kind !== 'created_many') return null
  return (
    <ul className="mt-2 flex flex-col divide-y rounded-md border">
      {entry.card.transactions.map((t) => (
        <li key={t.id} className="px-2 py-1">
          <div className="flex items-center gap-2 text-small">
            <span className="flex-1 truncate">{t.category?.name ?? txTitle(t)}</span>
            <span className="num">{txAmount(t).text.replace(/^−/, '')}</span>
            <UiButton size="icon-xs" variant="ghost" aria-label="Изменить" onClick={() => setEditId(editId === t.id ? null : t.id)}><Pencil /></UiButton>
            <UiButton size="icon-xs" variant="ghost" aria-label="Удалить" disabled={entry.busy === `del:${t.id}`} onClick={() => void onRemove(entry.uid, t)}>
              {entry.busy === `del:${t.id}` ? <Spinner size={12} /> : <X />}
            </UiButton>
          </div>
          {editId === t.id && (
            <TxEditForm
              key={`${t.id}:${t.version}`}
              tx={t} compact={compact} busy={entry.busy === 'edit'}
              onCancel={() => setEditId(null)}
              onSave={async (p) => { if (await onEdit(entry.uid, t.id, p)) setEditId(null) }}
            />
          )}
        </li>
      ))}
    </ul>
  )
}

