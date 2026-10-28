// Empty state = always a hint what to write (DESIGN §8.2).
import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface Props {
  icon: LucideIcon
  title: string
  text?: string
  chips?: string[]
  onChip?: (text: string) => void
  action?: React.ReactNode
  className?: string
}

export function EmptyState({ icon: Icon, title, text, chips, onChip, action, className }: Props) {
  return (
    <div className={cn('flex flex-col items-center gap-2 px-4 py-8 text-center', className)}>
      <span className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <Icon className="size-5" strokeWidth={1.75} />
      </span>
      <div className="text-title-3">{title}</div>
      {text && <p className="text-small text-muted-foreground">{text}</p>}
      {chips && chips.length > 0 && (
        <div className="mt-1 flex flex-wrap justify-center gap-1.5">
          {chips.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => onChip?.(c)}
              className="rounded-full border bg-card px-2.5 py-1 text-small text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
            >
              {c}
            </button>
          ))}
        </div>
      )}
      {action}
    </div>
  )
}
