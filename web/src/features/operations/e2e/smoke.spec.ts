// Pages smoke: each page renders real-shaped data, has no horizontal scroll at 1440 and 390 px,
// key interactions reach the API with the right contract. Screenshots (light + dark) go to test-results.
import { expect, test, type Page } from '@playwright/test'
import { mockApi } from './mock'

const errors = new WeakMap<Page, string[]>()

test.beforeEach(async ({ page }) => {
  const list: string[] = []
  errors.set(page, list)
  page.on('pageerror', (e) => list.push(String(e)))
  await page.clock.setFixedTime(new Date('2026-10-27T07:00:00Z'))
})

test.afterEach(async ({ page }) => {
  expect(errors.get(page), 'no uncaught page errors').toEqual([])
})

async function noHorizontalScroll(page: Page) {
  const { sw, iw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth }))
  expect(sw, 'page must not scroll horizontally').toBeLessThanOrEqual(iw + 1)
}

async function shots(page: Page, name: string, project: string) {
  await page.screenshot({ path: `test-results/web-b/shots/${project}-${name}-light.png`, fullPage: true })
  await page.evaluate(() => document.documentElement.classList.add('dark'))
  await page.waitForTimeout(150)
  await page.screenshot({ path: `test-results/web-b/shots/${project}-${name}-dark.png`, fullPage: true })
  await page.evaluate(() => document.documentElement.classList.remove('dark'))
}

test('operations: list, search, inline edit, delete + undo', async ({ page }, info) => {
  const rec = await mockApi(page)
  await page.goto('/operations')
  await expect(page.getByRole('heading', { name: 'Операции' })).toBeVisible()
  await expect(page.getByText('латте · Highlands')).toBeVisible()
  await expect(page.getByText(/Сегодня · вт, 27 октября/)).toBeVisible()
  await noHorizontalScroll(page)
  await shots(page, 'operations', info.project.name)

  await page.getByRole('searchbox', { name: 'Поиск операций' }).fill('grab')
  await expect(page.getByText('латте · Highlands')).toBeHidden()
  await expect(page.getByText('Grab').first()).toBeVisible()
  await expect(page).toHaveURL(/q=grab/)
  await page.getByRole('searchbox', { name: 'Поиск операций' }).fill('')
  await expect(page.getByText('латте · Highlands')).toBeVisible()

  await page.getByRole('button', { name: /латте · Highlands/ }).first().click()
  const form = page.getByRole('form', { name: 'Правка операции' })
  await expect(form).toBeVisible()
  await form.getByLabel('Сумма').fill('50к')
  await form.getByLabel('Сумма').press('Enter')
  await expect.poll(() => rec.filter((r) => r.method === 'PATCH').length).toBe(1)
  expect(rec.find((r) => r.method === 'PATCH')!.body).toEqual({ version: 1, amount: '50000' })
  await expect(form).toBeHidden()

  if (info.project.name === 'desktop') {
    // the edited row stays selected; ↑ keeps it (first row), Delete removes it with an undo toast
    await page.keyboard.press('ArrowUp')
    await page.keyboard.press('Delete')
    await expect(page.getByText(/Удалено: 45/)).toBeVisible()
    await page.getByRole('button', { name: 'Вернуть', exact: true }).click()
    await expect.poll(() => rec.filter((r) => r.path === '/actions/act-del/undo').length).toBe(1)
    expect(rec.find((r) => r.path === '/actions/act-del/undo')!.headers['idempotency-key']).toBeTruthy()
  }
})

test('operations: day total sits in the amount column; mobile filters open at the top', async ({ page }, info) => {
  await mockApi(page)
  await page.goto('/operations')
  await expect(page.getByText('латте · Highlands')).toBeVisible()
  if (info.project.name === 'desktop') {
    const total = await page.getByText(/^−165\s000\s₫$/).boundingBox()
    const amount = await page.locator('[data-tx-id="1"]').getByText(/^−45\s000\s₫$/).boundingBox()
    expect(Math.abs(total!.x + total!.width - (amount!.x + amount!.width))).toBeLessThanOrEqual(2)
  } else {
    await page.getByRole('button', { name: /^Фильтры/ }).click()
    const sheet = page.getByRole('dialog', { name: 'Фильтры' })
    await expect(sheet.getByText('Период', { exact: true })).toBeVisible()
    await page.waitForTimeout(400)
    const top = await sheet.evaluate((el) => Math.max(...Array.from(el.querySelectorAll('*')).map((n) => (n as HTMLElement).scrollTop)))
    expect(top, 'the sheet must not be scrolled to the categories').toBe(0)
    await expect(sheet.getByText('Период', { exact: true })).toBeInViewport()
  }
})

test('operations: manual add posts an idempotent create', async ({ page }, info) => {
  const rec = await mockApi(page)
  await page.goto('/operations')
  await expect(page.getByText('латте · Highlands')).toBeVisible()
  // «N» belongs to the shell (the shell's global manual form); the page button opens the operations form
  await page.getByRole('button', { name: info.project.name === 'desktop' ? /^Новая/ : 'Новая операция' }).click()
  const form = page.getByRole('form', { name: 'Новая операция' })
  await expect(form).toBeVisible()
  await form.getByLabel('Сумма').fill('85')
  await expect(form.getByText(/85\s000\s₫/).first()).toBeVisible()
  await form.getByRole('radio', { name: /Кофе/ }).click()
  await noHorizontalScroll(page)
  // double Enter: exactly one POST
  await form.getByLabel('Сумма').focus()
  await page.keyboard.press('Enter')
  await page.keyboard.press('Enter')
  await expect.poll(() => rec.filter((r) => r.method === 'POST' && r.path === '/transactions').length).toBe(1)
  await page.waitForTimeout(300)
  expect(rec.filter((r) => r.method === 'POST' && r.path === '/transactions')).toHaveLength(1)
  const post = rec.find((r) => r.method === 'POST' && r.path === '/transactions')!
  expect(post.body).toMatchObject({ kind: 'expense', amount: '85000', currency: 'VND', category_id: 3 })
  expect(post.headers['idempotency-key']).toBeTruthy()
})

test('operations: long lists are virtualised', async ({ page }) => {
  await mockApi(page, { many: 450 })
  await page.goto('/operations?period=all_time')
  await expect(page.getByText('Покупка 0', { exact: true })).toBeVisible()
  for (let i = 0; i < 12 && (await page.locator('[data-virtual]').count()) === 0; i++) {
    await page.mouse.wheel(0, 20000)
    await page.waitForTimeout(250)
  }
  await expect(page.locator('[data-virtual]')).toHaveCount(1)
  const rendered = await page.locator('[data-tx-id]').count()
  expect(rendered).toBeGreaterThan(5)
  expect(rendered).toBeLessThan(200)
  await noHorizontalScroll(page)
})

test('analytics: KPIs, charts and ₫/$ switch', async ({ page }, info) => {
  await mockApi(page)
  await page.goto('/analytics')
  await expect(page.getByRole('heading', { name: 'Аналитика' })).toBeVisible()
  await expect(page.getByText('−$476.30')).toBeVisible()
  await expect(page.getByText('+12% к сент.')).toBeVisible()
  await expect.poll(() => page.locator('.recharts-surface').count()).toBeGreaterThanOrEqual(4)
  await expect(page.getByText('Серверы/инфра · vast.ai')).toBeVisible()
  await noHorizontalScroll(page)
  await shots(page, 'analytics', info.project.name)
  await page.getByRole('radio', { name: '₫' }).click()
  await expect(page.getByText(/−12\s450\s482\s₫/)).toBeVisible()
  await expect(page).toHaveURL(/cur=VND/)
  await page.getByRole('button', { name: 'Предыдущий период' }).click()
  await expect(page.getByText('Сентябрь 2026', { exact: true })).toBeVisible()
})

test('analytics: «за всё время» uses the backend preset', async ({ page }) => {
  const urls: string[] = []
  page.on('request', (r) => { if (r.url().includes('/api/v1/stats/')) urls.push(r.url()) })
  await mockApi(page)
  await page.goto('/analytics?period=all_time')
  await expect(page.getByText('−$17.64')).toBeVisible()
  await expect.poll(() => urls.some((u) => u.includes('/stats/series') && u.includes('preset=all_time'))).toBe(true)
  expect(urls.filter((u) => u.includes('2000-01-01'))).toEqual([])
  expect(urls.filter((u) => u.includes('/stats/compare') && u.includes('prev_date_from'))).toEqual([])
  await noHorizontalScroll(page)
})

test('accounts: balances truthfully, inbox, account panel', async ({ page }, info) => {
  const rec = await mockApi(page)
  await page.goto('/accounts')
  await expect(page.getByRole('heading', { name: 'Счета' })).toBeVisible()
  const cash = page.locator('article', { has: page.getByRole('button', { name: 'Открыть счёт Наличные' }) })
  await expect(cash.getByText('Остаток не задан')).toBeVisible()
  await expect(page.getByText(/1 счёт без остатка не учтён$/)).toBeVisible()
  // the «по умолчанию для ₫» badge stays inside its card (it once stuck out at 390 and 1440)
  const bank = page.locator('article', { has: page.getByRole('button', { name: 'Открыть счёт Карта', exact: true }) })
  const [card, badge] = await Promise.all([bank.boundingBox(), bank.getByText(/по умолчанию для/).boundingBox()])
  expect(badge!.x + badge!.width).toBeLessThanOrEqual(card!.x + card!.width + 0.5)
  await noHorizontalScroll(page)
  await shots(page, 'accounts', info.project.name)
  await page.getByRole('group', { name: 'С какого счёта?' }).getByRole('button', { name: 'Кошелёк' }).click()
  await expect.poll(() => rec.filter((r) => r.path === '/transactions/6/account').length).toBe(1)
  await page.getByRole('button', { name: 'Открыть счёт Кошелёк' }).click()
  await expect(page.getByRole('dialog').getByText('Псевдонимы')).toBeVisible()
  await noHorizontalScroll(page)
})

test('settings: every section renders without horizontal scroll', async ({ page }, info) => {
  await mockApi(page)
  const sections = ['general', 'categories', 'accounts', 'merchants', 'my-accounts', 'recurring', 'telegram', 'security', 'ai', 'data']
  if (info.project.name === 'mobile') {
    await page.goto('/settings')
    await expect(page.getByRole('link', { name: /Регулярные платежи/ })).toBeVisible()
    await noHorizontalScroll(page)
  }
  for (const s of sections) {
    await page.goto(`/settings/${s}`)
    await expect(page.locator('section[aria-label]:not([aria-label^="Notifications"])').first()).toBeVisible()
    await noHorizontalScroll(page)
    if (s === 'general' || s === 'categories' || s === 'security') await shots(page, `settings-${s}`, info.project.name)
  }
  await page.goto('/settings/general')
  await page.getByRole('radio', { name: 'Тёмная' }).click()
  await expect(page.locator('html')).toHaveClass(/dark/)
})
