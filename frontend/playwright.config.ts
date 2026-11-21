import { defineConfig, devices } from '@playwright/test';

const existing = process.env['STUDIO_E2E_REUSE'] === '1';

export default defineConfig({
  testDir: './e2e',
  timeout: 45000,
  workers: 1,
  use: {
    baseURL: 'http://localhost:4200',
    ...devices['Desktop Chrome'],
    channel: process.env['STUDIO_BROWSER_CHANNEL'] || undefined,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'python ../scripts/dev.py --test',
    url: 'http://localhost:4200',
    timeout: 120000,
    reuseExistingServer: existing,
  },
});
