// Regressions (shell): undo after edit, edit after a change elsewhere, SSE card refresh,
// PWA cold start without network, Quick Capture window grows and shrinks.
import { expect, test } from '@playwright/test'
import { MOCK, loginAndHome, mockState, resetMock } from './helpers'

test.beforeEach(async ({ request }) => { await resetMock(request) })

test('«Отменить» after «Изменить» removes the operation', async ({ page, request }) => {
  await loginAndHome(page)
  const input = page.getByTestId('ai-input')
  await input.fill('еда 250к')
  await input.press('Enter')
  const card = page.getByTestId('result-card').first()
  await expect(card).toContainText('250 000 ₫ · Еда')
  await card.getByTestId('btn-edit').click()
  await card.getByTestId('edit-amount').fill('300000')
  await card.getByTestId('edit-save').click()
  await expect(card).toContainText('300 000 ₫ · Еда')
  await card.getByTestId('btn-undo').click()
  await expect(page.getByTestId('result-card').first()).toContainText('Отменено: 300 000 ₫ · Еда')
  await expect(page.getByText(/отменить нельзя/)).toHaveCount(0)
  await expect.poll(async () => (await mockState(request)).txs.length).toBe(0)
})

test('edit after a change on another client: shows the current value, the next save works', async ({ page, request }) => {
  await loginAndHome(page)
  const input = page.getByTestId('ai-input')
  await input.fill('еда 250к')
  await input.press('Enter')
  const card = page.getByTestId('result-card').first()
  await expect(card).toContainText('250 000 ₫ · Еда')
  await card.getByTestId('btn-edit').click()
  await request.post(`${MOCK}/__test/patch-tx`, { data: { amount: 251000 } })
  await card.getByTestId('edit-amount').fill('300000')
  await card.getByTestId('edit-amount').press('Enter')
  await expect(card).toContainText('Запись изменили на другом устройстве — показал актуальную')
  await expect(card.getByTestId('edit-amount')).toHaveValue('251000')
  await card.getByTestId('edit-amount').fill('300000')
  await card.getByTestId('edit-amount').press('Enter')
  await expect(card).toContainText('300 000 ₫ · Еда')
  await expect.poll(async () => (await mockState(request)).txs[0]?.amount).toBe('300000')
})

test('a visible card follows a change made by the bot (SSE)', async ({ page, request }) => {
  await loginAndHome(page)
  const input = page.getByTestId('ai-input')
  await input.fill('еда 250к')
  await input.press('Enter')
  const card = page.getByTestId('result-card').first()
  await expect(card).toContainText('250 000 ₫ · Еда')
  await request.post(`${MOCK}/__test/patch-tx`, { data: { amount: 251000 } })
  await expect(card).toContainText('251 000 ₫ · Еда')
})

test('PWA cold start without network: the shell with «нет связи», never a blank page (w1b)', async ({ page, context }) => {
  await loginAndHome(page)
  await page.evaluate(async () => { await navigator.serviceWorker.ready })
  await context.setOffline(true)
  await page.reload()
  await expect(page.getByTestId('offline-banner')).toBeVisible()
  await expect(page.getByTestId('ai-input')).toBeVisible()
  await context.setOffline(false)
})

test('Quick Capture: the window grows for the account choice and shrinks back', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 450 })
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>
    w.__sizes = []
    w.pocket = {
      auth: { getToken: async () => 'pfd_test', setToken: async () => {}, clearToken: async () => {} },
      window: { hide: () => {}, resize: (h: number) => (w.__sizes as number[]).push(h), setPinned: () => {}, openFull: () => {} },
      onShow: () => () => {},
      platform: 'windows',
    }
  })
  await page.goto('/capture')
  const input = page.getByTestId('ai-input')
  await expect(page.getByTestId('quick-kpis')).toContainText('Сегодня')
  await input.fill('кофе 11к')
  await input.press('Enter')
  await expect(page.getByTestId('result-card')).toContainText('11 000 ₫ · Кофе')
  const sizes = () => page.evaluate(() => (window as unknown as { __sizes: number[] }).__sizes)
  // an ordinary result card fits into 450: fewer «Последние» rows instead of a taller window
  await expect.poll(async () => (await sizes()).at(-1)).toBe(450)
  await input.fill('подписка 7 usdt')
  await input.press('Enter')
  await expect(page.getByText('С какого счёта?')).toBeVisible()
  await input.press('1')
  await expect(page.getByTestId('result-card')).toContainText('7 USDT · Подписки')
  await expect.poll(async () => (await sizes()).at(-1)).toBe(450)
  const all = await sizes()
  expect(Math.max(...all)).toBeLessThanOrEqual(640)
})

test('manual $ without an account says it waits for an account choice', async ({ page, request }) => {
  await loginAndHome(page)
  await page.goto('/add')
  await page.getByTestId('manual-amount').fill('15')
  await page.getByRole('button', { name: '$', exact: true }).click()
  await page.getByTestId('manual-categories').getByRole('radio', { name: 'Подписки' }).click()
  await page.getByTestId('manual-accounts').getByRole('button', { name: 'Спросить потом' }).click()
  await page.getByTestId('manual-save').click()
  const toast = page.locator('[data-sonner-toast]').first()
  await expect(toast).toContainText('Записал в расходы. С какого счёта?')
  await expect(toast.getByRole('button', { name: 'Разобрать' })).toBeVisible()
  await expect.poll(async () => (await mockState(request)).txs[0]?.status).toBe('needs_account')
})
