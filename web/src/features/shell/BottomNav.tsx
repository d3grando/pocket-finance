// Mobile bottom nav (DESIGN §10.3): 5 items, «Добавить» = 44 px primary circle:
// tap → bottom sheet with the AI line focused (+ «Заполнить вручную»), long tap (500 ms) → the manual
// form (/add). 64 px + safe-area, material bg-card/85; hidden while the keyboard is open.
import { useEffect, useRef, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router'
import { cn } from '@/lib/utils'
import { BOTTOM_NAV, isActive, type NavItem } from './nav'
import { useShell } from './ShellContext'

function useKeyboardOpen(): boolean {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const vv = window.visualViewport
    if (!vv) return
    const on = () => setOpen(window.innerHeight - vv.height > 150)
    vv.addEventListener('resize', on)
    return () => vv.removeEventListener('resize', on)
  }, [])
  return open
}

const LONG_PRESS_MS = 500

/** «+»: tap opens the AI sheet, a long press goes straight to the manual form. */
function AddButton({ item }: { item: NavItem }) {
  const { openAdd } = useShell()
  const nav = useNavigate()
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const long = useRef(false)
  const clear = () => { if (timer.current) { clearTimeout(timer.current); timer.current = null } }
  useEffect(() => clear, [])
  return (
    <button
      type="button"
      aria-label={item.label}
      title="Нажмите — записать словами, удерживайте — вручную"
      data-testid="nav-add"
      className="relative flex flex-1 flex-col items-center justify-center gap-0.5 select-none [-webkit-touch-callout:none] focus-visible:outline-none"
      onPointerDown={() => {
        long.current = false
        clear()
        timer.current = setTimeout(() => { long.current = true; timer.current = null; nav('/add') }, LONG_PRESS_MS)
      }}
      onPointerUp={clear}
      onPointerLeave={clear}
      onPointerCancel={clear}
      onContextMenu={(e) => e.preventDefault()}
      onClick={() => { if (long.current) { long.current = false; return } openAdd() }}
    >
      <span className="flex size-11 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-md transition-transform active:scale-95">
        <item.icon className="size-[22px]" strokeWidth={2} />
      </span>
    </button>
  )
}

export function BottomNav() {
  const { pathname } = useLocation()
  const kb = useKeyboardOpen()
  if (kb) return null
  return (
    <nav
      className="material-nav fixed inset-x-0 bottom-0 z-20 border-t pb-safe md:hidden"
      aria-label="Разделы"
      data-testid="bottom-nav"
    >
      <ul className="grid h-16 grid-cols-5">
        {BOTTOM_NAV.map((item) => {
          const active = isActive(pathname, item.to)
          if (item.to === '/add') return <li key={item.to} className="flex"><AddButton item={item} /></li>
          return (
            <li key={item.to} className="flex">
              <NavLink
                to={item.to}
                end={item.to === '/'}
                // the accessible name starts with the visible text (Lighthouse label-content-name-mismatch)
                aria-label={item.short && item.short !== item.label ? undefined : item.label}
                title={item.label}
                aria-current={active ? 'page' : undefined}
                className="relative flex flex-1 flex-col items-center justify-center gap-0.5 focus-visible:outline-none"
              >
                {active && <span className="absolute top-1.5 h-0.5 w-4 rounded-full bg-primary" />}
                <item.icon className={cn('size-[22px]', active ? 'text-foreground' : 'text-muted-foreground')} strokeWidth={1.75} />
                <span className={cn('text-micro', active ? 'text-foreground' : 'text-muted-foreground')}>{item.short ?? item.label}</span>
              </NavLink>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
