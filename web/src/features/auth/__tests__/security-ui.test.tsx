// Regression tests (web): a passkey needs the password on an old session and
// «Выйти со всех устройств» / «Сменить пароль» remove passkeys by default; «Выйти» in Settings,
// the sidebar and the mobile bar; the user's localStorage is cleared on logout and on any 401;
// the bot username comes from the server (TELEGRAM_BOT_USERNAME), never from the code.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TooltipProvider } from '@/components/ui/tooltip'
import { HttpError, mockApi, renderPage, setMobile } from '@/features/operations/test/utils'
import { settings } from '@/features/operations/test/fixtures'
import { SettingsView } from '@/features/settings/SettingsView'
import { Sidebar } from '@/features/shell/Sidebar'
import { MobileTopBar } from '@/features/shell/MobileTopBar'
import { PasskeyOffer } from '@/features/dashboard/PasskeyOffer'
import { request } from '@/lib/api/client'
import { STORE } from '@/lib/storage'
import { installSignOutCleanup } from '../session'

const webauthn = vi.hoisted(() => ({
  startRegistration: vi.fn(async () => ({ id: 'cred-1', type: 'public-key' })),
}))
vi.mock('@simplewebauthn/browser', () => ({
  browserSupportsWebAuthn: () => true,
  platformAuthenticatorIsAvailable: async () => true,
  startRegistration: webauthn.startRegistration,
  startAuthentication: vi.fn(),
}))

const ME = { user_id: 1, username: 'owner', auth: 'session', source: 'web', onboarding_done: true, has_passkeys: true, csrf_token: 'x' }
const PASSKEY = { id: 'p1', name: 'iPhone', created_at: '2026-10-20T10:00:00+07:00', last_used_at: null }
const securityRoutes = {
  'GET /auth/me': ME,
  'GET /passkeys': [PASSKEY],
  'GET /auth/sessions': [],
  'GET /devices': [],
}
const security = { route: '/settings/security', path: '/settings/*' }

beforeEach(() => {
  setMobile(false)
  localStorage.clear()
  sessionStorage.clear()
  webauthn.startRegistration.mockClear()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('passkey registration needs the password on an old session', () => {
  it('no password first; 403 reauth_required → password dialog → options with the password', async () => {
    const api = mockApi({
      ...securityRoutes,
      'POST /passkeys/register/options': (c) => {
        const pw = (c.body as { password: string | null }).password
        if (pw === null) throw new HttpError(403, 'reauth_required', 'Подтвердите паролем')
        if (pw !== 'correct horse battery') throw new HttpError(401, 'wrong_password', 'Неверный пароль')
        return { challenge_id: 'ch-1', options: { challenge: 'abc' } }
      },
      'POST /passkeys/register/verify': { ...PASSKEY, id: 'p2', name: 'Mac' },
    })
    renderPage(<SettingsView />, security)
    const user = userEvent.setup()
    await user.clear(await screen.findByLabelText('Название'))
    await user.type(screen.getByLabelText('Название'), 'Mac')
    await user.click(screen.getByRole('button', { name: /Включить Face ID/ }))

    const dlg = await screen.findByTestId('passkey-password-dialog')
    expect(api.find('POST', '/passkeys/register/options').map((c) => c.body)).toEqual([{ password: null }])
    expect(webauthn.startRegistration).not.toHaveBeenCalled()

    // a wrong password is a form error, not a lost session
    await user.type(within(dlg).getByLabelText('Пароль'), 'nope')
    await user.click(within(dlg).getByRole('button', { name: /Продолжить/ }))
    expect(await within(dlg).findByRole('alert')).toHaveTextContent('Неверный пароль')
    expect(screen.queryByTestId('elsewhere')).toBeNull()

    await user.clear(within(dlg).getByLabelText('Пароль'))
    await user.type(within(dlg).getByLabelText('Пароль'), 'correct horse battery')
    await user.click(within(dlg).getByRole('button', { name: /Продолжить/ }))
    await waitFor(() => expect(api.find('POST', '/passkeys/register/verify')).toHaveLength(1))
    expect(api.find('POST', '/passkeys/register/options').map((c) => c.body)).toEqual([
      { password: null }, { password: 'nope' }, { password: 'correct horse battery' },
    ])
    expect(api.find('POST', '/passkeys/register/verify')[0].body).toMatchObject({ challenge_id: 'ch-1', name: 'Mac', credential: { id: 'cred-1' } })
    expect(webauthn.startRegistration).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.queryByTestId('passkey-password-dialog')).toBeNull())
    expect(localStorage.getItem(STORE.hasPasskey)).toBe('1')
  })

  it('a fresh session (right after login) adds a key without asking for the password', async () => {
    const api = mockApi({
      ...securityRoutes,
      'POST /passkeys/register/options': { challenge_id: 'ch-2', options: { challenge: 'abc' } },
      'POST /passkeys/register/verify': { ...PASSKEY, id: 'p3' },
    })
    renderPage(<SettingsView />, security)
    await userEvent.setup().click(await screen.findByRole('button', { name: /Включить Face ID/ }))
    await waitFor(() => expect(api.find('POST', '/passkeys/register/verify')).toHaveLength(1))
    expect(api.find('POST', '/passkeys/register/options').map((c) => c.body)).toEqual([{ password: null }])
    expect(screen.queryByTestId('passkey-password-dialog')).toBeNull()
  })

  it('the dashboard offer asks for the password on an old session too', async () => {
    const api = mockApi({
      'GET /auth/me': { ...ME, has_passkeys: false },
      'POST /passkeys/register/options': (c) => {
        if ((c.body as { password: string | null }).password === null) throw new HttpError(403, 'reauth_required', 'Подтвердите паролем')
        return { challenge_id: 'ch-3', options: { challenge: 'abc' } }
      },
      'POST /passkeys/register/verify': PASSKEY,
    })
    renderPage(<PasskeyOffer />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Включить' }))
    const dlg = await screen.findByTestId('passkey-password-dialog')
    await user.type(within(dlg).getByLabelText('Пароль'), 'secret-password')
    await user.click(within(dlg).getByRole('button', { name: /Продолжить/ }))
    await waitFor(() => expect(api.find('POST', '/passkeys/register/verify')).toHaveLength(1))
    expect(api.find('POST', '/passkeys/register/options').at(-1)?.body).toEqual({ password: 'secret-password' })
    await waitFor(() => expect(screen.queryByTestId('passkey-offer')).toBeNull())
  })
})

describe('logout-all and password change remove passkeys by default', () => {
  it('«Выйти со всех устройств»: remove_passkeys=true by default, false when unticked', async () => {
    localStorage.setItem(STORE.hasPasskey, '1')
    const api = mockApi({
      ...securityRoutes,
      'POST /auth/logout-all': (c) => ({ ok: true, sessions_revoked: 2, devices_revoked: 1, passkeys_removed: c.query.get('remove_passkeys') === 'true' ? 1 : 0 }),
    })
    renderPage(<SettingsView />, security)
    const user = userEvent.setup()
    const boxes = await screen.findAllByRole('checkbox', { name: 'Удалить ключи входа (passkey)' })
    expect(boxes).toHaveLength(2) // password form + logout-all
    for (const b of boxes) expect(b).toHaveAttribute('aria-checked', 'true')

    await user.click(screen.getByRole('button', { name: 'Выйти со всех устройств' }))
    await waitFor(() => expect(api.find('POST', '/auth/logout-all')).toHaveLength(1))
    const first = api.find('POST', '/auth/logout-all')[0].query
    expect(first.get('keep_current')).toBe('true')
    expect(first.get('remove_passkeys')).toBe('true')
    await waitFor(() => expect(localStorage.getItem(STORE.hasPasskey)).toBeNull())

    await user.click(boxes[1])
    await user.click(screen.getByRole('button', { name: 'Выйти со всех устройств' }))
    await waitFor(() => expect(api.find('POST', '/auth/logout-all')).toHaveLength(2))
    expect(api.find('POST', '/auth/logout-all')[1].query.get('remove_passkeys')).toBe('false')
  })

  it('«Сменить пароль» sends remove_passkeys (on by default)', async () => {
    const api = mockApi({ ...securityRoutes, 'POST /users/me/password': { ok: true } })
    renderPage(<SettingsView />, security)
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Текущий пароль'), 'old-password-1')
    await user.type(screen.getByLabelText('Новый пароль'), 'new-password-12')
    await user.type(screen.getByLabelText('Ещё раз'), 'new-password-12')
    await user.click(screen.getByRole('button', { name: /Сменить пароль/ }))
    await waitFor(() => expect(api.find('POST', '/users/me/password')).toHaveLength(1))
    expect(api.find('POST', '/users/me/password')[0].body).toEqual({
      current_password: 'old-password-1', new_password: 'new-password-12', revoke_other_sessions: true, remove_passkeys: true,
    })
  })
})

describe('«Выйти» ends this session and forgets the user data', () => {
  const seed = () => {
    localStorage.setItem(STORE.lastSent, 'кофе 85к у @someone')
    localStorage.setItem(STORE.passkeyOffer, 'dismissed')
    localStorage.setItem(STORE.theme, 'dark')
    localStorage.setItem('pf.density', 'compact')
    localStorage.setItem('other.app', 'keep')
    sessionStorage.setItem(STORE.draft, 'черновик')
  }
  const expectCleared = () => {
    expect(localStorage.getItem(STORE.lastSent)).toBeNull()
    expect(localStorage.getItem(STORE.passkeyOffer)).toBeNull()
    expect(localStorage.getItem(STORE.theme)).toBe('dark') // device preferences stay
    expect(localStorage.getItem('pf.density')).toBe('compact')
    expect(localStorage.getItem('other.app')).toBe('keep') // not ours
    expect(sessionStorage.getItem(STORE.draft)).toBeNull()
  }

  it('Settings → «Выйти»: POST /auth/logout → storage cleared → /login', async () => {
    seed()
    const api = mockApi({ ...securityRoutes, 'POST /auth/logout': { ok: true } })
    renderPage(<SettingsView />, security)
    await userEvent.setup().click(await screen.findByTestId('settings-logout'))
    expect(await screen.findByTestId('elsewhere')).toBeInTheDocument()
    expect(api.find('POST', '/auth/logout')).toHaveLength(1)
    expectCleared()
  })

  it('sidebar «Выйти»', async () => {
    seed()
    const api = mockApi({ 'POST /auth/logout': { ok: true } })
    const r = renderPage(<TooltipProvider><Sidebar collapsed={false} onToggle={() => {}} /></TooltipProvider>)
    await userEvent.setup().click(screen.getByTestId('sidebar-logout'))
    await waitFor(() => expect(r.router.state.location.pathname).toBe('/login'))
    expect(api.find('POST', '/auth/logout')).toHaveLength(1)
    expectCleared()
  })

  it('mobile bar: menu → «Выйти»', async () => {
    seed()
    setMobile(true)
    const api = mockApi({ 'POST /auth/logout': { ok: true } })
    const r = renderPage(<TooltipProvider><MobileTopBar /></TooltipProvider>)
    const user = userEvent.setup()
    await user.click(screen.getByTestId('mobile-menu'))
    await user.click(await screen.findByTestId('mobile-logout'))
    await waitFor(() => expect(r.router.state.location.pathname).toBe('/login'))
    expect(api.find('POST', '/auth/logout')).toHaveLength(1)
    expectCleared()
  })

  it('any 401 clears the user data in localStorage (the draft survives for the re-login)', async () => {
    seed()
    const off = installSignOutCleanup()
    try {
      mockApi({ 'GET /transactions': () => { throw new HttpError(401, 'unauthorized', 'Сессия истекла') } })
      await expect(request('GET', '/transactions')).rejects.toMatchObject({ status: 401 })
      expect(localStorage.getItem(STORE.lastSent)).toBeNull()
      expect(localStorage.getItem(STORE.theme)).toBe('dark')
      expect(sessionStorage.getItem(STORE.draft)).toBe('черновик')
      // a wrong password in a form is not a lost session
      localStorage.setItem(STORE.lastSent, 'ещё')
      mockApi({ 'POST /users/me/wipe': () => { throw new HttpError(401, 'wrong_password', 'Неверный пароль') } })
      await expect(request('POST', '/users/me/wipe', {})).rejects.toMatchObject({ code: 'wrong_password' })
      expect(localStorage.getItem(STORE.lastSent)).toBe('ещё')
    } finally {
      off()
    }
  })
})

describe('Telegram bot username comes from the server', () => {
  const server = { ai_base_url: '', ai_model: '', ai_configured: false, telegram_bot_configured: true, version: '1.0.0', public_url: '' }
  const routes = { 'GET /settings': settings, 'GET /recurring': [] }

  it('TELEGRAM_BOT_USERNAME set → a t.me link', async () => {
    mockApi({ ...routes, 'GET /settings/server': { ...server, telegram_bot_username: 'pocket_test_bot' } })
    renderPage(<SettingsView />, { route: '/settings/telegram', path: '/settings/*' })
    const link = await screen.findByRole('link', { name: '@pocket_test_bot' })
    expect(link).toHaveAttribute('href', 'https://t.me/pocket_test_bot')
  })

  it('not set → a hint for the admin, no link', async () => {
    mockApi({ ...routes, 'GET /settings/server': { ...server, telegram_bot_username: null } })
    renderPage(<SettingsView />, { route: '/settings/telegram', path: '/settings/*' })
    expect(await screen.findByTestId('bot-username-missing')).toHaveTextContent('Укажите TELEGRAM_BOT_USERNAME на сервере')
    expect(screen.queryByRole('link', { name: /^@/ })).toBeNull()
    expect(document.querySelector('a[href^="https://t.me/"]')).toBeNull()
  })
})
