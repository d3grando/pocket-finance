// Small shared hooks for the pages.
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { newIdempotencyKey } from '@/lib/api'

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (cb) => {
      if (typeof window === 'undefined' || !window.matchMedia) return () => {}
      const mq = window.matchMedia(query)
      mq.addEventListener?.('change', cb)
      return () => mq.removeEventListener?.('change', cb)
    },
    () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : false),
    () => false,
  )
}

/** < 768 px — mobile layout (DESIGN §5.4). */
export const useIsMobile = () => useMediaQuery('(max-width: 767px)')
export const useReducedMotion = () => useMediaQuery('(prefers-reduced-motion: reduce)')

export function useOnline(): boolean {
  return useSyncExternalStore(
    (cb) => {
      window.addEventListener('online', cb)
      window.addEventListener('offline', cb)
      return () => {
        window.removeEventListener('online', cb)
        window.removeEventListener('offline', cb)
      }
    },
    () => (typeof navigator === 'undefined' ? true : navigator.onLine !== false),
    () => true,
  )
}

/** true after `ms` while `active` stays true — skeletons only for slow responses (DESIGN §8.1). */
export function useDelayed(active: boolean, ms = 200): boolean {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    if (!active) return
    const t = setTimeout(() => setShown(true), ms)
    return () => {
      clearTimeout(t)
      setShown(false)
    }
  }, [active, ms])
  return active && shown
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

/** Safe localStorage-backed preference (per-device UI convenience only). */
export function useLocalPref<T extends string>(key: string, fallback: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      return (localStorage.getItem(key) as T | null) ?? fallback
    } catch {
      return fallback
    }
  })
  const set = (nv: T) => {
    setV(nv)
    try {
      localStorage.setItem(key, nv)
    } catch {
      /* private mode */
    }
  }
  return [v, set]
}

/** Is focus in a text field (global hotkeys must not fire then). */
export function isTypingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null
  if (!el) return false
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable
}

/** Stable idempotency key per user attempt: same key on retry, new key after success/reset. */
export function useAttemptKey(): { key: () => string; reset: () => void } {
  const ref = useRef<string | null>(null)
  return {
    key: () => (ref.current ??= newIdempotencyKey()),
    reset: () => {
      ref.current = null
    },
  }
}
