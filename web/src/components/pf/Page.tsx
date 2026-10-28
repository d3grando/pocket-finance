// Page frame (DESIGN §5.3–5.4): side gutters 16/24/32, content max-w 1200, title-1 header.
import { cn } from '@/lib/utils'

export function Page({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn('mx-auto w-full max-w-[1200px] px-4 md:px-6 lg:px-8', className)}>{children}</div>
}

export function PageHeader({ title, actions, className }: { title: React.ReactNode; actions?: React.ReactNode; className?: string }) {
  return (
    <div className={cn('flex min-h-14 items-center gap-3 pt-2 md:pt-4', className)}>
      <h1 className="text-title-1">{title}</h1>
      <span className="flex-1" />
      {actions}
    </div>
  )
}

/** Section card (DESIGN §5.2: bg-card border, no shadow). */
export function Section({ title, actions, children, className, refreshing, bodyClassName }: {
  title?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string; refreshing?: boolean; bodyClassName?: string
}) {
  return (
    <section className={cn('rounded-lg border bg-card', className)}>
      {(title || actions) && (
        <header className="flex items-center gap-2 px-4 pt-3 md:px-5 md:pt-4">
          {title && <h2 className="text-title-3">{title}</h2>}
          {refreshing && <RefreshDot />}
          <span className="flex-1" />
          {actions}
        </header>
      )}
      <div className={cn('p-4 md:p-5', title && 'pt-2 md:pt-3', bodyClassName)}>{children}</div>
    </section>
  )
}

/** 12 px spinning RefreshCw while refetching in the background (DESIGN §8.1). */
export function RefreshDot() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" className="animate-spin text-muted-foreground" aria-label="Обновляется" role="status">
      <path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
