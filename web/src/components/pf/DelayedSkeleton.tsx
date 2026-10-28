// Skeleton shown only if data has not arrived within 200 ms (DESIGN §8.1); before that an
// empty box of the same height, so fast answers never flash.
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'

export function useDelayed(active: boolean, ms = 200): boolean {
  const [show, setShow] = useState(false)
  useEffect(() => {
    if (!active) return
    const t = setTimeout(() => setShow(true), ms)
    return () => { clearTimeout(t); setShow(false) }
  }, [active, ms])
  return active && show
}

export function Bar({ className }: { className?: string }) {
  return <span className={cn('pf-pulse block rounded-xs bg-muted', className)} />
}

export function DelayedSkeleton({ loading, children, className }: { loading: boolean; children: React.ReactNode; className?: string }) {
  const show = useDelayed(loading)
  return <div className={className} aria-busy={loading || undefined}>{show ? children : null}</div>
}
