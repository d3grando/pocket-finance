// Media-query hook (breakpoints from DESIGN §5.4).
import { useSyncExternalStore } from 'react'

export function useMediaQuery(query: string, fallback = false): boolean {
  return useSyncExternalStore(
    (cb) => {
      if (typeof window === 'undefined' || !window.matchMedia) return () => {}
      const m = window.matchMedia(query)
      m.addEventListener?.('change', cb)
      return () => m.removeEventListener?.('change', cb)
    },
    () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : fallback),
    () => fallback,
  )
}

export const useIsMobile = () => !useMediaQuery('(min-width: 768px)', true)
export const useIsDesktop = () => useMediaQuery('(min-width: 1024px)', true)
export const useReducedMotion = () => useMediaQuery('(prefers-reduced-motion: reduce)')
