// Global keys (DESIGN §11). Ignored while typing in a field, except Ctrl+K and Esc.
import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router'
import { focusAiInput } from '@/features/assistant/aiFocus'
import { SIDEBAR_NAV } from './nav'

interface Handlers {
  onPalette: () => void
  onManual: () => void
  onToggleSidebar: () => void
  onShortcuts: () => void
}

export function isTypingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null
  if (!el || !el.tagName) return false
  return el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)
}

export function useGlobalHotkeys(h: Handlers) {
  const nav = useNavigate()
  const gPending = useRef<number | null>(null)
  const hr = useRef(h)
  useEffect(() => { hr.current = h })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return
      const mod = e.ctrlKey || e.metaKey
      if (mod && !e.shiftKey && !e.altKey && (e.key === 'k' || e.key === 'K' || e.key === 'л' || e.key === 'Л')) {
        e.preventDefault()
        if (!focusAiInput()) hr.current.onPalette()
        return
      }
      if (mod || e.altKey || isTypingTarget(e.target)) return
      if (document.querySelector('[role="dialog"][data-state="open"]')) return
      const k = e.key.toLowerCase()
      if (gPending.current !== null) {
        window.clearTimeout(gPending.current)
        gPending.current = null
        const map: Record<string, string> = { h: 'h', р: 'h', o: 'o', щ: 'o', a: 'a', ф: 'a', b: 'b', и: 'b', s: 's', ы: 's' }
        const item = SIDEBAR_NAV.find((n) => n.key === map[k])
        if (item) { e.preventDefault(); nav(item.to) }
        return
      }
      if (k === 'g' || k === 'п') {
        gPending.current = window.setTimeout(() => { gPending.current = null }, 1200)
        return
      }
      if (k === 'n' || k === 'т') {
        // «Операции» has its own manual form behind «Новая» (and its own N) — one form per page
        if (window.location.pathname.startsWith('/operations')) return
        e.preventDefault(); hr.current.onManual(); return
      }
      if (e.key === '[' || e.key === 'х') { e.preventDefault(); hr.current.onToggleSidebar(); return }
      if (e.key === '?') { e.preventDefault(); hr.current.onShortcuts(); return }
      if (e.key === '/' && !window.location.pathname.startsWith('/operations')) {
        e.preventDefault()
        nav('/operations?focus=search')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [nav])
}
