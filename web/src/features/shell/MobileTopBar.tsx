// Mobile top bar 52 px + safe-area; translucent material after 4 px of scroll (DESIGN §6, §10.2).
import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router'
import { LogOut, MoreHorizontal } from 'lucide-react'
import { ConnectionDot } from '@/components/pf/Connection'
import { Logo } from '@/components/pf/Logo'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { useSignOut } from '@/features/auth/useSignOut'
import { cn } from '@/lib/utils'
import { NeedsAccountBadge } from './NeedsAccountBadge'
import { ThemeToggle } from './ThemeToggle'

export function MobileTopBar() {
  const { pathname } = useLocation()
  const [scrolled, setScrolled] = useState(false)
  const { signOut, busy } = useSignOut()
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 4)
    on()
    window.addEventListener('scroll', on, { passive: true })
    return () => window.removeEventListener('scroll', on)
  }, [])
  return (
    <header
      className={cn(
        'sticky top-0 z-10 border-b pt-safe transition-colors md:hidden',
        scrolled ? 'material-bar border-border' : 'border-transparent bg-background',
      )}
      data-testid="mobile-topbar"
    >
      <div className="flex h-[52px] items-center gap-2 px-4">
        <Link to="/" aria-label="Pocket — главная"><Logo size={22} /></Link>
        <ConnectionDot />
        <span className="flex-1" />
        {/* «Операции» shows its own «Разобрать: N» in the page header — one badge per screen */}
        {!pathname.startsWith('/operations') && <NeedsAccountBadge />}
        <ThemeToggle />
        {/* «Выйти» sits behind a menu: a stray tap next to the theme toggle must not end the session */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button type="button" variant="ghost" size="icon-sm" aria-label="Меню" data-testid="mobile-menu">
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuItem disabled={busy} onSelect={() => void signOut()} data-testid="mobile-logout"><LogOut /> Выйти</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  )
}
