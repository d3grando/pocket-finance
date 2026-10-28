// «В другую ▾» (DESIGN §9.2 b): popover with Command search; «Похожие» from
// GET /categories/suggest?transaction_id= first, then all categories of the same kind. Enter applies.
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Spinner } from '@/components/pf/Spinner'
import { categoryIcon } from '@/components/pf/icons'
import { categoriesApi } from '@/lib/api/endpoints'
import type { CategoryOut } from '@/lib/api/types'

interface Props {
  transactionId: number
  kind: 'expense' | 'income'
  label?: string
  busy?: boolean
  onPick: (c: CategoryOut) => void
}

export function CategoryPicker({ transactionId, kind, label = 'В другую', busy, onPick }: Props) {
  const [open, setOpen] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['categories', 'suggest', transactionId],
    queryFn: () => categoriesApi.suggest({ transaction_id: transactionId }),
    enabled: open,
    staleTime: 60_000,
  })
  const similar = (data?.similar ?? []).map((s) => s.category).filter((c) => c.kind === kind)
  const similarIds = new Set(similar.map((c) => c.id))
  const all = (data?.all ?? []).filter((c) => c.kind === kind && !c.is_archived && !similarIds.has(c.id))

  const item = (c: CategoryOut) => {
    const Icon = categoryIcon(c)
    return (
      <CommandItem
        key={c.id}
        value={`${c.name} ${c.aliases.join(' ')} #${c.id}`}
        onSelect={() => { setOpen(false); onPick(c) }}
      >
        <Icon className="size-4 text-muted-foreground" />
        <span className="flex-1 truncate">{c.name}</span>
        {c.is_business && <span className="text-caption text-muted-foreground">бизнес</span>}
      </CommandItem>
    )
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" size="sm" variant="outline" disabled={busy} data-testid="btn-change-category" aria-busy={busy || undefined}>
          {busy && <Spinner size={14} />}
          {label}
          <ChevronDown className="opacity-60" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-0" align="start" onKeyDown={(e) => { if (e.key === 'Escape') e.stopPropagation() }}>
        <Command>
          <CommandInput placeholder="Категория…" autoFocus />
          <CommandList>
            {isLoading && (
              <div className="flex items-center gap-2 p-3 text-small text-muted-foreground"><Spinner /> Загружаю…</div>
            )}
            <CommandEmpty>Ничего не нашлось</CommandEmpty>
            {similar.length > 0 && <CommandGroup heading="Похожие">{similar.map(item)}</CommandGroup>}
            {all.length > 0 && <CommandGroup heading="Все">{all.map(item)}</CommandGroup>}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
