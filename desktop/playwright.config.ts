// E2E smoke: real Electron (Playwright _electron) under Xvfb (Linux), against tests/e2e/mock-api.ts.
// Run: pnpm test:e2e  (= electron-vite build && xvfb-run -a -s "-screen 0 1920x1080x24" playwright test)
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: 'tests/e2e',
  testMatch: /.*\.spec\.ts/,
  workers: 1,
  fullyParallel: false,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  retries: 0,
  reporter: [['list']],
  use: { trace: 'retain-on-failure' },
  outputDir: 'test-results',
})
