// «Добавить» (bottom nav, DESIGN §9.7): manual form without AI. Accepts ?amount=&currency=&text=.
import { useNavigate, useSearchParams } from 'react-router'
import { Page, PageHeader } from '@/components/pf/Page'
import { ManualTxForm } from '@/features/manual/ManualTxForm'

export default function AddPage() {
  const [params] = useSearchParams()
  const nav = useNavigate()
  const prefill = {
    amount: params.get('amount') ?? undefined,
    currency: params.get('currency') ?? undefined,
    text: params.get('text') ?? undefined,
    kind: (params.get('kind') as 'expense' | 'income' | 'transfer' | null) ?? undefined,
  }
  return (
    <Page className="max-w-[560px] pb-8">
      <PageHeader title="Новая операция" />
      <p className="mb-4 text-small text-muted-foreground">Без AI: сумма, категория и счёт. Словами быстрее — на главной.</p>
      <ManualTxForm prefill={prefill} onSaved={() => nav('/')} />
    </Page>
  )
}
