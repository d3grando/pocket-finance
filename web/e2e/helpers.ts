import { expect, type APIRequestContext, type Page } from '@playwright/test'

export const MOCK = 'http://127.0.0.1:15201'
export const PASSWORD = 'test-password-123'

export async function resetMock(request: APIRequestContext, { onboarded = true } = {}) {
  await request.post(`${MOCK}/__test/reset`)
  if (onboarded) await request.post(`${MOCK}/__test/onboarded`)
}

export async function mockState(request: APIRequestContext) {
  return (await (await request.get(`${MOCK}/__test/state`)).json()) as {
    txs: { id: number; amount: string; status: string; account: { id: number } | null }[]
    calls: { method: string; path: string; idem: string | null; auth: string | null }[]
    onboarding_done: boolean
    deviceLogins: { device_name: string; platform: string }[]
  }
}

export async function login(page: Page) {
  await page.goto('/login')
  await page.getByTestId('login-password').fill(PASSWORD)
  await page.getByTestId('login-submit').click()
}

export async function loginAndHome(page: Page) {
  await login(page)
  await expect(page.getByTestId('kpi-tiles')).toBeVisible()
}
