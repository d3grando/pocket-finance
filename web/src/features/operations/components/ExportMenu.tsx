// «Экспорт ▾»: CSV (текущий фильтр), XLSX (текущий фильтр), Всё в XLSX (DESIGN §10.4).
import { useState } from 'react'
import { Download, FileSpreadsheet, FileText } from 'lucide-react'
import type { TransactionFilter } from '@/lib/api'
import { downloadFile, errorMessage } from '../api'
import { apiQuery } from '../lib/filters'
import { Button, Spinner } from '../ui/kit'
import { Menu, notify } from '../ui/overlays'

export function exportQuery(filter: TransactionFilter, format: 'csv' | 'xlsx') {
  const q = apiQuery({ ...filter, limit: undefined, offset: undefined })
  return { ...q, format }
}

export function ExportMenu({ filter, compact }: { filter: TransactionFilter; compact?: boolean }) {
  const [busy, setBusy] = useState(false)
  const run = async (q: Record<string, unknown>, label: string) => {
    setBusy(true)
    try {
      await downloadFile('/export/transactions', q, `pocket-transactions.${q.format}`)
      notify.info(`Экспорт готов: ${label}`)
    } catch (e) {
      notify.error(errorMessage(e, 'Не удалось выгрузить файл'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Menu
      label="Экспорт"
      items={[
        { label: 'CSV (текущий фильтр)', icon: <FileText />, onSelect: () => void run(exportQuery(filter, 'csv'), 'CSV') },
        { label: 'XLSX (текущий фильтр)', icon: <FileSpreadsheet />, onSelect: () => void run(exportQuery(filter, 'xlsx'), 'XLSX') },
        'sep',
        { label: 'Всё в XLSX', icon: <FileSpreadsheet />, onSelect: () => void run({ format: 'xlsx' }, 'все операции') },
      ]}
      trigger={
        <Button variant="outline" size={compact ? 'icon' : 'md'} aria-label="Экспорт" disabled={busy}>
          {busy ? <Spinner /> : <Download />}
          {!compact && 'Экспорт'}
        </Button>
      }
    />
  )
}
