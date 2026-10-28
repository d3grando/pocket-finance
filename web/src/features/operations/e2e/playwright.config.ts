// Playwright smoke for the pages (operations / analytics / accounts / settings) on :5202.
// Run:  cd web && pnpm exec playwright test -c src/features/operations/e2e/playwright.config.ts
// The API is mocked per test with page.route (contract-shaped fixtures, docs/API.md).
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.ts$/,
  timeout: 45_000,
  expect: { timeout: 8_000 },
  fullyParallel: true,
  workers: 2,
  retries: 0,
  reporter: [['list']],
  outputDir: '../../../../test-results/web-b',
  use: {
    baseURL: 'http://127.0.0.1:5202',
    locale: 'ru-RU',
    timezoneId: 'Asia/Ho_Chi_Minh',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', use: { ...devices['Pixel 7'], viewport: { width: 390, height: 844 } } },
  ],
  webServer: {
    command: 'pnpm exec vite --config src/features/operations/e2e/vite.e2e.config.mjs',
    cwd: '../../../..',
    url: 'http://127.0.0.1:5202/',
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
