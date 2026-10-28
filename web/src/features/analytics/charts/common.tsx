// Chart card chrome + shared Recharts bits (DESIGN §9.6): «Таблица» toggle, aria summary, empty state,
// tooltip, first-render-only animation (never with reduced motion).
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ChartColumn, Table2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button, Card, Skeleton } from '@/features/operations/ui/kit'
import { useDelayed, useReducedMotion } from '@/features/operations/ui/hooks'

export function ChartCard({ title, summary, table, children, actions, loading, empty, className, busy, height = 240 }: { title: ReactNode; summary: string; table: ReactNode; children: ReactNode; actions?: ReactNode; loading?: boolean; empty?: boolean; className?: string; busy?: boolean; height?: number }) {
  const [asTable, setAsTable] = useState(false)
  const showSkel = useDelayed(!!loading)
  return (
    <Card
      title={title}
      busy={busy}
      className={className}
      actions={
        <>
          {actions}
          <Button size="sm" variant="ghost" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)} title={asTable ? 'Показать график' : 'Показать таблицей'}>
            {asTable ? <ChartColumn /> : <Table2 />}
            <span className="max-sm:sr-only">{asTable ? 'График' : 'Таблица'}</span>
          </Button>
        </>
      }
    >
      {loading ? (
        <div style={{ height }} aria-busy="true">
          {showSkel && <Skeleton className="h-full w-full" />}
        </div>
      ) : empty ? (
        <div className="grid place-items-center rounded-md border border-dashed border-border text-sm text-muted-foreground" style={{ height }}>
          Нет данных за период
        </div>
      ) : asTable ? (
        <div className="overflow-x-auto">{table}</div>
      ) : (
        <figure role="img" aria-label={summary} className="m-0">
          {children}
          <figcaption className="sr-only">{summary}</figcaption>
        </figure>
      )}
    </Card>
  )
}

/** Animate only on the very first data render. */
export function useFirstAnimation(ready: boolean): boolean {
  const reduce = useReducedMotion()
  const done = useRef(false)
  const [animate, setAnimate] = useState(true)
  useEffect(() => {
    if (ready && !done.current) {
      done.current = true
      const t = setTimeout(() => setAnimate(false), 400)
      return () => clearTimeout(t)
    }
  }, [ready])
  return !reduce && animate
}

export function ChartTooltip({ active, title, rows }: { active?: boolean; title?: ReactNode; rows: { color?: string; label: string; value: string; muted?: boolean }[] }) {
  if (!active) return null
  return (
    <div className="num rounded-md border border-border bg-popover p-2 text-[13px] shadow-md">
      {title && <div className="mb-1 font-medium">{title}</div>}
      {rows.map((r) => (
        <div key={r.label} className={cn('flex items-center gap-2', r.muted && 'text-muted-foreground')}>
          {r.color && <span className="size-2 rounded-full" style={{ background: r.color }} />}
          <span>{r.label}</span>
          <span className="ml-auto pl-3 font-medium">{r.value}</span>
        </div>
      ))}
    </div>
  )
}

export function DataTable({ head, rows, align }: { head: string[]; rows: ReactNode[][]; align?: ('left' | 'right')[] }) {
  return (
    <table className="num w-full text-[13px]">
      <thead>
        <tr className="border-b border-border text-muted-foreground">
          {head.map((h, i) => (
            <th key={h} className={cn('py-1.5 font-medium', (align?.[i] ?? (i === 0 ? 'left' : 'right')) === 'right' ? 'text-right' : 'text-left')}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b border-border/60 last:border-0">
            {r.map((c, j) => (
              <td key={j} className={cn('py-1.5', (align?.[j] ?? (j === 0 ? 'left' : 'right')) === 'right' ? 'text-right' : 'text-left')}>
                {c}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export const axisProps = {
  tick: { fontSize: 12, fill: 'var(--muted-foreground)' },
  tickLine: false,
  axisLine: false,
} as const
