import { defineConfig } from '@playwright/test'

const live = process.env.BASE_URL
const port = process.env.BACKROOM_E2E_PORT || '5081'
export default defineConfig({
  testDir: './tests/e2e',
  outputDir: './test-results',
  timeout: 60000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  workers: 2,
  globalTeardown: './tests/e2e_teardown.ts',
  reporter: [['list'], ['json', { outputFile: 'test-results/results.json' }]],
  use: { baseURL: live || `http://127.0.0.1:${port}`, browserName: 'chromium', trace: 'retain-on-failure' },
  projects: [
    ...(['light', 'dark'] as const).flatMap(colorScheme => [
      { name: `desktop-${colorScheme}`, use: { colorScheme, viewport: { width: 1440, height: 900 } } },
      { name: `mobile-${colorScheme}`, use: { colorScheme, viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
    ]),
  ],
  webServer: live ? undefined : {
    command: process.platform === 'win32' ? '"..\\.venv\\Scripts\\python.exe" tests/e2e_server.py' : '../.venv/bin/python tests/e2e_server.py',
    url: `http://127.0.0.1:${port}/api/health`,
    timeout: 180000,
    reuseExistingServer: false,
  },
})
