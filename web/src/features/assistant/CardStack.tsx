// Stack of result cards under the AI line (DESIGN §9.1): max 3 visible (Quick Capture: 1),
// older collapse into «ещё N». Results are announced through an aria-live region.
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { useAssistant } from './AssistantProvider'
import { ResultCard } from './ResultCard'

interface Props {
  max?: number
  compact?: boolean
  className?: string
  onOpenTx?: (id: number) => void
}

export function CardStack({ max = 3, compact, className, onOpenTx }: Props) {
  const { entries, announce } = useAssistant()
  const [showAll, setShowAll] = useState(false)
  const visible = entries.filter((e) => !e.dismissed)
  const shown = showAll ? visible : visible.slice(0, max)
  const hidden = visible.length - shown.length
  return (
    <div className={cn('flex flex-col gap-2', className)} data-testid="card-stack">
      <div aria-live="polite" aria-atomic="true" className="sr-only">{announce}</div>
      {shown.map((e) => <ResultCard key={e.uid} entry={e} compact={compact} onOpenTx={onOpenTx} />)}
      {hidden > 0 && (
        <button type="button" onClick={() => setShowAll(true)} className="self-start px-1 text-small text-muted-foreground hover:text-foreground">
          ещё {hidden}
        </button>
      )}
      {showAll && visible.length > max && (
        <button type="button" onClick={() => setShowAll(false)} className="self-start px-1 text-small text-muted-foreground hover:text-foreground">
          свернуть
        </button>
      )}
    </div>
  )
}
