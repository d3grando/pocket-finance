// lucide icon registry for categories/accounts (DESIGN §4.2–4.3). A curated map keeps the bundle small
// (importing all lucide icons by name would ship ~1500 icons).
import {
  ArrowLeftRight, Banknote, Bot, BriefcaseBusiness, Building2, CarTaxiFront, ChartCandlestick, Circle, Coffee, CreditCard, Cpu,
  Gift, Handshake, HeartPulse, Landmark, Megaphone, Nfc, PackageOpen, Percent, PiggyBank, RefreshCcw, Repeat, Scale, Send, Server,
  ShoppingBag, ShoppingBasket, Smartphone, Store, Tag, Ticket, Utensils, UtensilsCrossed, Wallet, Plane, Car, Bus, Fuel, Shirt,
  Book, GraduationCap, Dumbbell, Baby, Dog, Music, Gamepad2, Film, Wifi, Zap, Droplets, Home, Hammer, Pill, Stethoscope, Scissors,
  Cat, Globe, Camera, Laptop, Headphones, Wine, Beer, Pizza, Sandwich, IceCreamCone, Bitcoin, DollarSign, Coins, Receipt, Landmark as Bank,
  HandCoins, type LucideIcon,
} from 'lucide-react'
import type { TransactionOut } from '@/lib/api'
import { cn } from '@/lib/utils'

export const ICONS: Record<string, LucideIcon> = {
  UtensilsCrossed, Utensils, ShoppingBasket, Coffee, CarTaxiFront, Building2, Smartphone, RefreshCcw, Repeat, HeartPulse, ShoppingBag,
  Ticket, Gift, Circle, Server, Bot, Cpu, Megaphone, Handshake, Percent, PackageOpen, Send, BriefcaseBusiness, Store, Banknote, Tag,
  Landmark, Bank, Wallet, ChartCandlestick, Nfc, CreditCard, PiggyBank, ArrowLeftRight, Scale, Plane, Car, Bus, Fuel, Shirt, Book,
  GraduationCap, Dumbbell, Baby, Dog, Cat, Music, Gamepad2, Film, Wifi, Zap, Droplets, Home, Hammer, Pill, Stethoscope, Scissors, Globe,
  Camera, Laptop, Headphones, Wine, Beer, Pizza, Sandwich, IceCreamCone, Bitcoin, DollarSign, Coins, Receipt, HandCoins,
}

/** Icons offered in pickers (categories / accounts). */
export const CATEGORY_ICON_CHOICES = [
  'UtensilsCrossed', 'ShoppingBasket', 'Coffee', 'CarTaxiFront', 'Building2', 'Smartphone', 'RefreshCcw', 'HeartPulse', 'ShoppingBag', 'Ticket',
  'Gift', 'Server', 'Bot', 'Megaphone', 'Handshake', 'Percent', 'PackageOpen', 'Send', 'BriefcaseBusiness', 'Store', 'Banknote', 'Tag', 'Plane',
  'Car', 'Bus', 'Fuel', 'Shirt', 'Book', 'GraduationCap', 'Dumbbell', 'Baby', 'Dog', 'Music', 'Gamepad2', 'Film', 'Wifi', 'Zap', 'Home', 'Pill',
  'Scissors', 'Globe', 'Laptop', 'Wine', 'Pizza', 'Receipt', 'Circle',
]
export const ACCOUNT_ICON_CHOICES = ['Landmark', 'Wallet', 'Bot', 'ChartCandlestick', 'Nfc', 'CreditCard', 'PiggyBank', 'Bitcoin', 'Coins', 'DollarSign', 'Banknote']

const ACCOUNT_KIND_ICON: Record<string, string> = { bank: 'Landmark', cash: 'Wallet', card: 'CreditCard', crypto_wallet: 'Bot', exchange: 'ChartCandlestick', other: 'PiggyBank' }

export function iconByName(name: string | null | undefined, fallback: LucideIcon = Tag): LucideIcon {
  return (name && ICONS[name]) || fallback
}

export function accountIcon(icon: string | null | undefined, kind?: string): LucideIcon {
  return iconByName(icon ?? (kind ? ACCOUNT_KIND_ICON[kind] : null), PiggyBank)
}

/** 28×28 rounded square; business categories get a chart-1 dot (DESIGN §4.2). */
export function IconTile({ icon: Icon, business, tone = 'muted', size = 28, className }: { icon: LucideIcon; business?: boolean; tone?: 'muted' | 'transfer' | 'income' | 'warning'; size?: number; className?: string }) {
  const tones = { muted: 'bg-muted text-muted-foreground', transfer: 'bg-transfer-soft text-transfer', income: 'bg-income-soft text-income', warning: 'bg-warning-soft text-warning' }
  return (
    <span className={cn('relative grid shrink-0 place-items-center rounded-sm', tones[tone], className)} style={{ width: size, height: size }} aria-hidden>
      <Icon className="size-4" strokeWidth={2} />
      {business && <span className="absolute -right-0.5 -bottom-0.5 size-2 rounded-full border-2 border-card bg-chart-1" />}
    </span>
  )
}

export function TxIcon({ tx, size }: { tx: TransactionOut; size?: number }) {
  if (tx.debt) return <IconTile icon={HandCoins} tone="transfer" size={size} />
  if (tx.kind === 'transfer') return <IconTile icon={ArrowLeftRight} tone="transfer" size={size} />
  if (tx.kind === 'exchange') return <IconTile icon={Repeat} tone="transfer" size={size} />
  if (tx.kind === 'adjustment') return <IconTile icon={Scale} size={size} />
  return <IconTile icon={iconByName(tx.category?.icon)} business={tx.is_business} size={size} />
}
