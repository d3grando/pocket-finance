// Settings → «Напоминать о долгах» (DEBTS.md §9–10): off by default, «старше N дней»
// (1–365, 30). PATCH /settings with `version` (useSettingsPatch retries a 409 once with the fresh one).
// An older server without these fields → the row is not shown.
import { useState } from 'react'
import type { SettingsOut } from '@/lib/api/types'
import { Button, Input, Switch } from '@/features/operations/ui/kit'
import { useSettingsPatch } from '@/features/settings/api'
import { Row } from '@/features/settings/ui'
import { DEBT_DAYS_MAX, DEBT_DAYS_MIN, parseReminderDays } from './lib'

export function DebtReminderSetting({ settings }: { settings: SettingsOut }) {
  const patch = useSettingsPatch()
  const [days, setDays] = useState<string | null>(null)
  if (settings.debt_reminders_enabled === undefined) return null
  const on = !!settings.debt_reminders_enabled
  const saved = settings.debt_reminder_days ?? 30
  const value = days ?? String(saved)
  const parsed = parseReminderDays(value)
  const dirty = days !== null && parsed !== saved
  return (
    <Row
      label="Напоминать о долгах"
      htmlFor="debt-reminders"
      hint={on ? `Бот напомнит, если долгу больше ${saved} дн.: «Коля должен 300 000 ₫ уже ${saved} дней» [Записать возврат] [Напомнить позже]` : 'Выключено. Бот может напоминать о долгах старше N дней'}
    >
      <div className="grid w-full gap-2">
        <div className="flex md:justify-end">
          <Switch id="debt-reminders" checked={on} onCheckedChange={(v) => patch.mutate({ debt_reminders_enabled: v })} label="Напоминать о долгах" />
        </div>
        {on && (
          <form
            aria-label="Старше N дней"
            className="flex items-center gap-2 md:justify-end"
            onSubmit={(e) => {
              e.preventDefault()
              if (parsed !== null && dirty) patch.mutate({ debt_reminder_days: parsed }, { onSuccess: () => setDays(null) })
            }}
          >
            <label htmlFor="debt-reminder-days" className="text-sm text-muted-foreground">старше</label>
            <Input
              id="debt-reminder-days"
              inputMode="numeric"
              value={value}
              onChange={(e) => setDays(e.target.value.replace(/\D/g, '').slice(0, 3))}
              aria-invalid={parsed === null || undefined}
              className="num w-20 text-right"
            />
            <span className="text-sm text-muted-foreground">дн.</span>
            <Button type="submit" size="sm" disabled={!dirty || parsed === null} loading={patch.isPending}>Сохранить</Button>
          </form>
        )}
        {on && parsed === null && <p role="alert" className="text-xs text-destructive md:text-right">От {DEBT_DAYS_MIN} до {DEBT_DAYS_MAX} дней</p>}
      </div>
    </Row>
  )
}
