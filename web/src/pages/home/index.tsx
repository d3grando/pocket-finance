// Главная (DESIGN §10.1 desktop, §10.2 mobile): AI line on top (desktop) / in the
// middle (mobile), KPI tiles in ₫, dynamics + categories, recent operations + known balances.
import { useNavigate } from 'react-router'
import { Page, PageHeader } from '@/components/pf/Page'
import { AiCommandBar, DEFAULT_SUGGESTIONS } from '@/features/assistant/AiCommandBar'
import { CardStack } from '@/features/assistant/CardStack'
import { BalancesCard } from '@/features/dashboard/BalancesCard'
import { CategoryDonut } from '@/features/dashboard/CategoryDonut'
import { DynamicsChart } from '@/features/dashboard/DynamicsChart'
import { KpiTiles } from '@/features/dashboard/KpiTiles'
import { PasskeyOffer } from '@/features/dashboard/PasskeyOffer'
import { RecentTransactions } from '@/features/dashboard/RecentTransactions'
import { WeekBars } from '@/features/dashboard/WeekBars'
import { DebtsHomeCard } from '@/features/debts/DebtsHomeCard'
import { NeedsAccountBadge } from '@/features/shell/NeedsAccountBadge'
import { ThemeToggle } from '@/features/shell/ThemeToggle'
import { useIsMobile } from '@/hooks/useMediaQuery'
import { useSettings, useSummary } from '@/lib/api/hooks'
import type { StatsCurrency } from '@/lib/api/types'

export default function HomePage() {
  const mobile = useIsMobile()
  const nav = useNavigate()
  const settings = useSettings()
  const currency = (settings.data?.summary_currency === 'USD' ? 'USD' : 'VND') as StatsCurrency
  const tz = settings.data?.timezone
  const summary = useSummary(currency)
  const openTx = (id: number) => nav(`/operations?tx=${id}`)

  if (mobile) {
    return (
      <Page className="flex flex-col gap-6 pt-3">
        <KpiTiles summary={summary.data} loading={summary.isPending} mobile />
        <div className="flex flex-col gap-2">
          <AiCommandBar variant="mobile" suggestions={DEFAULT_SUGGESTIONS} />
          <CardStack onOpenTx={openTx} />
        </div>
        <PasskeyOffer />
        <RecentTransactions limit={5} tz={tz} spentToday={summary.data?.spent_today} title="Последние" />
        <DebtsHomeCard />
        <WeekBars currency={currency} />
        <CategoryDonut currency={currency} />
        <BalancesCard />
      </Page>
    )
  }

  return (
    <Page className="flex flex-col gap-8 pb-8">
      <PageHeader
        title="Главная"
        actions={<div className="flex items-center gap-2"><NeedsAccountBadge count={summary.data?.needs_account_count} /><ThemeToggle /></div>}
      />
      <div className="-mt-4 flex flex-col gap-3">
        <AiCommandBar variant="home" autoFocus suggestions={DEFAULT_SUGGESTIONS} />
        <CardStack onOpenTx={openTx} />
      </div>
      <PasskeyOffer />
      <KpiTiles summary={summary.data} loading={summary.isPending} />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <DynamicsChart currency={currency} tz={tz} />
        <CategoryDonut currency={currency} />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <RecentTransactions className="lg:col-span-8" tz={tz} spentToday={summary.data?.spent_today} />
        <div className="flex flex-col gap-4 lg:col-span-4">
          <BalancesCard />
          <DebtsHomeCard />
        </div>
      </div>
    </Page>
  )
}
