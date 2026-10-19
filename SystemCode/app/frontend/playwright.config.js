// User acceptance and UI tests: every portal, every role, at phone, tablet and desktop sizes.
// The run starts its own backend on a separate test database, so real data is never touched.
//   npx playwright test            (from app/frontend)
//   npx playwright show-report     (the HTML report, with a screenshot of every failure)
import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'

const ROOT = path.resolve(import.meta.dirname, '..', '..')
const DB = path.join(import.meta.dirname, 'tests', '.uat', 'uat.db')
const API = 'http://127.0.0.1:8011'
const WEB = 'http://127.0.0.1:5181'

export default defineConfig({
  testDir: './tests', testMatch: 'uat.spec.js',   // the report screenshots have their own config
  globalSetup: './tests/global-setup.js',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'tests/.uat/report' }]],
  outputDir: 'tests/.uat/results',
  use: { baseURL: WEB, trace: 'retain-on-failure', screenshot: 'only-on-failure', actionTimeout: 15_000 },
  projects: [
    { name: 'phone', use: { ...devices['Pixel 7'], viewport: { width: 375, height: 812 } } },
    { name: 'tablet', use: { viewport: { width: 768, height: 1024 }, hasTouch: true } },
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
  ],
  webServer: [
    {
      command: 'py -m uvicorn app.backend.main:app --port 8011',
      cwd: ROOT,
      url: `${API}/api/catalog`,
      timeout: 240_000,
      reuseExistingServer: false,
      env: { ...process.env, CIVICOPS_DB: DB, CIVICOPS_ORIGINS: WEB },
    },
    {
      command: 'npx vite --port 5181 --strictPort --host 127.0.0.1',
      url: WEB,
      timeout: 120_000,
      reuseExistingServer: false,
      env: { ...process.env, CIVICOPS_API: API },
    },
  ],
})
