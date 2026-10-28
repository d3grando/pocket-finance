// Theme: class `.dark` on <html> (shadcn). «Как в системе» by default; the choice is
// kept in localStorage `pf.theme` and mirrored to settings.theme by the settings page (pages).
// The same logic runs inline in index.html before first paint (no flash).
import { useSyncExternalStore } from 'react'
import { readStore, STORE, writeStore } from './storage'

export type ThemeChoice = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

const listeners = new Set<() => void>()
const mq = () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null)

export function getThemeChoice(): ThemeChoice {
  const v = readStore(STORE.theme)
  return v === 'light' || v === 'dark' ? v : 'system'
}

export function resolveTheme(choice: ThemeChoice = getThemeChoice()): ResolvedTheme {
  if (choice !== 'system') return choice
  return mq()?.matches ? 'dark' : 'light'
}

/** Browser/status-bar colour per resolved theme (DESIGN §13.5: manifest theme_color, dark meta). */
export const THEME_COLOR: Record<ResolvedTheme, string> = { light: '#5d4ed6', dark: '#0d0e12' }

/** Point every theme-color meta at the resolved theme, so a manual switch also recolours the bar. */
export function applyThemeColor(resolved: ResolvedTheme) {
  if (typeof document === 'undefined') return
  document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((m) => { m.content = THEME_COLOR[resolved] })
}

export function applyTheme(choice: ThemeChoice = getThemeChoice()) {
  const resolved = resolveTheme(choice)
  const root = document.documentElement
  root.classList.toggle('dark', resolved === 'dark')
  root.style.colorScheme = resolved
  applyThemeColor(resolved)
  listeners.forEach((l) => l())
}

export function setThemeChoice(choice: ThemeChoice) {
  writeStore(STORE.theme, choice === 'system' ? null : choice)
  applyTheme(choice)
}

let watching = false
export function watchSystemTheme() {
  if (watching) return
  watching = true
  mq()?.addEventListener?.('change', () => { if (getThemeChoice() === 'system') applyTheme('system') })
  window.addEventListener('storage', (e) => { if (e.key === STORE.theme) applyTheme() })
}

const subscribe = (l: () => void) => { listeners.add(l); return () => listeners.delete(l) }
let snap = ''
const getSnap = () => {
  const next = `${getThemeChoice()}|${resolveTheme()}`
  if (next !== snap) snap = next
  return snap
}

export function useTheme(): { choice: ThemeChoice; resolved: ResolvedTheme; setChoice: (c: ThemeChoice) => void; toggle: () => void } {
  const s = useSyncExternalStore(subscribe, getSnap, getSnap)
  const [choice, resolved] = s.split('|') as [ThemeChoice, ResolvedTheme]
  return {
    choice,
    resolved,
    setChoice: setThemeChoice,
    toggle: () => setThemeChoice(resolved === 'dark' ? 'light' : 'dark'),
  }
}
