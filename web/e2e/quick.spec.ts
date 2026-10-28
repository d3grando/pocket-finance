import { expect, test } from '@playwright/test'
import { mockState, resetMock } from './helpers'

test.beforeEach(async ({ request }) => { await resetMock(request) })

test('Quick Capture with the Electron bridge: Bearer token, Enter saves, Esc hides', async ({ page, request }) => {
  await page.setViewportSize({ width: 420, height: 450 })
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>
    w.__hidden = 0
    w.__sizes = []
    w.pocket = {
      auth: { getToken: async () => 'pfd_test', setToken: async () => {}, clearToken: async () => {} },
      window: {
        hide: () => { (w.__hidden as number)++; w.__hidden = (w.__hidden as number) },
        resize: (h: number) => (w.__sizes as number[]).push(h),
        setPinned: () => {}, openFull: () => {},
      },
      onShow: () => () => {},
      platform: 'windows',
    }
  })
  await page.goto('/capture')
  const input = page.getByTestId('ai-input')
  await expect(input).toBeFocused()
  await expect(page.getByTestId('quick-kpis')).toContainText('Сегодня')
  await input.fill('еда 250к')
  await input.press('Shift+Enter')
  await expect(input).toHaveValue('еда 250к\n')
  await input.fill('еда 250к')
  await input.press('Enter')
  await expect(page.getByTestId('result-card')).toContainText('250 000 ₫ · Еда')
  await expect(page.getByTestId('quick-recent')).toContainText('Еда')
  const st = await mockState(request)
  expect(st.calls.find((c) => c.path.endsWith('/assistant/messages'))?.auth).toBe('Bearer pfd_test')
  await input.press('Escape')
  expect(await page.evaluate(() => (window as unknown as { __hidden: number }).__hidden)).toBeGreaterThan(0)
})

test('Quick Capture without a session shows the in-window login', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 450 })
  await page.goto('/quick')
  await expect(page.getByText('Вход в Pocket')).toBeVisible()
  await expect(page.getByText('Пароль нужен один раз — дальше приложение входит само.')).toBeVisible()
})

// The real preload (desktop/src/preload/index.ts): «shown» is window.onShown({ edge }), platform is
// process.platform ('win32'), the device name is in config.get().
test('Quick Capture with the real preload shape: login sends platform=windows, «shown» re-fetches data', async ({ page, request }) => {
  await page.setViewportSize({ width: 420, height: 450 })
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>
    let token: string | null = null
    const shown: ((e: { edge: string }) => void)[] = []
    w.__fireShown = (edge: string) => shown.slice().forEach((cb) => cb({ edge }))
    w.__shownSubs = () => shown.length
    w.pocket = {
      isDesktop: true,
      platform: 'win32',
      windowKind: 'popup',
      auth: {
        getToken: async () => token,
        setToken: async (t: string) => { token = t; return { ok: true } },
        clearToken: async () => { token = null },
        login: async () => ({ ok: false, code: 'server', message: 'not used' }),
        logout: async () => {},
      },
      config: {
        get: async () => ({ serverUrl: location.origin, apiBaseUrl: '/api/v1', hotkey: 'Ctrl+Alt+Space', hotkeyStatus: 'ok', autostart: true, version: '0.1.0', platform: 'win32', deviceName: 'DESKTOP-QA7', renderer: 'web', signedIn: false }),
        setServerUrl: async () => ({ ok: false, message: '' }),
        onChange: () => () => {},
      },
      window: {
        hide: async () => {}, openFull: async () => {}, openSettings: async () => {}, resize: async () => {}, setPinned: async () => {},
        onShown: (cb: (e: { edge: string }) => void) => { shown.push(cb); return () => { shown.splice(shown.indexOf(cb), 1) } },
        onHidden: () => () => {},
      },
      connection: { get: async () => 'online', onChange: () => () => {} },
      openExternal: async () => true,
    }
  })
  await page.goto('/quick')
  await expect(page.getByText('Вход в Pocket')).toBeVisible()
  await page.getByTestId('login-password').fill('test-password-123')
  await page.getByTestId('login-submit').click()
  await expect(page.getByTestId('quick-kpis')).toContainText('Сегодня')
  expect((await mockState(request)).deviceLogins).toEqual([{ device_name: 'DESKTOP-QA7', platform: 'windows' }])
  await expect.poll(() => page.evaluate(() => (window as unknown as { __shownSubs: () => number }).__shownSubs())).toBeGreaterThan(0)

  // a change made elsewhere while the SSE «change» was missed: showing the popup must re-fetch
  const gets = async () => (await mockState(request)).calls.filter((c) => c.method === 'GET' && c.path.startsWith('/api/v1/') && c.path !== '/api/v1/events/stream').length
  await page.waitForTimeout(500)
  const before = await gets()
  await page.getByTestId('ai-input').blur()
  await page.evaluate(() => (window as unknown as { __fireShown: (e: string) => void }).__fireShown('bottom'))
  await expect(page.getByTestId('ai-input')).toBeFocused()
  await expect.poll(gets).toBeGreaterThan(before)
  const after = (await mockState(request)).calls.slice(-20).map((c) => c.path)
  expect(after.some((p) => p.startsWith('/api/v1/transactions') || p.startsWith('/api/v1/stats'))).toBe(true)
})
