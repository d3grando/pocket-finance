// Playwright smoke: production build served by `vite preview` on :5201,
// /api proxied to the mock API (e2e/mock-api.mjs) on :15201. E2E_BASE_URL=… skips both servers
// to run against a live server.
import { defineConfig, devices } from '@playwright/test'

const external = process.env.E2E_BASE_URL
const MOCK_PORT = 15201

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 7_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: external ?? 'http://127.0.0.1:5201',
    locale: 'ru-RU',
    timezoneId: 'Asia/Ho_Chi_Minh',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } }, testIgnore: /mobile\.spec/ },
    { name: 'mobile', use: { ...devices['Pixel 7'], viewport: { width: 390, height: 844 } }, testMatch: /(mobile|screens)\.spec/ },
  ],
  webServer: external ? undefined : [
    { command: `node e2e/mock-api.mjs ${MOCK_PORT}`, port: MOCK_PORT, reuseExistingServer: false, timeout: 15_000 },
    {
      command: 'pnpm exec vite build --logLevel warn && pnpm exec vite preview --host 127.0.0.1 --port 5201 --strictPort',
      port: 5201,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { VITE_PROXY_API: `http://127.0.0.1:${MOCK_PORT}` },
    },
  ],
})
