// Debts on a 390 px phone (project «mobile»): Home block, «Счета» → «Долги», the card of a person as a
// bottom sheet, the reminder setting — no horizontal scroll anywhere.
import { expect, test, type Page } from '@playwright/test'
import { MOCK, loginAndHome, resetMock } from './helpers'

test.beforeEach(async ({ request }) => {
  await resetMock(request)
  await request.post(`${MOCK}/__test/debts-seed`)
})

const noHScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)

test('mobile: home block, accounts lists, card sheet and repay form fit 390 px', async ({ page }) => {
  await loginAndHome(page)
  const block = page.getByTestId('home-debts')
  await block.scrollIntoViewIfNeeded()
  await expect(block.getByTestId('home-debts-owed_to_me')).toContainText('Коля')
  await expect(block.getByTestId('home-debts-i_owe')).toContainText('Петя')
  expect(await noHScroll(page)).toBe(true)

  await block.getByRole('button', { name: 'Открыть долг: Вася' }).click()
  await expect(page).toHaveURL(/\/accounts\?debt=\d+/)
  const details = page.getByTestId('debt-details')
  await expect(details).toContainText('долг тебе 50 USDT')
  await details.getByRole('button', { name: 'Записать возврат' }).click()
  const form = details.getByRole('form', { name: 'Возврат долга' })
  await expect(form.getByRole('button', { name: 'Записать возврат' })).toBeVisible()
  const box = (await form.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(390)
  expect(await noHScroll(page)).toBe(true)
  await page.keyboard.press('Escape')

  await page.goto('/accounts?debt=all')
  const section = page.getByTestId('debts-section')
  await expect(section.getByTestId('debts-owed_to_me')).toContainText('Миша')
  await expect(section.getByRole('button', { name: 'Дать в долг' })).toBeVisible()
  expect(await noHScroll(page)).toBe(true)
})

test('mobile: settings → Telegram → «Напоминать о долгах»', async ({ page }) => {
  await loginAndHome(page)
  await page.goto('/settings/telegram')
  const sw = page.getByRole('switch', { name: 'Напоминать о долгах' })
  await sw.scrollIntoViewIfNeeded()
  await sw.click()
  await expect(page.getByLabel('старше', { exact: true })).toBeVisible()
  expect(await noHScroll(page)).toBe(true)
})

test('mobile: a long name and several currencies do not widen «Счета» → «Долги»', async ({ page, request }) => {
  const name = 'Мария-Антуанетта Константиновна'
  for (const [amount, currency] of [['100000', 'VND'], ['125', 'USDT'], ['3.5', 'TON']]) {
    const r = await request.post(`${MOCK}/__test/debt-op`, { data: { op: 'lend', person_name: name, amount, currency, account_none: true } })
    expect(r.ok()).toBe(true)
  }
  await loginAndHome(page)
  await page.goto('/accounts?debt=all')
  const section = page.getByTestId('debts-section')
  const row = section.getByRole('button', { name: `Открыть долг: ${name}` })
  await expect(row).toBeVisible()
  await row.scrollIntoViewIfNeeded()
  expect(await noHScroll(page)).toBe(true)
  // every person's row (and its amounts) ends inside the screen
  for (const b of await section.getByTestId('debt-person').all()) {
    const box = (await b.boundingBox())!
    expect(box.x + box.width).toBeLessThanOrEqual(390)
  }
  const kolya = (await section.getByRole('button', { name: 'Открыть долг: Коля' }).boundingBox())!
  expect(kolya.x + kolya.width).toBeLessThanOrEqual(390)
})

test('mobile: «Отменить» in the toast over the open card undoes the write-off (no tap-through)', async ({ page }) => {
  await loginAndHome(page)
  await page.goto('/accounts?debt=all')
  await page.getByTestId('debts-section').getByRole('button', { name: 'Открыть долг: Коля' }).click()
  const details = page.getByTestId('debt-details')
  await expect(details).toContainText('долг тебе 300 000 ₫')
  await details.getByRole('button', { name: 'Простить остаток' }).click()
  await page.getByRole('dialog', { name: 'Простить долг: Коля?' }).getByRole('button', { name: 'Простить' }).click()
  await expect(details.getByTestId('debt-settled')).toBeVisible()
  const toast = page.locator('[data-sonner-toast]').filter({ hasText: 'Списан долг: Коля' })
  await toast.getByRole('button', { name: 'Отменить' }).click()
  await expect(details).toContainText('долг тебе 300 000 ₫')
  await expect(page).toHaveURL(/\/accounts\?debt=\d+/) // not taken to /operations by a tap-through
})
