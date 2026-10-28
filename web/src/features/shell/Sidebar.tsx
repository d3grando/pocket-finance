// Desktop sidebar 232 px, collapses to 64 (key «[»); tablet = icons only (DESIGN §5.4, §10.1).
import { NavLink, useLocation } from 'react-router'
import { LogOut, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { ConnectionDot } from '@/components/pf/Connection'
import { useSignOut } from '@/features/auth/useSignOut'
import { Logo, LogoMark } from '@/components/pf/Logo'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { SIDEBAR_NAV, isActive } from './nav'

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const { pathname } = useLocation()
  const { signOut, busy } = useSignOut()
  return (
    <aside
      className={cn(
        'sticky top-0 hidden h-dvh shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground md:flex',
        collapsed ? 'w-16' : 'w-16 lg:w-[232px]',
      )}
      data-testid="sidebar"
    >
      <div className={cn('flex h-14 items-center gap-2 px-4', collapsed ? 'justify-center px-0' : 'justify-center lg:justify-start')}>
        <span className={cn(collapsed ? 'flex' : 'flex lg:hidden')}><LogoMark size={24} /></span>
        <span className={cn('items-center gap-2', collapsed ? 'hidden' : 'hidden lg:flex')}>
          <Logo size={24} />
          <ConnectionDot />
        </span>
      </div>
      <nav className="flex flex-1 flex-col gap-0.5 px-2 py-2" aria-label="Разделы">
        {SIDEBAR_NAV.map((item) => {
          const active = isActive(pathname, item.to)
          const link = (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={cn(
                'flex h-9 items-center gap-3 rounded-sm px-3 text-body transition-colors focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none',
                active ? 'bg-sidebar-accent font-medium text-foreground' : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground',
                collapsed ? 'justify-center px-0' : 'justify-center px-0 lg:justify-start lg:px-3',
              )}
              aria-current={active ? 'page' : undefined}
            >
              <item.icon className={cn('size-[18px] shrink-0', active ? 'text-foreground' : 'text-muted-foreground')} strokeWidth={1.75} />
              <span className={cn(collapsed ? 'sr-only' : 'sr-only lg:not-sr-only')}>{item.label}</span>
            </NavLink>
          )
          return (
            <Tooltip key={item.to}>
              <TooltipTrigger asChild>{link}</TooltipTrigger>
              <TooltipContent side="right" className={cn(!collapsed && 'lg:hidden')}>{item.label}</TooltipContent>
            </Tooltip>
          )
        })}
      </nav>
      <div className="flex flex-col gap-1 border-t px-2 py-3">
        <div className={cn('flex px-3', collapsed ? 'justify-center px-0' : 'justify-center px-0 lg:justify-start lg:px-3')}>
          <ConnectionDot withLabel={!collapsed} className={cn(!collapsed && '[&>span:last-child]:hidden lg:[&>span:last-child]:inline')} />
        </div>
        <button
          type="button"
          onClick={onToggle}
          className={cn('hidden h-8 items-center gap-2 rounded-sm px-3 text-small text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground lg:flex', collapsed && 'justify-center px-0')}
          aria-label={collapsed ? 'Развернуть панель' : 'Свернуть панель'}
          title="["
        >
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
          {!collapsed && 'Свернуть'}
        </button>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => void signOut()}
              disabled={busy}
              className={cn('flex h-8 items-center gap-2 rounded-sm px-3 text-small text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground disabled:opacity-50', collapsed ? 'justify-center px-0' : 'justify-center px-0 lg:justify-start lg:px-3')}
              aria-label="Выйти"
              data-testid="sidebar-logout"
            >
              <LogOut className="size-4" />
              <span className={cn(collapsed ? 'sr-only' : 'sr-only lg:not-sr-only')}>Выйти</span>
            </button>
          </TooltipTrigger>
          <TooltipContent side="right" className={cn(!collapsed && 'lg:hidden')}>Выйти</TooltipContent>
        </Tooltip>
      </div>
    </aside>
  )
}
