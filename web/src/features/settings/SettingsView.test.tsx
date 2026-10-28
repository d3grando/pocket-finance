import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpError, mockApi, renderPage, setMobile } from '@/features/operations/test/utils'
import { accounts, categories, settings } from '@/features/operations/test/fixtures'
import { SettingsView } from './SettingsView'
import { scheduleText } from './sections/Recurring'

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-27T07:00:00Z'))
  setMobile(false)
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const server = { ai_base_url: 'https://api.example.com/v1', ai_model: 'your-model', ai_configured: true, telegram_bot_configured: true, telegram_bot_username: 'pocket_test_bot', version: '0.1.0', public_url: 'https://pocket.example.com' }

const route = (section: string) => ({ route: `/settings/${section}`, path: '/settings/*' })

describe('SettingsView', () => {
  it('general: switches are saved with the settings version; 409 retries once with the fresh version', async () => {
    let n = 0
    const api = mockApi({
      'GET /settings': () => ({ ...settings, version: n > 0 ? 4 : 3 }),
      'PATCH /settings': (c) => {
        n++
        if ((c.body as { version: number }).version === 3) throw new HttpError(409, 'version_conflict')
        return { ...settings, ...(c.body as object), version: 5 }
      },
    })
    renderPage(<SettingsView />, route('general'))
    const sw = await screen.findByRole('switch', { name: 'Голое число меньше 1000 — тысячи' })
    expect(sw).toHaveAttribute('aria-checked', 'true')
    await userEvent.setup().click(sw)
    await waitFor(() => expect(api.find('PATCH', '/settings')).toHaveLength(2))
    expect(api.find('PATCH', '/settings').map((c) => c.body)).toEqual([
      { small_amount_thousands: false, version: 3 },
      { small_amount_thousands: false, version: 4 },
    ])
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Голое число меньше 1000 — тысячи' })).toHaveAttribute('aria-checked', 'false'))
  })

  it('theme applies immediately and is stored', async () => {
    mockApi({ 'GET /settings': settings, 'PATCH /settings': (c) => ({ ...settings, ...(c.body as object), version: 4 }) })
    renderPage(<SettingsView />, route('general'))
    await userEvent.setup().click(await screen.findByRole('radio', { name: 'Тёмная' }))
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem('pf.theme')).toBe('dark')
  })

  it('telegram: report toggles and time', async () => {
    const api = mockApi({ 'GET /settings': settings, 'GET /settings/server': server, 'GET /recurring': [], 'PATCH /settings': (c) => ({ ...settings, ...(c.body as object), version: 4 }) })
    renderPage(<SettingsView />, route('telegram'))
    const user = userEvent.setup()
    await user.click(await screen.findByRole('switch', { name: 'Ежедневный отчёт' }))
    await waitFor(() => expect(api.find('PATCH', '/settings')[0]?.body).toEqual({ report_daily: true, version: 3 }))
    expect(screen.getByText('Привязан к Telegram ID 123456789')).toBeInTheDocument()
  })

  it('categories: groups, 🆕 confirm and merge preview', async () => {
    const api = mockApi({ 'GET /categories': categories, 'PATCH /categories/:id': (c) => ({ ...categories[9], ...(c.body as object) }), 'POST /categories/merge': categories[0] })
    renderPage(<SettingsView />, route('categories'))
    expect(await screen.findByText('Бизнес-расходы')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Подтвердить «Вейпы»' }))
    await waitFor(() => expect(api.find('PATCH', '/categories/30')[0]?.body).toEqual({ version: 1, is_new: false }))
    await user.click(screen.getByRole('button', { name: 'Ещё для «Вейпы»' }))
    await user.click(await screen.findByRole('menuitem', { name: /Слить с/ }))
    const dlg = await screen.findByRole('dialog')
    await user.selectOptions(within(dlg).getByLabelText('Цель'), '1')
    expect(within(dlg).getByText('Перенесу 2 операции в «Еда»')).toBeInTheDocument()
    await user.click(within(dlg).getByRole('button', { name: /Слить/ }))
    await waitFor(() => expect(api.find('POST', '/categories/merge')[0]?.body).toEqual({ source_id: 30, target_id: 1 }))
  })

  it('security: lists devices and revokes one', async () => {
    const api = mockApi({
      'GET /auth/me': { user_id: 1, username: 'owner', auth: 'session', source: 'web', onboarding_done: true, has_passkeys: false, csrf_token: 'x' },
      'GET /passkeys': [],
      'GET /auth/sessions': [{ id: 's1', user_agent: 'Mozilla/5.0 (iPhone) Safari/605', ip: '1.2.3.4', created_at: '2026-10-20T10:00:00+07:00', last_seen_at: '2026-10-27T10:00:00+07:00', expires_at: '2027-01-20T10:00:00+07:00', is_current: true }],
      'GET /devices': [{ id: 'd1', name: 'DESKTOP-ABC', platform: 'windows', created_at: '2026-10-20T10:00:00+07:00', last_used_at: null, is_current: false }],
      'DELETE /devices/:id': { ok: true },
    })
    renderPage(<SettingsView />, route('security'))
    expect(await screen.findByText('DESKTOP-ABC')).toBeInTheDocument()
    expect(screen.getByText(/Safari · iPhone/)).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Отозвать' }))
    await waitFor(() => expect(api.find('DELETE', '/devices/d1')).toHaveLength(1))
  })

  it('AI is read-only server config; data wipe needs the word', async () => {
    mockApi({ 'GET /settings/server': server, 'GET /health': { status: 'ok', db: true, version: '0.1.0', server_time: '' }, 'GET /rates/latest': { usd_vnd: { base: 'USD', quote: 'VND', rate: '26140', rate_date: '2026-10-27', kind: 'sell', source: 'vietcombank', fetched_at: null, is_fallback: false, label: '$1 = 26 140 ₫ (VCB, 27.10)' }, usd_rub: null, crypto: [] } })
    const r = renderPage(<SettingsView />, route('ai'))
    expect(await screen.findByText('your-model')).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).toBeNull()
    r.unmount()
    renderPage(<SettingsView />, route('data'))
    const user = userEvent.setup()
    const btn = await screen.findByRole('button', { name: 'Удалить все данные' })
    expect(btn).toBeDisabled()
    await user.type(screen.getByLabelText('Пароль'), 'secret')
    await user.type(screen.getByLabelText('Напишите «удалить»'), 'удалить')
    expect(btn).toBeEnabled()
  })

  it('mobile: list of sections, then a section with «‹ Настройки»', async () => {
    setMobile(true)
    mockApi({ 'GET /accounts': accounts })
    renderPage(<SettingsView />, { route: '/settings', path: '/settings/*' })
    expect(await screen.findByRole('link', { name: /Мои аккаунты/ })).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('link', { name: /Счета/ }))
    expect(await screen.findByRole('link', { name: 'Настройки' })).toBeInTheDocument()
    expect(await screen.findByText('карта')).toBeInTheDocument()
  })

  it('recurring «Записать сейчас»: a repeat after a lost answer reuses the Idempotency-Key', async () => {
    const rec = { id: 2, title: 'Hetzner', kind: 'expense', amount: '45000', currency: 'VND', account_id: null, category_id: 14, merchant_id: null, description: null, frequency: 'monthly', interval: 1, day_of_month: 5, weekday: null, month: null, next_due_on: '2026-10-27', remind_time: '10:00:00', is_active: true, last_reminded_on: null, version: 1 }
    let n = 0
    const api = mockApi({
      'GET /settings': settings,
      'GET /recurring': [rec],
      'POST /recurring/:id/record': () => {
        n++
        if (n === 1) throw new TypeError('Failed to fetch') // the server recorded it, the answer was lost
        return { transaction: { id: 15, amount: '45000', currency: 'VND', category: { name: 'Серверы/инфра' }, title: 'Hetzner' }, action_id: 'a-rec' }
      },
    })
    renderPage(<SettingsView />, route('recurring'))
    const user = userEvent.setup()
    const record = async () => {
      await user.click(await screen.findByRole('button', { name: 'Ещё для «Hetzner»' }))
      await user.click(await screen.findByRole('menuitem', { name: /Записать сейчас/ }))
    }
    await record()
    await waitFor(() => expect(api.find('POST', '/recurring/2/record')).toHaveLength(1))
    expect(await screen.findByText('Нет ответа сервера — не знаю, сохранилось ли')).toBeInTheDocument()
    await record()
    await waitFor(() => expect(api.find('POST', '/recurring/2/record')).toHaveLength(2))
    const [a, b] = api.find('POST', '/recurring/2/record').map((c) => c.headers['Idempotency-Key'])
    expect(a).toMatch(/^[0-9a-f-]{36}$/)
    expect(b).toBe(a)
    await waitFor(() => expect(screen.getByText(/45\s000\s₫ · Серверы\/инфра/)).toBeInTheDocument())
    // after a confirmed success the next «Записать» is a new attempt
    await record()
    await waitFor(() => expect(api.find('POST', '/recurring/2/record')).toHaveLength(3))
    expect(api.find('POST', '/recurring/2/record')[2].headers['Idempotency-Key']).not.toBe(a)
  })

  it('desktop hotkey is not an editable web setting (Electron keeps its own)', async () => {
    const api = mockApi({ 'GET /settings': settings, 'PATCH /settings': (c) => ({ ...settings, ...(c.body as object) }) })
    renderPage(<SettingsView />, route('general'))
    expect(await screen.findByText(/трее → «Настройки»/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Хоткей мини-окна (Windows)')).toBeNull()
    expect(api.find('PATCH', '/settings')).toHaveLength(0)
  })

  it('describes recurring schedules', () => {
    expect(scheduleText({ frequency: 'monthly', interval: 1, day_of_month: 5, weekday: null, month: null })).toBe('каждый месяц, 5-го')
    expect(scheduleText({ frequency: 'monthly', interval: 1, day_of_month: 31, weekday: null, month: null })).toBe('каждый месяц, в последний день')
    expect(scheduleText({ frequency: 'weekly', interval: 2, day_of_month: null, weekday: 4, month: null })).toBe('раз в 2 нед., по пятницам')
    expect(scheduleText({ frequency: 'yearly', interval: 1, day_of_month: 8, weekday: null, month: 3 })).toBe('каждый год, 8 марта')
  })
})
