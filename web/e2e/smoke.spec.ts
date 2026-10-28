import { expect, test } from '@playwright/test'
import { MOCK, PASSWORD, login, loginAndHome, mockState, resetMock } from './helpers'

test.beforeEach(async ({ request }) => { await resetMock(request) })

test('login: wrong password, then right one → home; theme toggle', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.getByTestId('login-password').fill('nope')
  await page.getByTestId('login-submit').click()
  await expect(page.getByTestId('login-error')).toHaveText('Неверный пароль')
  await page.getByTestId('login-password').fill(PASSWORD)
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'Главная' })).toBeVisible()
  await expect(page.getByTestId('sidebar')).toBeVisible()
  const dark = await page.evaluate(() => document.documentElement.classList.contains('dark'))
  await page.getByTestId('theme-toggle').click()
  expect(await page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(!dark)
})

test('«Выйти» in the sidebar ends the session and forgets the last AI line', async ({ page, request }) => {
  await loginAndHome(page)
  const input = page.getByTestId('ai-input')
  await input.fill('кофе 85к')
  await input.press('Enter')
  await expect(page.getByTestId('result-card').first()).toContainText('85 000 ₫')
  expect(await page.evaluate(() => localStorage.getItem('pf.ai.last'))).toBe('кофе 85к')
  await page.getByTestId('sidebar-logout').click()
  await expect(page).toHaveURL(/\/login/)
  await expect.poll(async () => (await mockState(request)).calls.some((c) => c.method === 'POST' && c.path === '/api/v1/auth/logout')).toBe(true)
  expect(await page.evaluate(() => localStorage.getItem('pf.ai.last'))).toBeNull()
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/) // the cookie session is gone
})

test('onboarding: «2.5м» parsed, skip the rest, lands on home', async ({ page, request }) => {
  await resetMock(request, { onboarded: false })
  await login(page)
  await expect(page).toHaveURL(/\/onboarding/)
  await page.getByTestId('ob-input-0').fill('2.5м')
  await expect(page.getByText('= 2 500 000 ₫')).toBeVisible()
  await page.getByTestId('ob-input-2').fill('312.4 USDT, 3 TON')
  await page.getByTestId('ob-done').click()
  await expect(page.getByRole('heading', { name: 'Главная' })).toBeVisible()
  await expect.poll(async () => (await mockState(request)).onboarding_done).toBe(true)
  await expect(page.getByTestId('balances')).toContainText('2 500 000 ₫')
})

test('AI line: Enter saves once, card + undo; Ctrl+K focuses the line', async ({ page, request }) => {
  await loginAndHome(page)
  const input = page.getByTestId('ai-input')
  await page.locator('body').click({ position: { x: 5, y: 600 } })
  await page.keyboard.press('Control+k')
  await expect(input).toBeFocused()
  await input.fill('еда 250к')
  await input.press('Enter')
  const card = page.getByTestId('result-card').first()
  await expect(card).toContainText('250 000 ₫ · Еда')
  await expect(input).toHaveValue('')
  await expect(page.getByTestId('recent-list')).toContainText('Еда')
  await expect(page.getByTestId('kpi-today-value')).toHaveText('−250 000 ₫')
  const st = await mockState(request)
  expect(st.txs).toHaveLength(1)
  const send = st.calls.find((c) => c.path.endsWith('/assistant/messages'))!
  expect(send.idem).toMatch(/^[0-9a-f-]{36}$/)
  await card.getByTestId('btn-undo').click()
  await expect(page.getByText('Отменено: 250 000 ₫ · Еда').first()).toBeVisible()
  await expect.poll(async () => (await mockState(request)).txs.length).toBe(0)
})

test('needs_account: choose wallet by digit; «разобрать» badge', async ({ page, request }) => {
  await loginAndHome(page)
  const input = page.getByTestId('ai-input')
  await input.fill('подписка 15 usdt')
  await input.press('Enter')
  await expect(page.getByText('С какого счёта?')).toBeVisible()
  await expect(page.getByTestId('needs-account-badge')).toContainText('Разобрать: 1')
  await input.press('2')
  await expect(page.getByTestId('result-card').first()).toContainText('15 USDT · Подписки')
  const st = await mockState(request)
  expect(st.txs[0].account?.id).toBe(4)
  await expect(page.getByTestId('needs-account-badge')).toHaveCount(0)
})

test('offline: honest «нет связи», text kept, retry after reconnect does not duplicate', async ({ page, context, request }) => {
  await loginAndHome(page)
  await context.setOffline(true)
  await expect(page.getByTestId('offline-banner')).toContainText('Нет связи с сервером. Записи не сохраняются.')
  const input = page.getByTestId('ai-input')
  await input.fill('кофе 85к')
  await input.press('Enter')
  await expect(page.getByTestId('result-card').first()).toContainText('Не сохранено — нет связи')
  await expect(input).toHaveValue('кофе 85к')
  await context.setOffline(false)
  // reconnect → the card is re-sent by itself with the same key (DESIGN §10.10.7), no click needed
  await expect(page.getByTestId('result-card').first()).toContainText('85 000 ₫ · Кофе', { timeout: 15_000 })
  await expect(page.getByText('Снова на связи')).toBeVisible()
  await expect(input).toHaveValue('')
  // press retry again via a second Enter on an empty field does nothing; exactly one row exists
  const st = await mockState(request)
  expect(st.txs).toHaveLength(1)
  const keys = st.calls.filter((c) => c.path.endsWith('/assistant/messages')).map((c) => c.idem)
  expect(new Set(keys).size).toBe(1)
})

test('server 500: «Ничего не записано», Enter again reuses the key', async ({ page, request }) => {
  await loginAndHome(page)
  await request.post(`${MOCK}/__test/fail-next`, { data: { count: 1 } })
  const input = page.getByTestId('ai-input')
  await input.fill('такси 70к')
  await input.press('Enter')
  await expect(page.getByTestId('result-card').first()).toContainText('Ничего не записано')
  await expect(input).toHaveValue('такси 70к')
  await input.press('Enter')
  await expect(page.getByTestId('result-card').first()).toContainText('70 000 ₫ · Такси/транспорт')
  const st = await mockState(request)
  const keys = st.calls.filter((c) => c.path.endsWith('/assistant/messages')).map((c) => c.idem)
  expect(keys).toHaveLength(2)
  expect(keys[0]).toBe(keys[1])
  expect(st.txs).toHaveLength(1)
})

test('SSE change from another client refreshes the list', async ({ page }) => {
  await loginAndHome(page)
  await expect(page.getByTestId('connection-dot').first()).toHaveAttribute('data-status', 'online', { timeout: 10_000 })
  // another client (bot) writes; we only push the SSE signal
  await page.request.post('/api/v1/transactions', {
    data: { kind: 'expense', amount: '45000', currency: 'VND', category_id: 3 },
    headers: { 'Idempotency-Key': 'e2e-other-client', 'X-CSRF-Token': 'csrf-test' },
  })
  await expect(page.getByTestId('recent-list')).toContainText('Кофе', { timeout: 10_000 })
})

test('PWA: manifest, icons and service worker are served', async ({ request }) => {
  const manifest = await (await request.get('/manifest.webmanifest')).json()
  expect(manifest.display).toBe('standalone')
  expect(manifest.name).toBe('Pocket Finance')
  expect(manifest.icons.some((i: { purpose?: string }) => i.purpose === 'maskable')).toBe(true)
  for (const p of ['/icons/icon-192.png', '/icons/maskable-512.png', '/apple-touch-icon.png', '/favicon.ico', '/sw.js']) {
    expect((await request.get(p)).status(), p).toBe(200)
  }
  const sw = await (await request.get('/sw.js')).text()
  expect(sw).not.toMatch(/api\/v1/) // the SW never routes API calls
})
