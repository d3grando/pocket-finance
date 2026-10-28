// Category/account icons (DESIGN §4.2–4.3). Backend sends lucide names in `icon`;
// unknown names fall back by category name, then to Tag. Explicit imports keep the bundle small.
import {
  ArrowLeftRight, Banknote, Bot, BriefcaseBusiness, Building, CarTaxiFront, ChartCandlestick, Circle, Coffee, Cpu,
  CreditCard, Gift, HandCoins, Handshake, HeartPulse, Landmark, Megaphone, Nfc, PackageOpen, Percent, PiggyBank, RefreshCcw,
  Repeat, Scale, Send, Server, ShoppingBag, ShoppingBasket, Smartphone, Store, Tag, Ticket, Utensils,
  UtensilsCrossed, Wallet, type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { CategoryRef, TransactionOut } from '@/lib/api/types'

const BY_NAME: Record<string, LucideIcon> = {
  UtensilsCrossed, Utensils, ShoppingBasket, Coffee, CarTaxiFront, Building, Building2: Building, Smartphone,
  RefreshCcw, Repeat, HeartPulse, ShoppingBag, Ticket, Gift, Circle, Server, Bot, Cpu, Megaphone, Handshake,
  Percent, PackageOpen, Send, BriefcaseBusiness, Store, Banknote, Tag, Landmark, Wallet, ChartCandlestick, Nfc,
  CreditCard, PiggyBank, ArrowLeftRight, Scale, HandCoins,
}

const BY_CATEGORY: Record<string, LucideIcon> = {
  'еда': UtensilsCrossed, 'продукты': ShoppingBasket, 'кофе': Coffee, 'такси/транспорт': CarTaxiFront,
  'жильё': Building, 'связь': Smartphone, 'подписки': RefreshCcw, 'здоровье': HeartPulse, 'покупки': ShoppingBag,
  'развлечения': Ticket, 'подарки': Gift, 'прочее': Circle, 'серверы/инфра': Server, 'софт и ai': Bot,
  'реклама': Megaphone, 'подрядчики': Handshake, 'комиссии/обмен': Percent, 'закупка товара': PackageOpen,
  'переводы людям': Send, 'клиенты': BriefcaseBusiness, 'продажи': Store, 'продажи (бизнес)': Store, 'зарплата': Banknote,
}

const BY_ACCOUNT: Record<string, LucideIcon> = {
  'карта': Landmark, 'наличные': Wallet, 'кошелёк': Wallet, 'копилка': Bot, 'биржа': ChartCandlestick, 'сейф': Nfc, 'карта rub': CreditCard,
}

const pascal = (s: string) => s.replace(/(^|[-_ ])(\w)/g, (_, __, c: string) => c.toUpperCase())

export function iconByName(name: string | null | undefined): LucideIcon | null {
  if (!name) return null
  return BY_NAME[name] ?? BY_NAME[pascal(name)] ?? null
}

export function categoryIcon(c: Pick<CategoryRef, 'icon' | 'name'> | null | undefined): LucideIcon {
  if (!c) return Tag
  return iconByName(c.icon) ?? BY_CATEGORY[c.name.toLowerCase()] ?? Tag
}

export function accountIcon(a: { icon: string | null; name: string } | null | undefined): LucideIcon {
  if (!a) return PiggyBank
  return iconByName(a.icon) ?? BY_ACCOUNT[a.name.toLowerCase()] ?? PiggyBank
}

export function txIcon(tx: TransactionOut): LucideIcon {
  if (tx.debt) return HandCoins
  if (tx.kind === 'transfer') return ArrowLeftRight
  if (tx.kind === 'exchange') return Repeat
  if (tx.kind === 'adjustment') return Scale
  return categoryIcon(tx.category)
}

/** 28×28 rounded square with the category icon; business categories get a chart-1 dot (DESIGN §4.2). */
export function IconTile({ icon: Icon, business, size = 28, className }: { icon: LucideIcon; business?: boolean; size?: number; className?: string }) {
  return (
    <span
      className={cn('relative inline-flex shrink-0 items-center justify-center rounded-sm bg-muted text-muted-foreground', className)}
      style={{ width: size, height: size }}
      aria-hidden
    >
      <Icon className="size-4" strokeWidth={2} />
      {business && (
        <span className="absolute -right-0.5 -bottom-0.5 size-2 rounded-full bg-chart-1 ring-2 ring-card" />
      )}
    </span>
  )
}
