// Settings → AI (read-only server config), → Счета (aliases/defaults), → Данные (export, wipe).
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { Download, FileJson, FileSpreadsheet, FileText, RefreshCw, TriangleAlert } from 'lucide-react'
import { api, ApiError, type HealthOut, type RatesLatestOut } from '@/lib/api'
import { downloadFile, errorMessage, useAccounts } from '@/features/operations/api'
import { AliasEditor, DefaultCurrencies } from '@/features/accounts/editors'
import { accountIcon, IconTile } from '@/features/operations/ui/icons'
import { Badge, Button, Field, Input, Skeleton } from '@/features/operations/ui/kit'
import { notify } from '@/features/operations/ui/overlays'
import { Row, SettingsSection } from '../ui'
import { useServerInfo } from './Telegram'

export function AiSection() {
  const qc = useQueryClient()
  const server = useServerInfo()
  const health = useQuery({ queryKey: ['health'], queryFn: () => api.get<HealthOut>('/health'), staleTime: 30_000 })
  const rates = useQuery({ queryKey: ['rates'], queryFn: () => api.get<RatesLatestOut>('/rates/latest') })
  const refresh = useMutation({
    mutationFn: () => api.post('/rates/refresh'),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['rates'] })
      notify.info('Курсы обновлены')
    },
    onError: (e) => notify.error(errorMessage(e, 'Не удалось обновить курсы')),
  })
  const s = server.data
  return (
    <div className="grid gap-4">
      <SettingsSection title="AI" description="Конфигурация сервера — только чтение. Модель меняется одной строкой в .env (AI_MODEL). Ключи сюда не попадают.">
        {server.isLoading ? (
          <Skeleton className="h-28" />
        ) : server.error ? (
          <p className="text-sm text-destructive">{errorMessage(server.error, 'Не удалось получить настройки сервера')}</p>
        ) : s ? (
          <>
            <Row label="Провайдер">
              <span className="truncate font-mono text-[13px]">{s.ai_base_url}</span>
            </Row>
            <Row label="Модель">
              <span className="font-mono text-[13px]">{s.ai_model}</span>
            </Row>
            <Row label="Статус" hint="Если AI недоступен, простые записи вида «кофе 85к» всё равно работают">
              <Badge tone={s.ai_configured ? 'income' : 'warning'}>{s.ai_configured ? 'ключ настроен' : 'ключ не задан'}</Badge>
            </Row>
            <Row label="Сервер">
              <span className="text-[13px] text-muted-foreground">
                v{s.version} · {health.data ? (health.data.status === 'ok' ? 'работает' : 'частично недоступен') : '…'}
              </span>
            </Row>
          </>
        ) : null}
      </SettingsSection>
      <SettingsSection
        title="Курсы"
        description="₫ — Vietcombank (продажа), ₽ — ЦБ РФ, крипта — CoinGecko. Курс на дату пишется в саму операцию."
        actions={
          <Button size="sm" variant="ghost" loading={refresh.isPending} onClick={() => refresh.mutate()}>
            <RefreshCw /> Обновить
          </Button>
        }
      >
        {rates.isLoading ? (
          <Skeleton className="h-16" />
        ) : rates.data ? (
          <ul className="num grid gap-1 text-[13px]">
            {[rates.data.usd_vnd, rates.data.usd_rub, ...rates.data.crypto].filter(Boolean).map((r) => (
              <li key={`${r!.base}-${r!.quote}`} className="flex items-center gap-2">
                {r!.label}
                {r!.is_fallback && <Badge tone="warning">запасной источник</Badge>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">Курсов пока нет</p>
        )}
      </SettingsSection>
    </div>
  )
}

export function AccountsSettingsSection() {
  const q = useAccounts()
  const accounts = (q.data ?? []).filter((a) => !a.is_archived)
  return (
    <SettingsSection title="Счета" description="Как вы называете счета в сообщениях и какой счёт списывать по умолчанию." actions={<Link to="/accounts" className="text-sm font-medium text-primary hover:underline">Остатки и операции →</Link>}>
      {q.isLoading ? (
        <Skeleton className="h-40" />
      ) : (
        <ul className="grid gap-4">
          {accounts.map((a) => (
            <li key={a.id} className="grid gap-2 border-b border-border pb-4 last:border-0 last:pb-0">
              <div className="flex items-center gap-2">
                <IconTile icon={accountIcon(a.icon, a.kind)} />
                <span className="text-sm font-semibold">{a.name}</span>
                <span className="text-xs text-muted-foreground">{a.is_multi_asset ? 'любые монеты' : a.currency}</span>
              </div>
              <AliasEditor account={a} compact />
              <DefaultCurrencies account={a} accounts={accounts} />
            </li>
          ))}
        </ul>
      )}
    </SettingsSection>
  )
}

export function DataSection() {
  const qc = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  const [password, setPassword] = useState('')
  const [word, setWord] = useState('')
  const dl = async (key: string, path: string, query: Record<string, unknown>, name: string) => {
    setBusy(key)
    try {
      await downloadFile(path, query, name)
    } catch (e) {
      notify.error(errorMessage(e, 'Не удалось выгрузить'))
    } finally {
      setBusy(null)
    }
  }
  const wipe = useMutation({
    mutationFn: () => api.post('/users/me/wipe', { password, confirm: 'УДАЛИТЬ' }),
    onSuccess: () => {
      setPassword('')
      setWord('')
      void qc.invalidateQueries()
      notify.info('Все финансовые данные удалены, стартовые счета и категории созданы заново')
    },
  })
  const wordOk = word.trim().toLowerCase() === 'удалить'
  return (
    <div className="grid gap-4">
      <SettingsSection title="Экспорт" description="Файлы со всеми данными — для бэкапа или таблиц.">
        <div className="flex flex-wrap gap-2">
          <Button loading={busy === 'json'} onClick={() => void dl('json', '/export/full', {}, 'pocket-export.json')}>
            <FileJson /> Всё в JSON
          </Button>
          <Button loading={busy === 'xlsx'} onClick={() => void dl('xlsx', '/export/transactions', { format: 'xlsx' }, 'pocket-transactions.xlsx')}>
            <FileSpreadsheet /> Операции XLSX
          </Button>
          <Button loading={busy === 'csv'} onClick={() => void dl('csv', '/export/transactions', { format: 'csv' }, 'pocket-transactions.csv')}>
            <FileText /> Операции CSV
          </Button>
        </div>
        <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Download className="size-3.5" /> CSV: UTF-8, разделитель «;», точка в дробях. Экспорт по фильтру — в «Операциях».
        </p>
      </SettingsSection>
      <SettingsSection title="Удаление данных" className="border-destructive/40">
        <form
          className="grid gap-3 md:max-w-md"
          onSubmit={(e) => {
            e.preventDefault()
            if (wordOk && password) wipe.mutate()
          }}
        >
          <p className="flex gap-2 rounded-md bg-expense-soft p-3 text-sm text-destructive">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            Удалю все операции, счета, категории, правила и напоминания. Это необратимо — сначала сделайте экспорт.
          </p>
          <Field label="Пароль" htmlFor="wipe-pw">
            <Input id="wipe-pw" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Field label="Напишите «удалить»" htmlFor="wipe-word">
            <Input id="wipe-word" value={word} onChange={(e) => setWord(e.target.value)} autoComplete="off" />
          </Field>
          {wipe.error && <p role="alert" className="text-sm text-destructive">{wipe.error instanceof ApiError && wipe.error.status === 401 ? 'Неверный пароль' : errorMessage(wipe.error, 'Не удалось удалить данные')}</p>}
          <Button type="submit" variant="destructive" disabled={!wordOk || !password} loading={wipe.isPending}>
            Удалить все данные
          </Button>
        </form>
      </SettingsSection>
    </div>
  )
}
