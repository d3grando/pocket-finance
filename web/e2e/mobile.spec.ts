import { expect, test } from '@playwright/test'
import { loginAndHome, mockState, resetMock } from './helpers'

test.beforeEach(async ({ request }) => { await resetMock(request) })

test('mobile: bottom nav, no horizontal scroll, «+» opens the AI sheet, «Заполнить вручную» saves', async ({ page, request }) => {
  await loginAndHome(page)
  const nav = page.getByTestId('bottom-nav')
  await expect(nav).toBeVisible()
  await expect(nav.getByRole('link')).toHaveCount(4)
  await expect(nav.getByRole('button', { name: 'Добавить' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  // tap → bottom sheet with the AI line focused (DESIGN §10.3)
  await page.getByTestId('nav-add').click()
  const sheet = page.getByTestId('add-sheet-ai')
  await expect(sheet).toBeVisible()
  await expect(sheet.getByTestId('ai-input')).toBeFocused()
  await sheet.getByTestId('add-sheet-manual-btn').click()
  await expect(page.getByTestId('add-sheet-manual')).toBeVisible()
  await page.getByTestId('manual-amount').fill('150к')
  await expect(page.getByTestId('manual-parsed')).toHaveText('= 150 000 ₫')
  await page.getByTestId('manual-categories').getByRole('radio', { name: 'Еда' }).click()
  await page.getByTestId('manual-save').click()
  await expect(page.locator('[data-sonner-toast]').first()).toContainText('Записал: 150 000 ₫ · Еда')
  const st = await mockState(request)
  expect(st.txs).toHaveLength(1)
  expect(st.calls.find((c) => c.path.endsWith('/transactions') && c.method === 'POST')?.idem).toBeTruthy()
})

test('mobile: long press on «+» opens the manual form page', async ({ page }) => {
  await loginAndHome(page)
  const add = page.getByTestId('nav-add')
  const box = (await add.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.waitForTimeout(700)
  await page.mouse.up()
  await expect(page).toHaveURL(/\/add/)
  await expect(page.getByTestId('manual-amount')).toBeVisible()
})

test('mobile: onboarding «Готово» is not covered by the bottom nav', async ({ page, request }) => {
  await resetMock(request, { onboarded: false })
  await page.goto('/login')
  await page.getByTestId('login-password').fill('test-password-123')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/onboarding/)
  await expect(page.getByTestId('bottom-nav')).toHaveCount(0)
  await page.mouse.wheel(0, 200)
  const done = page.getByTestId('ob-done')
  const box = (await done.boundingBox())!
  const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest('[data-testid="ob-done"]') !== null, [box.x + box.width / 2, box.y + box.height / 2])
  expect(hit).toBe(true)
})

test('mobile: the offline banner sits under the top bar (never over it) and shows «данные на»', async ({ page, context }) => {
  await loginAndHome(page)
  await context.setOffline(true)
  const banner = page.getByTestId('offline-banner')
  await expect(banner).toBeVisible()
  await page.mouse.wheel(0, 400)
  await page.waitForTimeout(300)
  const bar = (await page.getByTestId('mobile-topbar').boundingBox())!
  const b = (await banner.boundingBox())!
  expect(b.y).toBeGreaterThanOrEqual(bar.y + bar.height - 1)
  await expect(page.getByTestId('offline-stale')).toBeVisible()
  // the theme toggle in the top bar is still reachable
  const toggle = (await page.getByTestId('mobile-topbar').getByRole('button').last().boundingBox())!
  const hit = await page.evaluate(([x, y]) => !!document.elementFromPoint(x, y)?.closest('[data-testid="mobile-topbar"]'), [toggle.x + toggle.width / 2, toggle.y + toggle.height / 2])
  expect(hit).toBe(true)
  await context.setOffline(false)
})
