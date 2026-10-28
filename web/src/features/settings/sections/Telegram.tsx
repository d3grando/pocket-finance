// Settings → Telegram: bot status, reports on/off + time, send now / preview, link to reminders.
import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { Send } from 'lucide-react'
import { api, qk, type RecurringOut, type ReportOut, type ServerInfoOut } from '@/lib/api'
import { errorMessage, useSettings } from '@/features/operations/api'
import { Badge, Button, Input, Skeleton, Switch } from '@/features/operations/ui/kit'
import { notify } from '@/features/operations/ui/overlays'
import { useSettingsPatch } from '../api'
import { Row, SettingsSection } from '../ui'
import { DebtReminderSetting } from '@/features/debts/DebtReminderSetting'

type Kind = 'monthly' | 'daily' | 'weekly'

/** TELEGRAM_BOT_USERNAME from GET /settings/server → «name» (no «@»); null = not set / not a username. */
function botUsername(server: Pick<ServerInfoOut, 'telegram_bot_username'> | undefined): string | null {
  const v = (server?.telegram_bot_username ?? '').trim().replace(/^@/, '')
  return /^[A-Za-z0-9_]{3,64}$/.test(v) ? v : null
}

function BotLink({ server }: { server: ServerInfoOut | undefined }) {
  if (!server) return <span>Telegram-бот</span>
  const name = botUsername(server)
  if (!name) return <span className="text-muted-foreground" data-testid="bot-username-missing">Укажите TELEGRAM_BOT_USERNAME на сервере</span>
  return <a href={`https://t.me/${name}`} target="_blank" rel="noreferrer" className="text-primary hover:underline">@{name}</a>
}

export function useServerInfo() {
  return useQuery({ queryKey: qk.serverInfo, queryFn: () => api.get<ServerInfoOut>('/settings/server'), staleTime: 300_000 })
}

function ReportActions({ kind }: { kind: Kind }) {
  const [preview, setPreview] = useState<string | null>(null)
  const send = useMutation({
    mutationFn: () => api.post<{ outbox_id: number }>(`/reports/${kind}/send`),
    onSuccess: () => notify.info('Отчёт поставлен в очередь — придёт в Telegram в течение минуты'),
    onError: (e) => notify.error(errorMessage(e, 'Не удалось отправить отчёт')),
  })
  const load = useMutation({
    mutationFn: () => api.get<ReportOut>(`/reports/${kind}`),
    onSuccess: (r) => setPreview(r.text_plain),
    onError: (e) => notify.error(errorMessage(e, 'Не удалось собрать отчёт')),
  })
  return (
    <div className="grid w-full gap-2">
      <div className="flex flex-wrap gap-1.5 md:justify-end">
        <Button size="sm" variant="ghost" loading={load.isPending} onClick={() => (preview ? setPreview(null) : load.mutate())}>
          {preview ? 'Скрыть' : 'Предпросмотр'}
        </Button>
        <Button size="sm" variant="outline" loading={send.isPending} onClick={() => send.mutate()}>
          <Send /> Прислать сейчас
        </Button>
      </div>
      {preview && <pre className="max-h-64 overflow-auto rounded-md bg-surface-2 p-3 font-mono text-xs whitespace-pre-wrap">{preview}</pre>}
    </div>
  )
}

const WEEKDAYS = ['По понедельникам', 'По вторникам', 'По средам', 'По четвергам', 'По пятницам', 'По субботам', 'По воскресеньям']

export function TelegramSection() {
  const { data: s, isLoading } = useSettings()
  const server = useServerInfo()
  const patch = useSettingsPatch()
  const recurring = useQuery({ queryKey: qk.recurring, queryFn: () => api.get<RecurringOut[]>('/recurring') })
  const [time, setTime] = useState<string | null>(null)
  if (isLoading || !s) return <SettingsSection title="Telegram"><Skeleton className="h-60" /></SettingsSection>
  const t = time ?? s.report_time.slice(0, 5)
  const active = recurring.data?.filter((r) => r.is_active).length ?? 0
  return (
    <SettingsSection title="Telegram" description="Бот отвечает только вашему Telegram-аккаунту.">
      <Row label={<BotLink server={server.data} />} hint={s.telegram_user_id ? `Привязан к Telegram ID ${s.telegram_user_id}` : 'Ещё не привязан — напишите боту /start со своего аккаунта'}>
        <div className="flex gap-1.5">
          {server.data && <Badge tone={server.data.telegram_bot_configured ? 'income' : 'warning'}>{server.data.telegram_bot_configured ? 'бот подключён' : 'бот не настроен на сервере'}</Badge>}
          <Badge tone={s.telegram_user_id ? 'income' : 'muted'}>{s.telegram_user_id ? 'аккаунт привязан' : 'не привязан'}</Badge>
        </div>
      </Row>
      <Row label="Время месячного отчёта" hint="Пояс — из общих настроек" htmlFor="tg-time">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (time && /^\d{2}:\d{2}$/.test(time)) patch.mutate({ report_time: `${time}:00` }, { onSuccess: () => setTime(null) }) }}>
          <Input id="tg-time" type="time" value={t} onChange={(e) => setTime(e.target.value)} className="w-32" />
          <Button type="submit" disabled={!time || time === s.report_time.slice(0, 5)} loading={patch.isPending}>
            Сохранить
          </Button>
        </form>
      </Row>
      <Row label="Месячный отчёт" htmlFor="tg-monthly" hint={`1-го числа в ${s.report_time.slice(0, 5)}: расходы, доходы, поток, категории`}>
        <div className="grid w-full gap-2">
          <div className="flex md:justify-end">
            <Switch id="tg-monthly" checked={s.report_monthly} onCheckedChange={(v) => patch.mutate({ report_monthly: v })} label="Месячный отчёт" />
          </div>
          <ReportActions kind="monthly" />
        </div>
      </Row>
      <Row label="Ежедневный отчёт" htmlFor="tg-daily" hint={`Каждый день в ${(s.report_daily_time ?? '22:00').slice(0, 5)}`}>
        <div className="grid w-full gap-2">
          <div className="flex md:justify-end">
            <Switch id="tg-daily" checked={s.report_daily} onCheckedChange={(v) => patch.mutate({ report_daily: v })} label="Ежедневный отчёт" />
          </div>
          {s.report_daily && <ReportActions kind="daily" />}
        </div>
      </Row>
      <Row label="Недельный отчёт" htmlFor="tg-weekly" hint={`${WEEKDAYS[s.report_weekly_day ?? 6]} в ${(s.report_weekly_time ?? '21:00').slice(0, 5)}`}>
        <div className="grid w-full gap-2">
          <div className="flex md:justify-end">
            <Switch id="tg-weekly" checked={s.report_weekly} onCheckedChange={(v) => patch.mutate({ report_weekly: v })} label="Недельный отчёт" />
          </div>
          {s.report_weekly && <ReportActions kind="weekly" />}
        </div>
      </Row>
      <Row label="Напоминания о регулярных платежах" hint={recurring.data ? `Активно: ${active} из ${recurring.data.length}` : 'Кнопки [Записать] [Пропустить] в боте'}>
        <Link to="/settings/recurring" className="text-sm font-medium text-primary hover:underline">
          Настроить →
        </Link>
      </Row>
      <DebtReminderSetting settings={s} />
    </SettingsSection>
  )
}
