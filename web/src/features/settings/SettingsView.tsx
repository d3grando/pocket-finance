// «Настройки» (DESIGN §10.7): sections at /settings/<section>; desktop — side list + content,
// mobile — list of sections → section screen with «‹ Настройки».
import type { ComponentType } from 'react'
import { Link, NavLink, useParams } from 'react-router'
import { AtSign, CalendarClock, ChevronLeft, ChevronRight, Database, ScanFace, Send, Settings2, Sparkles, Store, Tags, WalletCards, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useIsMobile } from '@/features/operations/ui/hooks'
import { Page, PageHeader } from '@/features/operations/ui/kit'
import { PageToaster } from '@/features/operations/ui/overlays'
import { CategoriesSection } from './sections/Categories'
import { GeneralSection } from './sections/General'
import { AccountsSettingsSection, AiSection, DataSection } from './sections/Misc'
import { RecurringSection } from './sections/Recurring'
import { MerchantsSection, MyAccountsSection } from './sections/Rules'
import { SecuritySection } from './sections/Security'
import { TelegramSection } from './sections/Telegram'

export interface SectionDef {
  id: string
  label: string
  hint: string
  icon: LucideIcon
  Component: ComponentType
}

export const SECTIONS: SectionDef[] = [
  { id: 'general', label: 'Общие', hint: 'валюты, пояс, тема', icon: Settings2, Component: GeneralSection },
  { id: 'categories', label: 'Категории', hint: 'личные и бизнес, слияние', icon: Tags, Component: CategoriesSection },
  { id: 'accounts', label: 'Счета', hint: 'псевдонимы, по умолчанию', icon: WalletCards, Component: AccountsSettingsSection },
  { id: 'merchants', label: 'Продавцы', hint: 'правила «васт → vast.ai»', icon: Store, Component: MerchantsSection },
  { id: 'my-accounts', label: 'Мои аккаунты', hint: '@ники для переводов между своими', icon: AtSign, Component: MyAccountsSection },
  { id: 'recurring', label: 'Регулярные платежи', hint: 'напоминания', icon: CalendarClock, Component: RecurringSection },
  { id: 'telegram', label: 'Telegram', hint: 'отчёты, напоминания, бот', icon: Send, Component: TelegramSection },
  { id: 'security', label: 'Вход и устройства', hint: 'Face ID, пароль, сессии', icon: ScanFace, Component: SecuritySection },
  { id: 'ai', label: 'AI', hint: 'провайдер, модель, курсы', icon: Sparkles, Component: AiSection },
  { id: 'data', label: 'Данные', hint: 'экспорт и удаление', icon: Database, Component: DataSection },
]

export function SettingsView() {
  const params = useParams()
  const raw = (params['*'] ?? '').split('/')[0]
  const mobile = useIsMobile()
  const current = SECTIONS.find((s) => s.id === raw) ?? (mobile ? null : SECTIONS[0])

  if (mobile && !current) {
    return (
      <Page>
        <PageToaster />
        <PageHeader title="Настройки" />
        <nav aria-label="Разделы настроек" className="mt-4 overflow-hidden rounded-lg border border-border bg-card">
          {SECTIONS.map((s) => (
            <Link key={s.id} to={`/settings/${s.id}`} className="flex min-h-14 items-center gap-3 border-b border-border px-4 last:border-0 active:bg-surface-2">
              <s.icon className="size-[18px] text-muted-foreground" strokeWidth={1.75} />
              <span className="min-w-0 flex-1">
                <span className="block text-[15px] font-medium">{s.label}</span>
                <span className="block truncate text-[13px] text-muted-foreground">{s.hint}</span>
              </span>
              <ChevronRight className="size-4 text-muted-foreground" />
            </Link>
          ))}
        </nav>
      </Page>
    )
  }

  const Section = current!.Component
  return (
    <Page>
      <PageToaster />
      {mobile ? (
        <div className="mb-3">
          <Link to="/settings" className="-ml-2 inline-flex h-10 items-center gap-1 rounded-sm px-2 text-[15px] font-medium text-primary">
            <ChevronLeft className="size-5" /> Настройки
          </Link>
        </div>
      ) : (
        <PageHeader title="Настройки" />
      )}
      <div className={cn('grid gap-4', !mobile && 'mt-4 grid-cols-[220px_minmax(0,1fr)] gap-6')}>
        {!mobile && (
          <nav aria-label="Разделы настроек" className="sticky top-4 self-start rounded-lg border border-border bg-card p-1.5">
            {SECTIONS.map((s) => (
              <NavLink
                key={s.id}
                to={`/settings/${s.id}`}
                className={() =>
                  cn('flex h-9 items-center gap-2.5 rounded-sm px-2.5 text-sm transition-colors', s.id === current!.id ? 'bg-accent font-medium text-foreground' : 'text-muted-foreground hover:bg-surface-2 hover:text-foreground')
                }
                aria-current={s.id === current!.id ? 'page' : undefined}
              >
                <s.icon className="size-4" strokeWidth={1.75} />
                {s.label}
              </NavLink>
            ))}
          </nav>
        )}
        <div className="min-w-0">
          <Section />
        </div>
      </div>
    </Page>
  )
}
