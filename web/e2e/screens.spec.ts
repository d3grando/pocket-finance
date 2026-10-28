// Screenshots for review (DESIGN §15 checklist: light + dark, desktop + mobile + Quick Capture).
// Opt-in: SCREENS=1 pnpm exec playwright test e2e/screens.spec.ts → test-results/screens/*.png
import { test } from '@playwright/test'
import { MOCK, loginAndHome, resetMock } from './helpers'

test.skip(!process.env.SCREENS, 'set SCREENS=1')

for (const theme of ['light', 'dark'] as const) {
  test(`home ${theme}`, async ({ page, request }, info) => {
    await resetMock(request)
    await page.emulateMedia({ colorScheme: theme })
    await loginAndHome(page)
    const input = page.getByTestId('ai-input')
    for (const t of ['еда 250к', 'кофе 85к', 'такси 70к']) { await input.fill(t); await input.press('Enter'); await page.waitForTimeout(150) }
    await input.fill('подписка 15 usdt'); await input.press('Enter')
    await page.waitForTimeout(800)
    await page.screenshot({ path: `test-results/screens/${info.project.name}-home-${theme}.png`, fullPage: true })
  })
}

test('quick capture', async ({ page, request }, info) => {
  await resetMock(request)
  await request.post(`${MOCK}/__test/onboarded`)
  await page.setViewportSize({ width: 420, height: 450 })
  await page.addInitScript(() => { (window as unknown as { pocket: unknown }).pocket = { auth: { getToken: async () => 'pfd_test' }, window: {}, platform: 'windows' } })
  await page.goto('/quick')
  const input = page.getByTestId('ai-input')
  await input.fill('еда 250к'); await input.press('Enter')
  await page.waitForTimeout(600)
  await page.screenshot({ path: `test-results/screens/${info.project.name}-quick.png` })
})

test('login + onboarding', async ({ page, request }, info) => {
  await resetMock(request, { onboarded: false })
  await page.goto('/login')
  await page.waitForTimeout(300)
  await page.screenshot({ path: `test-results/screens/${info.project.name}-login.png` })
  await page.getByTestId('login-password').fill('test-password-123')
  await page.keyboard.press('Enter')
  await page.getByTestId('ob-input-0').fill('2.5м')
  await page.waitForTimeout(300)
  await page.screenshot({ path: `test-results/screens/${info.project.name}-onboarding.png`, fullPage: true })
})

// Debts (DEBTS.md §10): Home block, «Счета» → «Долги» + card of a person, Quick Capture debt card.
for (const theme of ['light', 'dark'] as const) {
  test(`debts ${theme}`, async ({ page, request }, info) => {
    await resetMock(request)
    await request.post(`${MOCK}/__test/debts-seed`)
    await page.emulateMedia({ colorScheme: theme })
    await loginAndHome(page)
    const input = page.getByTestId('ai-input')
    await input.fill('коля вернул 400к'); await input.press('Enter')
    await page.getByTestId('home-debts').waitFor()
    await page.waitForTimeout(500)
    await page.screenshot({ path: `test-results/screens/${info.project.name}-debts-home-${theme}.png`, fullPage: true })
    await page.goto('/accounts?debt=all')
    await page.getByTestId('debts-section').waitFor()
    await page.waitForTimeout(500)
    await page.screenshot({ path: `test-results/screens/${info.project.name}-debts-accounts-${theme}.png`, fullPage: true })
    await page.getByTestId('debts-section').getByRole('button', { name: 'Открыть долг: Миша' }).first().click()
    await page.getByTestId('debt-history').waitFor()
    await page.getByTestId('debt-details').getByRole('button', { name: 'Записать возврат' }).click()
    await page.waitForTimeout(500)
    await page.screenshot({ path: `test-results/screens/${info.project.name}-debts-card-${theme}.png` })
  })

  test(`debts quick ${theme}`, async ({ page, request }, info) => {
    await resetMock(request)
    await request.post(`${MOCK}/__test/debts-seed`)
    await page.emulateMedia({ colorScheme: theme })
    await page.setViewportSize({ width: 420, height: 450 })
    await page.addInitScript(() => { (window as unknown as { pocket: unknown }).pocket = { auth: { getToken: async () => 'pfd_test' }, window: {}, platform: 'windows' } })
    await page.goto('/quick')
    const input = page.getByTestId('ai-input')
    await input.fill('коля вернул 400к'); await input.press('Enter')
    await page.getByTestId('card-debt').waitFor()
    await page.waitForTimeout(500)
    await page.screenshot({ path: `test-results/screens/${info.project.name}-debts-quick-${theme}.png` })
    await input.fill('кто мне должен'); await input.press('Enter')
    await page.getByTestId('card-debts').waitFor()
    await page.waitForTimeout(400)
    await page.screenshot({ path: `test-results/screens/${info.project.name}-debts-quick-answer-${theme}.png` })
  })
}
