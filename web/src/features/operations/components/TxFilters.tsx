// Filter bar (DESIGN §9.5): search (/), period, category, account, currency, type, business/personal;
// active filters as removable chips; mobile — search + ListFilter (N) → bottom sheet with the same controls.
import { useState, type ComponentProps, type ReactNode, type RefObject } from 'react'
import { CalendarDays, ChevronDown, ListFilter, Search, X } from 'lucide-react'
import type { AccountOut, CategoryOut, TxKind } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PRESET_LABELS } from '../lib/dates'
import { activeFilterCount, ALL_KINDS, DEFAULT_FILTER, KIND_LABELS, parseAmountQuery, periodLabel, type BizFilter, type OpsFilter } from '../lib/filters'
import { formatMoney } from '../lib/format'
import { Button, Checkbox, Chip, Field, Input, Kbd, Segmented } from '../ui/kit'
import { Popover, Sheet } from '../ui/overlays'
import { CategoryMultiList } from './CategoryPicker'
import { CurrencySelect } from './CurrencySelect'

type SetFilter = (patch: Partial<OpsFilter>) => void

const PERIODS = ['today', 'yesterday', 'last_7_days', 'this_month', 'last_month', 'this_year', 'all_time']

function DropTrigger({ label, active, icon, className, ...rest }: { label: ReactNode; active?: boolean; icon?: ReactNode } & ComponentProps<'button'>) {
  return (
    <button
      type="button"
      {...rest}
      className={cn(className, 
        'inline-flex h-8 shrink-0 items-center gap-1.5 rounded-sm border px-2.5 text-[13px] font-medium focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none',
        active ? 'border-transparent bg-primary-soft text-primary' : 'border-input bg-card hover:bg-surface-2',
      )}
    >
      {icon}
      <span className="max-w-40 truncate">{label}</span>
      <ChevronDown className="size-3.5 opacity-70" />
    </button>
  )
}

export function PeriodControl({ f, set, today }: { f: OpsFilter; set: SetFilter; today: string }) {
  const [from, setFrom] = useState(f.from)
  const [to, setTo] = useState(f.to)
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap gap-1.5">
        {PERIODS.map((p) => (
          <Chip key={p} active={f.period === p} onClick={() => set({ period: p, from: '', to: '' })}>
            {PRESET_LABELS[p]}
          </Chip>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="С" htmlFor="pf-from">
          <Input id="pf-from" type="date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} />
        </Field>
        <Field label="По" htmlFor="pf-to">
          <Input id="pf-to" type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} />
        </Field>
      </div>
      <Button size="sm" variant="secondary" disabled={!from && !to} onClick={() => set({ period: 'custom', from, to })}>
        <CalendarDays /> Показать период
      </Button>
      <p className="text-xs text-muted-foreground">Сейчас: {periodLabel(f, today)}</p>
    </div>
  )
}

function AccountsControl({ f, set, accounts }: { f: OpsFilter; set: SetFilter; accounts: AccountOut[] }) {
  const toggle = (id: number) => set({ acc: f.acc.includes(id) ? f.acc.filter((x) => x !== id) : [...f.acc, id] })
  return (
    <div className="grid gap-0.5">
      {accounts.map((a) => (
        <label key={a.id} className="flex h-9 cursor-pointer items-center gap-2 rounded-sm px-2 text-sm hover:bg-accent">
          <Checkbox checked={f.acc.includes(a.id)} onCheckedChange={() => toggle(a.id)} label={a.name} />
          <span className="flex-1 truncate">{a.name}</span>
          <span className="text-xs text-muted-foreground">{a.is_multi_asset ? 'кошелёк' : a.currency}</span>
        </label>
      ))}
      <label className="mt-1 flex h-9 cursor-pointer items-center gap-2 rounded-sm border-t border-border px-2 pt-1 text-sm hover:bg-accent">
        <Checkbox checked={f.status === 'needs_account'} onCheckedChange={(v) => set({ status: v ? 'needs_account' : '' })} label="Без счёта — разобрать" />
        <span className="flex-1">Без счёта (разобрать)</span>
      </label>
    </div>
  )
}

function KindsControl({ f, set }: { f: OpsFilter; set: SetFilter }) {
  const toggle = (k: TxKind) => set({ kind: f.kind.includes(k) ? f.kind.filter((x) => x !== k) : [...f.kind, k] })
  return (
    <div className="grid gap-2">
      <div className="grid gap-0.5">
        {ALL_KINDS.map((k) => (
          <label key={k} className="flex h-9 cursor-pointer items-center gap-2 rounded-sm px-2 text-sm hover:bg-accent">
            <Checkbox checked={f.kind.includes(k)} onCheckedChange={() => toggle(k)} label={KIND_LABELS[k]} />
            {KIND_LABELS[k]}
          </label>
        ))}
      </div>
    </div>
  )
}

function BizControl({ f, set }: { f: OpsFilter; set: SetFilter }) {
  return (
    <Segmented<BizFilter>
      label="Бизнес или личное"
      value={f.biz}
      onChange={(v) => set({ biz: v })}
      options={[
        { value: 'all', label: 'Все' },
        { value: 'business', label: 'Бизнес' },
        { value: 'personal', label: 'Личное' },
      ]}
    />
  )
}

export function ActiveChips({ f, set, categories, accounts, today, onReset }: { f: OpsFilter; set: SetFilter; categories: CategoryOut[]; accounts: AccountOut[]; today: string; onReset: () => void }) {
  const chips: { key: string; label: string; clear: () => void }[] = []
  if (f.period !== DEFAULT_FILTER.period && !(f.status && f.period === 'all_time')) chips.push({ key: 'p', label: `Период: ${periodLabel(f, today)}`, clear: () => set({ period: f.status ? 'all_time' : DEFAULT_FILTER.period, from: '', to: '' }) })
  for (const id of f.cat) chips.push({ key: `c${id}`, label: `Категория: ${categories.find((c) => c.id === id)?.name ?? id}`, clear: () => set({ cat: f.cat.filter((x) => x !== id) }) })
  for (const id of f.acc) chips.push({ key: `a${id}`, label: `Счёт: ${accounts.find((a) => a.id === id)?.name ?? id}`, clear: () => set({ acc: f.acc.filter((x) => x !== id) }) })
  if (f.status) chips.push({ key: 's', label: 'Разобрать: без счёта', clear: () => set({ status: '' }) })
  if (f.cur) chips.push({ key: 'cur', label: `Валюта: ${f.cur}`, clear: () => set({ cur: '' }) })
  for (const k of f.kind) chips.push({ key: `k${k}`, label: KIND_LABELS[k], clear: () => set({ kind: f.kind.filter((x) => x !== k) }) })
  if (f.biz !== 'all') chips.push({ key: 'b', label: f.biz === 'business' ? 'Бизнес' : 'Личное', clear: () => set({ biz: 'all' }) })
  if (f.min || f.max) {
    const cur = f.amtCur || 'VND'
    const label = f.min && f.max ? (f.min === f.max ? `= ${formatMoney(f.min, cur)}` : `${formatMoney(f.min, cur)}–${formatMoney(f.max, cur)}`) : f.min ? `от ${formatMoney(f.min, cur)}` : `до ${formatMoney(f.max, cur)}`
    chips.push({ key: 'amt', label: `Сумма ${label}`, clear: () => set({ min: '', max: '', amtCur: '' }) })
  }
  const aq = f.q ? parseAmountQuery(f.q) : null
  if (aq) {
    const label = aq.min && aq.max ? `= ${formatMoney(aq.min, aq.currency)}` : aq.min ? `больше ${formatMoney(aq.min, aq.currency)}` : `меньше ${formatMoney(aq.max!, aq.currency)}`
    chips.push({ key: 'aq', label: `Сумма ${label}`, clear: () => set({ q: '' }) })
  }
  if (!chips.length) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {chips.map((c) => (
        <span key={c.key} className="inline-flex h-7 items-center gap-1 rounded-sm bg-primary-soft pr-1 pl-2.5 text-xs font-medium text-primary">
          {c.label}
          <button type="button" onClick={c.clear} aria-label={`Убрать фильтр ${c.label}`} className="grid size-5 place-items-center rounded-xs hover:bg-primary/10">
            <X className="size-3" />
          </button>
        </span>
      ))}
      <Button size="sm" variant="ghost" onClick={onReset}>
        Сбросить
      </Button>
    </div>
  )
}

export function TxFilters({ f, set, categories, accounts, today, mobile, searchRef, q, setQ, onReset, right }: { f: OpsFilter; set: SetFilter; categories: CategoryOut[]; accounts: AccountOut[]; today: string; mobile: boolean; searchRef: RefObject<HTMLInputElement | null>; q: string; setQ: (v: string) => void; onReset: () => void; right?: ReactNode }) {
  const [sheet, setSheet] = useState(false)
  const n = activeFilterCount(f)
  const search = (
    <div className="relative min-w-0 flex-1 md:max-w-80 md:min-w-56">
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
      <Input ref={searchRef} type="search" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Escape' && (q ? setQ('') : (e.target as HTMLInputElement).blur())} placeholder="Поиск по описанию, продавцу, сумме" aria-label="Поиск операций" className="h-9 pr-8 pl-8" />
      {!q && <Kbd className="absolute top-1/2 right-2 -translate-y-1/2">/</Kbd>}
    </div>
  )
  if (mobile) {
    return (
      <div className="flex items-center gap-2">
        {search}
        <Button variant={n ? 'soft' : 'outline'} size="md" onClick={() => setSheet(true)} aria-label={`Фильтры${n ? `: ${n}` : ''}`}>
          <ListFilter />
          {n > 0 && <span className="num">{n}</span>}
        </Button>
        {right}
        <Sheet open={sheet} onOpenChange={setSheet} title="Фильтры" footer={<div className="flex gap-2"><Button variant="ghost" className="flex-1" onClick={onReset}>Сбросить</Button><Button variant="primary" className="flex-1" onClick={() => setSheet(false)}>Показать</Button></div>}>
          <div className="grid gap-5">
            <Field label="Период">
              <PeriodControl f={f} set={set} today={today} />
            </Field>
            <Field label="Бизнес или личное">
              <BizControl f={f} set={set} />
            </Field>
            <Field label="Тип">
              <KindsControl f={f} set={set} />
            </Field>
            <Field label="Валюта" htmlFor="m-cur">
              <CurrencySelect id="m-cur" value={f.cur} onChange={(v) => set({ cur: v })} allowEmpty />
            </Field>
            <Field label="Счёт">
              <AccountsControl f={f} set={set} accounts={accounts.filter((a) => !a.is_archived)} />
            </Field>
            <Field label="Категория">
              <CategoryMultiList cats={categories} selected={f.cat} onToggle={(id) => set({ cat: f.cat.includes(id) ? f.cat.filter((x) => x !== id) : [...f.cat, id] })} />
            </Field>
          </div>
        </Sheet>
      </div>
    )
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      {search}
      <Popover trigger={<DropTrigger label={periodLabel(f, today)} active={f.period !== DEFAULT_FILTER.period} icon={<CalendarDays className="size-3.5" />} />} className="w-80">
        <PeriodControl f={f} set={set} today={today} />
      </Popover>
      <Popover trigger={<DropTrigger label={f.cat.length ? `Категории: ${f.cat.length}` : 'Категория'} active={!!f.cat.length} />} className="w-80">
        <CategoryMultiList cats={categories} selected={f.cat} onToggle={(id) => set({ cat: f.cat.includes(id) ? f.cat.filter((x) => x !== id) : [...f.cat, id] })} />
      </Popover>
      <Popover trigger={<DropTrigger label={f.acc.length ? `Счета: ${f.acc.length}` : f.status ? 'Без счёта' : 'Счёт'} active={!!f.acc.length || !!f.status} />} className="w-64 p-1">
        <AccountsControl f={f} set={set} accounts={accounts.filter((a) => !a.is_archived)} />
      </Popover>
      <Popover trigger={<DropTrigger label={f.cur || 'Валюта'} active={!!f.cur} />} className="w-56">
        <Field label="Валюта операции" htmlFor="d-cur">
          <CurrencySelect id="d-cur" value={f.cur} onChange={(v) => set({ cur: v })} allowEmpty />
        </Field>
      </Popover>
      <Popover trigger={<DropTrigger label={f.kind.length === 1 ? KIND_LABELS[f.kind[0]] : f.kind.length ? `Типы: ${f.kind.length}` : 'Тип'} active={!!f.kind.length} />} className="w-56 p-1">
        <KindsControl f={f} set={set} />
      </Popover>
      <BizControl f={f} set={set} />
      {right && <div className="ml-auto flex items-center gap-2">{right}</div>}
    </div>
  )
}
