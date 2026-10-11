import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end tests against the Vite dev server with the API mocked per test
 * (e2e/mock-api.mjs), so they need no backend and spend nothing.
 * `channel: 'chrome'` drives the installed Google Chrome: no browser download.
 */
const PORT = 5174

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    locale: 'en-US',
    timezoneId: 'America/Los_Angeles',
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], channel: 'chrome', viewport: { width: 1440, height: 900 } },
      testIgnore: '**/*.phone.spec.ts',
    },
    {
      name: 'phone',
      use: { ...devices['Desktop Chrome'], channel: 'chrome', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
      testMatch: '**/*.phone.spec.ts',
    },
  ],
  webServer: {
    command: `npm run dev -- --port ${PORT} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
})
