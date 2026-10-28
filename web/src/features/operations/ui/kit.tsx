// Local UI kit for the pages (shadcn-style, styled by DESIGN.md tokens). the shell's src/components/ui did not
// exist when the pages was built; these are drop-in replacements kept inside the the pages zone.
import { useId, type ComponentProps, type ReactNode } from 'react'
import { motion } from 'motion/react'
import { Checkbox as RCheckbox, Switch as RSwitch } from 'radix-ui'
import { Check, LoaderCircle, WifiOff } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useDelayed, useReducedMotion } from './hooks'

const focus = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-offset-2 focus-visible:ring-offset-background'

const variants = {
  primary: 'bg-primary text-primary-foreground hover:bg-primary/90 shadow-xs',
  secondary: 'bg-secondary text-secondary-foreground hover:bg-surface-2',
  outline: 'border border-input bg-card hover:bg-surface-2',
  ghost: 'hover:bg-surface-2 text-foreground',
  destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
  soft: 'bg-primary-soft text-primary hover:bg-primary-soft/80',
  warning: 'bg-warning-soft text-warning hover:bg-warning-soft/80',
} as const
const sizes = {
  sm: 'h-8 px-2.5 text-[13px] gap-1.5',
  md: 'h-9 px-3.5 text-sm gap-2',
  lg: 'h-11 px-5 text-[15px] gap-2',
  icon: 'size-9',
  iconSm: 'size-8',
} as const

export interface ButtonProps extends ComponentProps<'button'> {
  variant?: keyof typeof variants
  size?: keyof typeof sizes
  loading?: boolean
}

export function Button({ variant = 'outline', size = 'md', loading, className, children, disabled, type = 'button', ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        'inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap rounded-sm font-medium transition-colors duration-[80ms] active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0',
        focus,
        variants[variant],
        sizes[size],
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <LoaderCircle className="animate-spin" aria-hidden />}
      {children}
    </button>
  )
}

export function Input({ className, ...rest }: ComponentProps<'input'>) {
  return (
    <input
      className={cn(
        'h-9 w-full min-w-0 rounded-sm border border-input bg-card px-3 text-sm placeholder:text-muted-foreground disabled:opacity-50 aria-[invalid=true]:border-destructive',
        'max-md:text-base',
        focus,
        className,
      )}
      {...rest}
    />
  )
}

export function Textarea({ className, ...rest }: ComponentProps<'textarea'>) {
  return <textarea className={cn('min-h-16 w-full rounded-sm border border-input bg-card px-3 py-2 text-sm max-md:text-base', focus, className)} {...rest} />
}

export function NativeSelect({ className, children, ...rest }: ComponentProps<'select'>) {
  return (
    <select
      className={cn('h-9 w-full min-w-0 rounded-sm border border-input bg-card px-2.5 text-sm max-md:text-base disabled:opacity-50', focus, className)}
      {...rest}
    >
      {children}
    </select>
  )
}

export function Field({ label, hint, error, children, className, htmlFor }: { label: ReactNode; hint?: ReactNode; error?: ReactNode; children: ReactNode; className?: string; htmlFor?: string }) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      <label htmlFor={htmlFor} className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  )
}

export function Chip({ active, className, children, ...rest }: ComponentProps<'button'> & { active?: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={cn(
        'inline-flex h-8 shrink-0 items-center gap-1.5 rounded-sm border px-2.5 text-[13px] font-medium transition-colors [&_svg]:size-3.5',
        active ? 'border-transparent bg-primary-soft text-primary' : 'border-input bg-card text-foreground hover:bg-surface-2',
        focus,
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
}

export function Badge({ tone = 'muted', className, children }: { tone?: 'muted' | 'primary' | 'warning' | 'income' | 'expense' | 'transfer'; className?: string; children: ReactNode }) {
  const tones = {
    muted: 'bg-muted text-muted-foreground',
    primary: 'bg-primary-soft text-primary',
    warning: 'bg-warning-soft text-warning',
    income: 'bg-income-soft text-income',
    expense: 'bg-expense-soft text-expense',
    transfer: 'bg-transfer-soft text-transfer',
  }
  return <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap', tones[tone], className)}>{children}</span>
}

export interface SegOption<T extends string> {
  value: T
  label: ReactNode
  title?: string
}

/** Segmented control (₫/$, Дни/Месяцы …) — DESIGN §9.7. */
export function Segmented<T extends string>({ options, value, onChange, label, className, size = 'md' }: { options: SegOption<T>[]; value: T; onChange: (v: T) => void; label: string; className?: string; size?: 'sm' | 'md' }) {
  const id = useId()
  const reduce = useReducedMotion()
  return (
    <div role="radiogroup" aria-label={label} className={cn('inline-flex shrink-0 rounded-md bg-muted p-0.5', className)}>
      {options.map((o) => {
        const on = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            title={o.title}
            onClick={() => onChange(o.value)}
            className={cn('relative rounded-sm px-2.5 font-medium transition-colors', size === 'sm' ? 'h-7 text-xs' : 'h-8 text-[13px]', on ? 'text-foreground' : 'text-muted-foreground hover:text-foreground', focus)}
          >
            {on && (
              <motion.span
                layoutId={`seg-${id}`}
                transition={reduce ? { duration: 0 } : { type: 'spring', stiffness: 520, damping: 40, mass: 0.8 }}
                className="absolute inset-0 rounded-sm bg-card shadow-xs"
              />
            )}
            <span className="relative">{o.label}</span>
          </button>
        )
      })}
    </div>
  )
}

export function Switch({ checked, onCheckedChange, label, disabled, id }: { checked: boolean; onCheckedChange: (v: boolean) => void; label: string; disabled?: boolean; id?: string }) {
  return (
    <RSwitch.Root
      id={id}
      checked={checked}
      onCheckedChange={onCheckedChange}
      disabled={disabled}
      aria-label={label}
      className={cn("relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors after:absolute after:-inset-x-1 after:-inset-y-3 after:content-[''] data-[state=checked]:bg-primary data-[state=unchecked]:bg-input disabled:opacity-50", focus)}
    >
      <RSwitch.Thumb className="block size-4 translate-x-0.5 rounded-full bg-card shadow-sm transition-transform data-[state=checked]:translate-x-[18px]" />
    </RSwitch.Root>
  )
}

export function Checkbox({ checked, onCheckedChange, label, id }: { checked: boolean; onCheckedChange: (v: boolean) => void; label?: string; id?: string }) {
  return (
    <RCheckbox.Root
      id={id}
      checked={checked}
      onCheckedChange={(v) => onCheckedChange(v === true)}
      aria-label={label}
      className={cn("relative grid size-4 shrink-0 place-items-center rounded-xs border border-input bg-card after:absolute after:-inset-1.5 after:content-[''] data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground", focus)}
    >
      <RCheckbox.Indicator>
        <Check className="size-3" strokeWidth={3} />
      </RCheckbox.Indicator>
    </RCheckbox.Root>
  )
}

export function Card({ title, actions, children, className, bodyClassName, busy, ...rest }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClassName?: string; busy?: boolean } & Omit<ComponentProps<'section'>, 'title'>) {
  return (
    <section className={cn('min-w-0 rounded-lg border border-border bg-card', className)} {...rest}>
      {(title || actions) && (
        <header className="flex min-h-12 items-center justify-between gap-2 px-4 pt-3 md:px-5">
          <h2 className="flex min-w-0 items-center gap-2 text-[15px] leading-5 font-semibold">
            <span className="truncate">{title}</span>
            {busy && <Spinner className="size-3 text-muted-foreground" label="Обновление" />}
          </h2>
          {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
        </header>
      )}
      <div className={cn('px-4 pt-2 pb-4 md:px-5 md:pb-5', !title && !actions && 'pt-4 md:pt-5', bodyClassName)}>{children}</div>
    </section>
  )
}

export function Spinner({ className, label }: { className?: string; label?: string }) {
  return <LoaderCircle className={cn('size-4 animate-spin', className)} aria-label={label} role={label ? 'status' : undefined} aria-hidden={label ? undefined : true} />
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-sm bg-muted motion-reduce:animate-none', className)} aria-hidden />
}

/** Renders skeleton children only if loading lasts > 200 ms; before that, empty space of `minH`. */
export function Loading({ loading, children, fallback, className }: { loading: boolean; children: ReactNode; fallback: ReactNode; className?: string }) {
  const show = useDelayed(loading)
  if (!loading) return <>{children}</>
  return (
    <div className={className} aria-busy="true">
      {show ? fallback : null}
    </div>
  )
}

export function EmptyState({ icon, title, text, action, className }: { icon: ReactNode; title: string; text?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cn('flex flex-col items-center gap-2 px-4 py-10 text-center', className)}>
      <div className="grid size-10 place-items-center rounded-full bg-muted text-muted-foreground [&_svg]:size-5">{icon}</div>
      <p className="text-[15px] font-semibold">{title}</p>
      {text && <p className="max-w-sm text-sm text-muted-foreground">{text}</p>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}

/** Offline and nothing loaded yet (DESIGN §8.4: honest «нет связи», never a false «пусто»). */
export function OfflineState({ what = 'Данные', onRetry, className }: { what?: string; onRetry?: () => void; className?: string }) {
  return (
    <EmptyState
      icon={<WifiOff />}
      title="Нет связи — данные не загружены"
      text={`${what} появятся, как только связь вернётся.`}
      action={onRetry ? <Button onClick={onRetry}>Повторить</Button> : undefined}
      className={className}
    />
  )
}

export function ErrorNote({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const msg = error instanceof Error ? error.message : 'Не удалось загрузить данные'
  return (
    <div role="alert" className={cn('flex flex-wrap items-center gap-2 rounded-md bg-expense-soft px-3 py-2 text-sm text-destructive', className)}>
      <span>{msg}</span>
      {onRetry && (
        <Button size="sm" variant="ghost" onClick={onRetry}>
          Повторить
        </Button>
      )}
    </div>
  )
}

export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return <kbd className={cn('rounded-xs border border-border bg-muted px-1 font-mono text-[11px] leading-4 text-muted-foreground [@media(hover:none)]:hidden', className)}>{children}</kbd>
}

export function PageHeader({ title, actions, children }: { title: string; actions?: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
      <h1 className="text-[22px] leading-7 font-semibold tracking-[-0.015em] md:text-2xl">{title}</h1>
      {children}
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

/** Page container: max-w 1200, side gutters 16/24/32 (bottom-nav space is reserved by the AppShell <main>). */
export function Page({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('mx-auto w-full max-w-[1200px] px-4 pt-4 pb-8 sm:px-6 lg:px-8 lg:pt-6', className)}>{children}</div>
}
