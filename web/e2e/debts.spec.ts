// Debts smoke (DEBTS.md §10) against the mock API: Home block, «Счета» → «Долги», card of a person,
// the AI line / Quick Capture cards, «Операции» rows, the reminder setting.
import { expect, test } from '@playwright/test'
import { MOCK, loginAndHome, mockState, resetMock } from './helpers'

test.beforeEach(async ({ request }) => { await resetMock(request) })

test('home: no debts → no block; «дал в долг коле 500к» → card (balance said once), block appears, opens the card', async ({ page }) => {
  await loginAndHome(page)
  await expect(page.getByTestId('balances')).toBeVisible()
  await expect(page.getByTestId('home-debts')).toHaveCount(0)
  const input = page.getByTestId('ai-input')
  await input.fill('дал в долг коле 500к')
  await input.press('Enter')
  const card = page.getByTestId('result-card').first()
  await expect(card.getByTestId('card-title')).toHaveText('500 000 ₫ · Дал в долг: Коля')
  // the subtitle already says «Коля: долг тебе 500 000 ₫» — no second debt line; the name in the title is the link
  await expect(card).toContainText('Карта · Коля: долг тебе 500 000 ₫')
  await expect(card.getByTestId('card-debt')).toHaveCount(0)
  await expect(card.getByTestId('card-debt-name')).toHaveText('Коля')
  const block = page.getByTestId('home-debts')
  await expect(block).toBeVisible()
  await expect(block.getByTestId('home-debts-owed_to_me')).toContainText('≈ 500 000 ₫')
  await expect(page.getByTestId('home-debts-i_owe')).toHaveCount(0)
  await block.getByRole('button', { name: 'Открыть долг: Коля' }).click()
  await expect(page).toHaveURL(/\/accounts\?debt=1$/)
  const details = page.getByTestId('debt-details')
  await expect(details).toContainText('долг тебе 500 000 ₫')
  await expect(details.getByTestId('debt-history')).toContainText('Дал в долг')
})

test('accounts: lists, «весь долг» repay closes it (one POST with a key), forgive asks first, undo of the last one', async ({ page, request }) => {
  await request.post(`${MOCK}/__test/debts-seed`)
  await loginAndHome(page)
  await page.goto('/accounts?debt=all')
  const section = page.getByTestId('debts-section')
  await expect(section).toBeInViewport()
  await expect(section.getByTestId('debts-owed_to_me')).toContainText('Коля')
  await expect(section.getByTestId('debts-owed_to_me')).toContainText('50 USDT')
  await expect(section.getByTestId('debts-i_owe')).toContainText('Петя')
  // Миша: owes me ₫, I owe him USDT — in both lists with own currencies
  await expect(section.getByTestId('debts-i_owe').getByRole('button', { name: 'Открыть долг: Миша' })).toContainText('20 USDT')
  await section.getByRole('button', { name: 'Открыть долг: Коля' }).click()
  const details = page.getByTestId('debt-details')
  await expect(details).toContainText('долг тебе 300 000 ₫')
  await details.getByRole('button', { name: 'Записать возврат' }).click()
  await details.getByRole('form', { name: 'Возврат долга' }).getByRole('button', { name: 'Записать возврат' }).click()
  await expect(details.getByTestId('debt-settled')).toBeVisible()
  const debtPosts = async () => (await mockState(request)).calls.filter((c) => c.path.endsWith('/debts/operations'))
  await expect.poll(async () => (await debtPosts()).length).toBe(1)
  const posts = await debtPosts()
  expect(posts[0].idem).toBeTruthy()
  await expect(section.getByTestId('debts-owed_to_me')).not.toContainText('Коля')
  // undo of the last operation brings the balance back
  await details.getByRole('button', { name: 'Отменить последнюю операцию' }).click()
  await expect(details).toContainText('долг тебе 300 000 ₫')
  // forgive: a confirmation first
  await details.getByRole('button', { name: 'Простить остаток' }).click()
  const dialog = page.getByRole('dialog', { name: 'Простить долг: Коля?' })
  await expect(dialog).toContainText('«Переводы людям»')
  await dialog.getByRole('button', { name: 'Простить' }).click()
  await expect(details.getByTestId('debt-settled')).toBeVisible()
  await expect(details.getByTestId('debt-history')).toContainText('Списан')
  // «Отменить» in the toast works while the card (a modal sheet) is open, and the card stays open
  const toast = page.locator('[data-sonner-toast]').filter({ hasText: 'Списан долг: Коля' })
  await toast.getByRole('button', { name: 'Отменить' }).click()
  await expect(details).toContainText('долг тебе 300 000 ₫')
  await expect(details.getByTestId('debt-history')).not.toContainText('Списан')
  await expect(details).toBeVisible()
})

test('accounts: «Дать в долг» form with a new person in USDT → «Разобрать» asks «С какого счёта?»', async ({ page, request }) => {
  await loginAndHome(page)
  await page.goto('/accounts')
  const section = page.getByTestId('debts-section')
  await expect(section).toContainText('Долгов нет')
  await section.getByRole('button', { name: 'Дать в долг' }).click()
  const form = page.getByRole('form', { name: 'Дать в долг' })
  await form.getByLabel('Кто').fill('Дима')
  await form.getByLabel('Сумма').fill('50 usdt')
  await expect(form).toContainText('= 50 USDT')
  await form.getByRole('button', { name: 'Дать в долг' }).click()
  await expect(section.getByTestId('debts-owed_to_me')).toContainText('Дима')
  const inbox = page.getByRole('group', { name: 'С какого счёта?' })
  await expect(inbox).toBeVisible()
  await expect(page.getByText('Дал в долг: Дима').first()).toBeVisible()
  const st = await mockState(request)
  expect(st.txs.find((t) => t.status === 'needs_account')).toBeTruthy()
})

test('operations: a debt row reads «Дал в долг: Коля», the name opens the card', async ({ page, request }) => {
  await request.post(`${MOCK}/__test/debts-seed`)
  await loginAndHome(page)
  await page.goto('/operations?period=all_time')
  const link = page.getByRole('link', { name: 'Петя' }).first()
  await expect(link).toBeVisible()
  await expect(page.locator('[data-tx-id]').filter({ hasText: 'Занял: Петя' }).first()).toBeVisible()
  await link.click()
  await expect(page).toHaveURL(/\/accounts\?debt=\d+/)
  await expect(page.getByTestId('debt-details')).toContainText('ты должен 1 000 000 ₫')
})

test('«кто мне должен» answer lists both sides; «Открыть долги →»', async ({ page, request }) => {
  await request.post(`${MOCK}/__test/debts-seed`)
  await loginAndHome(page)
  const input = page.getByTestId('ai-input')
  await input.fill('кто мне должен')
  await input.press('Enter')
  const lists = page.getByTestId('card-debts')
  await expect(lists).toContainText('Мне должны')
  await expect(lists).toContainText('Я должен')
  await page.getByRole('button', { name: /Открыть долги/ }).click()
  await expect(page).toHaveURL(/\/accounts\?debt=all/)
})

test('settings: «Напоминать о долгах» off by default, then on with «старше 45 дн.»', async ({ page }) => {
  await loginAndHome(page)
  await page.goto('/settings/telegram')
  const sw = page.getByRole('switch', { name: 'Напоминать о долгах' })
  await expect(sw).toHaveAttribute('aria-checked', 'false')
  await sw.click()
  await expect(sw).toHaveAttribute('aria-checked', 'true')
  const days = page.getByLabel('старше', { exact: true })
  await days.fill('45')
  await page.getByRole('form', { name: 'Старше N дней' }).getByRole('button', { name: 'Сохранить' }).click()
  await expect(page.getByText(/больше 45 дн\./)).toBeVisible()
})

test('Quick Capture: a debt card (compact) with the line and the excess highlighted', async ({ page, request }) => {
  await request.post(`${MOCK}/__test/debts-seed`)
  await page.setViewportSize({ width: 420, height: 450 })
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>
    w.__opened = []
    w.pocket = {
      auth: { getToken: async () => 'pfd_test', setToken: async () => {}, clearToken: async () => {} },
      window: { hide: () => {}, resize: () => {}, setPinned: () => {}, openFull: (r: string) => (w.__opened as string[]).push(r) },
      onShow: () => () => {},
      platform: 'windows',
    }
  })
  await page.goto('/quick')
  const input = page.getByTestId('ai-input')
  await input.fill('коля вернул 400к')
  await input.press('Enter')
  const card = page.getByTestId('result-card')
  await expect(card.getByTestId('card-title')).toHaveText('400 000 ₫ · Возврат долга: Коля')
  await expect(card.getByTestId('card-debt-excess')).toHaveText('Излишек 100 000 ₫ — теперь ты должен: Коля')
  await expect(card.getByRole('button', { name: 'Излишек — мне' })).toBeVisible()
  // «Излишек — мне» on its own row; «Отменить» and «Изменить» stay together on the next one (no lone wrap)
  const undo = (await card.getByRole('button', { name: /Отменить/ }).boundingBox())!
  const edit = (await card.getByRole('button', { name: /Изменить/ }).boundingBox())!
  const excess = (await card.getByRole('button', { name: 'Излишек — мне' }).boundingBox())!
  expect(Math.abs(undo.y - edit.y)).toBeLessThan(2)
  expect(excess.y).toBeLessThan(undo.y)
  expect(edit.x).toBeGreaterThan(undo.x)
  // the balance is in the subtitle («долг закрыт») — only the excess is highlighted, the name in the title opens the card
  await expect(card.getByTestId('card-debt-line')).toHaveCount(0)
  await card.getByTestId('card-debt-name').click()
  expect(await page.evaluate(() => (window as unknown as { __opened: string[] }).__opened)).toContain('/accounts?debt=1')
})
