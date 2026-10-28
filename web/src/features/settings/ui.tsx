// Settings layout atoms: a section card and a label/control row (two columns on desktop, stacked on mobile).
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export function SettingsSection({ title, description, children, actions, className }: { title: string; description?: ReactNode; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <section className={cn('min-w-0 rounded-lg border border-border bg-card', className)} aria-label={title}>
      <header className="flex flex-wrap items-start justify-between gap-2 px-4 pt-4 md:px-5">
        <div className="min-w-0">
          <h2 className="text-[17px] leading-6 font-semibold">{title}</h2>
          {description && <p className="mt-0.5 text-[13px] text-muted-foreground">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
      </header>
      <div className="px-4 pt-3 pb-4 md:px-5 md:pb-5">{children}</div>
    </section>
  )
}

export function Row({ label, hint, children, htmlFor }: { label: ReactNode; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="grid gap-2 border-b border-border py-3 first:pt-0 last:border-0 last:pb-0 md:grid-cols-[minmax(0,1fr)_minmax(0,320px)] md:items-center md:gap-6">
      <div className="min-w-0">
        <label htmlFor={htmlFor} className="text-sm font-medium">
          {label}
        </label>
        {hint && <p className="text-[13px] text-muted-foreground">{hint}</p>}
      </div>
      <div className="flex min-w-0 items-center md:justify-end">{children}</div>
    </div>
  )
}
