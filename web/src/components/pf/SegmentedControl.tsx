// Segmented control (DESIGN §9.7): bg-muted rounded-md p-0.5, active bg-card shadow-xs.
import { cn } from '@/lib/utils'

interface Props<V extends string> {
  value: V
  onChange: (v: V) => void
  options: { value: V; label: React.ReactNode; title?: string }[]
  className?: string
  size?: 'sm' | 'md'
  'aria-label'?: string
}

export function SegmentedControl<V extends string>({ value, onChange, options, className, size = 'sm', ...rest }: Props<V>) {
  return (
    <div role="radiogroup" aria-label={rest['aria-label']} className={cn('inline-flex rounded-md bg-muted p-0.5', className)}>
      {options.map((o) => {
        const active = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={o.title}
            onClick={() => onChange(o.value)}
            className={cn(
              'rounded-sm px-2.5 font-medium transition-[background-color,color,box-shadow] duration-(--dur-fast) focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none',
              size === 'sm' ? 'h-7 text-caption' : 'h-8 text-small',
              active ? 'bg-card text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
