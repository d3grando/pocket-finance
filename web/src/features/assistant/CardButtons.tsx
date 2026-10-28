// Renders Card.buttons (API.md §9.1) as shadcn buttons with keyboard hints.
import { ChevronDown, Pencil, RotateCcw, Undo2 } from 'lucide-react'
import { Button as UiButton } from '@/components/ui/button'
import { Kbd } from '@/components/pf/Kbd'
import { Spinner } from '@/components/pf/Spinner'
import type { Button } from '@/lib/api/types'
import { useConnection } from '@/lib/connection'
import { cn } from '@/lib/utils'

const VARIANT: Record<Button['style'], 'default' | 'secondary' | 'outline' | 'ghost' | 'destructive'> = {
  primary: 'default', secondary: 'secondary', outline: 'outline', ghost: 'ghost', destructive: 'destructive',
}

const HINT: Record<string, string> = { 'Ctrl+Z': 'Ctrl Z', 'Ctrl+E': 'Ctrl E', Enter: '⏎', Esc: 'Esc' }

export function CardButton({
  b, busy, onClick, compact, autoFocus, showHint = true,
}: { b: Button; busy?: boolean; onClick: () => void; compact?: boolean; autoFocus?: boolean; showHint?: boolean }) {
  const { status } = useConnection()
  const offline = status === 'offline'
  const kind = b.action.kind
  const needsNet = !['later', 'manual', 'open', 'change_category', 'edit', 'cancel_preview', 'send_text', 'retry'].includes(kind)
  const icon = kind === 'undo' ? <Undo2 /> : kind === 'redo' ? <RotateCcw /> : kind === 'edit' ? <Pencil /> : null
  const digit = kind === 'choose_account' && b.hotkey && /^\d$/.test(b.hotkey) ? b.hotkey : null
  const hint = !digit && b.hotkey ? HINT[b.hotkey] ?? b.hotkey : null
  return (
    <UiButton
      type="button"
      size="sm"
      variant={VARIANT[b.style] ?? 'ghost'}
      onClick={onClick}
      disabled={busy || (offline && needsNet)}
      title={offline && needsNet ? 'Нет связи' : undefined}
      aria-busy={busy || undefined}
      autoFocus={autoFocus}
      data-testid={`btn-${b.id}`}
      data-action={kind}
      className={cn(compact && 'h-7 px-2 text-small')}
    >
      {busy ? <Spinner size={14} /> : icon}
      {digit && <span className="num text-caption text-muted-foreground">{digit}</span>}
      {b.label}
      {kind === 'change_category' && <ChevronDown className="opacity-60" />}
      {hint && showHint && <Kbd className="ml-0.5 hidden md:inline-flex">{hint}</Kbd>}
    </UiButton>
  )
}
