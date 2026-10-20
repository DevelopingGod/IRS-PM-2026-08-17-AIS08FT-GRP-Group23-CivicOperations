// Screenshots of the running app for the group report. Needs the containers up (docker compose up -d)
// and credentials.txt from manage team-accounts. Run from 01_Application:
//   npx --prefix app/frontend playwright test --config app/frontend/tests/screens/report-screens.config.js
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '.',
  testMatch: 'screens.spec.js',
  workers: 1,
  timeout: 120_000,
  reporter: [['list']],
  outputDir: '../.uat/screens-results',
  use: { baseURL: process.env.CIVICOPS_URL || 'http://localhost:8080', viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1.5, colorScheme: 'light', actionTimeout: 20_000 },
})
