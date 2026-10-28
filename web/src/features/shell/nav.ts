// Navigation items (DESIGN §4.1, §10.3). Paths are the routing contract (ARCH §13).
import { ChartPie, House, ListOrdered, Plus, Settings2, WalletCards, type LucideIcon } from 'lucide-react'

export interface NavItem { to: string; label: string; short?: string; icon: LucideIcon; key: string }

export const SIDEBAR_NAV: NavItem[] = [
  { to: '/', label: 'Главная', icon: House, key: 'h' },
  { to: '/operations', label: 'Операции', icon: ListOrdered, key: 'o' },
  { to: '/analytics', label: 'Аналитика', icon: ChartPie, key: 'a' },
  { to: '/accounts', label: 'Счета', icon: WalletCards, key: 'b' },
  { to: '/settings', label: 'Настройки', icon: Settings2, key: 's' },
]

export const BOTTOM_NAV: NavItem[] = [
  { to: '/', label: 'Главная', icon: House, key: 'h' },
  { to: '/operations', label: 'Операции', icon: ListOrdered, key: 'o' },
  { to: '/add', label: 'Добавить', icon: Plus, key: 'n' },
  { to: '/analytics', label: 'Аналитика', short: 'Аналит.', icon: ChartPie, key: 'a' },
  { to: '/settings', label: 'Настройки', short: 'Настр.', icon: Settings2, key: 's' },
]

export const isActive = (pathname: string, to: string) => (to === '/' ? pathname === '/' : pathname === to || pathname.startsWith(`${to}/`))
