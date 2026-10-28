// «Inbox · Разобрать: N» (DESIGN §9.3) → /operations?status=needs_account.
import { Inbox } from 'lucide-react'
import { Link } from 'react-router'
import { useNeedsAccountCount } from '@/lib/api/hooks'
import { cn } from '@/lib/utils'

export function NeedsAccountBadge({ className, count: given }: { className?: string; count?: number }) {
  const q = useNeedsAccountCount({ enabled: given === undefined })
  const count = given ?? q.data?.count ?? 0
  if (!count) return null
  return (
    <Link
      to="/operations?status=needs_account"
      data-testid="needs-account-badge"
      className={cn(
        'inline-flex h-7 items-center gap-1.5 rounded-full bg-warning-soft px-2.5 text-caption text-warning transition-colors hover:bg-warning/15 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none',
        className,
      )}
    >
      <Inbox className="size-3.5" />
      <span>Разобрать: <span className="num">{count}</span></span>
    </Link>
  )
}
