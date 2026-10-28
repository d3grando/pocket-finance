// Category choice with search (cmdk), grouped «Расходы / Бизнес-расходы / Доходы» (DESIGN §9.4/§9.5).
import { useRef, useState, type ReactNode } from 'react'
import { Command } from 'cmdk'
import { Check, ChevronDown } from 'lucide-react'
import type { CategoryOut } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Popover } from '../ui/overlays'
import { IconTile, iconByName } from '../ui/icons'
import { Checkbox } from '../ui/kit'

export function groupCategories(cats: CategoryOut[], kind?: 'expense' | 'income'): { heading: string; items: CategoryOut[] }[] {
  const live = cats.filter((c) => !c.is_archived && (!kind || c.kind === kind))
  const byOrder = (a: CategoryOut, b: CategoryOut) => Number(b.is_new) - Number(a.is_new) || a.sort_order - b.sort_order || a.name.localeCompare(b.name, 'ru')
  const groups = [
    { heading: 'Расходы', items: live.filter((c) => c.kind === 'expense' && !c.is_business).sort(byOrder) },
    { heading: 'Бизнес-расходы', items: live.filter((c) => c.kind === 'expense' && c.is_business).sort(byOrder) },
    { heading: 'Доходы', items: live.filter((c) => c.kind === 'income').sort(byOrder) },
  ]
  return groups.filter((g) => g.items.length)
}

function CategoryList({ cats, kind, selected, onPick, multi, autoFocus = true }: { cats: CategoryOut[]; kind?: 'expense' | 'income'; selected: number[]; onPick: (c: CategoryOut) => void; multi?: boolean; autoFocus?: boolean }) {
  const groups = groupCategories(cats, kind)
  // Without autofocus (filters sheet/popover) cmdk must not pre-select the first item: selecting
  // calls scrollIntoView, which scrolled the whole sheet down to the categories on open. The value is
  // controlled and accepted only after the user types, uses the keyboard or moves the pointer here.
  const lazy = !autoFocus
  const armed = useRef(false)
  const [value, setValue] = useState('')
  const arm = () => {
    armed.current = true
  }
  const lazyProps = lazy ? { value, onValueChange: (v: string) => armed.current && setValue(v), onKeyDown: arm, onPointerMove: arm } : {}
  return (
    <Command loop className="flex flex-col" label="Категории" {...lazyProps}>
      <Command.Input autoFocus={autoFocus} placeholder="Найти категорию" className="mb-1 h-9 w-full rounded-sm border border-input bg-card px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50 max-md:text-base" />
      <Command.List className="max-h-72 overflow-y-auto">
        <Command.Empty className="px-2 py-6 text-center text-sm text-muted-foreground">Не нашлось</Command.Empty>
        {groups.map((g) => (
          <Command.Group key={g.heading} heading={g.heading} className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground">
            {g.items.map((c) => {
              const on = selected.includes(c.id)
              return (
                <Command.Item
                  key={c.id}
                  value={`${c.name} ${c.aliases.join(' ')} ${c.id}`}
                  onSelect={() => onPick(c)}
                  className="flex h-9 cursor-default items-center gap-2 rounded-sm px-2 text-sm data-[selected=true]:bg-accent"
                >
                  {multi && (
                    <span className="pointer-events-none" aria-hidden>
                      <Checkbox checked={on} onCheckedChange={() => undefined} />
                    </span>
                  )}
                  <IconTile icon={iconByName(c.icon)} business={c.is_business} size={24} />
                  <span className="flex-1 truncate">{c.name}</span>
                  {c.is_new && <span title="Новая категория, создана автоматически">🆕</span>}
                  {!multi && on && <Check className="size-4 text-primary" />}
                </Command.Item>
              )
            })}
          </Command.Group>
        ))}
      </Command.List>
    </Command>
  )
}

/** Single-select trigger + popover. */
export function CategoryPicker({ cats, value, onChange, kind, id, className, triggerLabel }: { cats: CategoryOut[]; value: number | null; onChange: (c: CategoryOut) => void; kind?: 'expense' | 'income'; id?: string; className?: string; triggerLabel?: ReactNode }) {
  const [open, setOpen] = useState(false)
  const cur = cats.find((c) => c.id === value)
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      className="w-80"
      trigger={
        <button
          id={id}
          type="button"
          className={cn('flex h-9 w-full min-w-0 items-center gap-2 rounded-sm border border-input bg-card px-2.5 text-left text-sm hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none', className)}
          aria-label="Категория"
        >
          {triggerLabel ?? (
            <>
              {cur && <IconTile icon={iconByName(cur.icon)} business={cur.is_business} size={20} />}
              <span className={cn('flex-1 truncate', !cur && 'text-muted-foreground')}>{cur ? cur.name : 'Категория'}</span>
              {cur?.is_new && <span>🆕</span>}
              <ChevronDown className="size-4 text-muted-foreground" />
            </>
          )}
        </button>
      }
    >
      <CategoryList
        cats={cats}
        kind={kind}
        selected={value ? [value] : []}
        onPick={(c) => {
          onChange(c)
          setOpen(false)
        }}
      />
    </Popover>
  )
}

/** Multi-select list (filters). */
export function CategoryMultiList({ cats, selected, onToggle }: { cats: CategoryOut[]; selected: number[]; onToggle: (id: number) => void }) {
  return <CategoryList cats={cats} selected={selected} onPick={(c) => onToggle(c.id)} multi autoFocus={false} />
}
