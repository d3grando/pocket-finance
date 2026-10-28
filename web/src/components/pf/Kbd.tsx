// <Kbd> — DESIGN §11: micro .num, bg-muted border rounded-xs px-1; hidden on touch.
import { cn } from '@/lib/utils'

export function Kbd({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        'pf-kbd num inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-xs border bg-muted px-1 font-sans text-micro text-muted-foreground',
        className,
      )}
    >
      {children}
    </kbd>
  )
}
