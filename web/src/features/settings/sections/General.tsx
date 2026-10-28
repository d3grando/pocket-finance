// Settings → Общие: input/analytics currency, timezone, theme, list density, «кофе 85» rule; the desktop
// hotkey is shown read-only — the Windows app keeps it in its own config and changes it in its own window
// (it checks the combination is free there), so editing user_settings.desktop_hotkey here did nothing.
import { useSettings } from '@/features/operations/api'
import { CurrencySelect } from '@/features/operations/components/CurrencySelect'
import { useLocalPref } from '@/features/operations/ui/hooks'
import { Kbd, NativeSelect, Segmented, Skeleton, Switch } from '@/features/operations/ui/kit'
import { applyTheme, TIMEZONES, tzOffsetLabel, useSettingsPatch, type ThemePref } from '../api'
import { Row, SettingsSection } from '../ui'

export function GeneralSection() {
  const { data: s, isLoading } = useSettings()
  const patch = useSettingsPatch()
  const [density, setDensity] = useLocalPref<'normal' | 'compact'>('pf.density', 'normal')
  if (isLoading || !s) return <SettingsSection title="Общие"><Skeleton className="h-64" /></SettingsSection>
  const tzs = TIMEZONES.includes(s.timezone) ? TIMEZONES : [s.timezone, ...TIMEZONES]
  return (
    <SettingsSection title="Общие" description="Валюты, пояс, внешний вид. Сохраняется сразу.">
      <Row label="Валюта ввода по умолчанию" hint="Если в сообщении нет валюты" htmlFor="set-defcur">
        <div className="w-full md:w-48">
          <CurrencySelect id="set-defcur" value={s.default_currency} onChange={(v) => patch.mutate({ default_currency: v })} options={['VND', 'USD', 'USDT', 'RUB']} />
        </div>
      </Row>
      <Row label="Валюта аналитики" hint="Плитки, бот и мини-окно — всегда в ₫">
        <Segmented label="Валюта аналитики" value={s.analytics_currency === 'VND' ? 'VND' : 'USD'} onChange={(v) => patch.mutate({ analytics_currency: v })} options={[{ value: 'VND', label: '₫' }, { value: 'USD', label: '$' }]} />
      </Row>
      <Row label="Часовой пояс" hint="«Вчера», «в пятницу» и границы дней считаются в нём" htmlFor="set-tz">
        <NativeSelect id="set-tz" value={s.timezone} onChange={(e) => patch.mutate({ timezone: e.target.value })} className="md:w-72">
          {tzs.map((tz) => (
            <option key={tz} value={tz}>
              {tz} ({tzOffsetLabel(tz)})
            </option>
          ))}
        </NativeSelect>
      </Row>
      <Row label="Тема">
        <Segmented<ThemePref>
          label="Тема"
          value={s.theme}
          onChange={(v) => {
            applyTheme(v)
            patch.mutate({ theme: v })
          }}
          options={[{ value: 'system', label: 'Как в системе' }, { value: 'light', label: 'Светлая' }, { value: 'dark', label: 'Тёмная' }]}
        />
      </Row>
      <Row label="Плотность списка" hint="Только на этом устройстве">
        <Segmented label="Плотность списка" value={density} onChange={setDensity} options={[{ value: 'normal', label: 'Обычная' }, { value: 'compact', label: 'Компактная' }]} />
      </Row>
      <Row label="Голое число < 1000 в ₫ — тысячи" hint="«кофе 85» → 85 000 ₫">
        <Switch checked={s.small_amount_thousands} onCheckedChange={(v) => patch.mutate({ small_amount_thousands: v })} label="Голое число меньше 1000 — тысячи" />
      </Row>
      <Row label="Хоткей мини-окна (Windows)" hint="Меняется в самом приложении: значок Pocket в трее → «Настройки». Там же оно проверит, что сочетание не занято другой программой.">
        <span className="text-sm text-muted-foreground">
          по умолчанию <Kbd className="text-[12px] [@media(hover:none)]:inline">Ctrl+Alt+Space</Kbd>
        </span>
      </Row>
    </SettingsSection>
  )
}
