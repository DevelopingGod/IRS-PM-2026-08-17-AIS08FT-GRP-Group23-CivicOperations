// Captures the screens the group report shows, from the running app with its real backlog.
import { expect, test } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const ROOT = path.resolve(import.meta.dirname, '..', '..', '..', '..', '..')
const OUT = path.join(ROOT, '03_Research', 'evidence', 'report', 'screens')
const SHEET = path.join(ROOT, '01_Application', 'credentials.txt')
fs.mkdirSync(OUT, { recursive: true })

const raw = fs.readFileSync(SHEET).toString(fs.readFileSync(SHEET)[0] === 0xff ? 'utf16le' : 'utf8')
const rows = raw.split(/\r?\n/).filter((l) => /^(tech|staff|crew) /.test(l)).map((l) => l.trim().split(/\s+/))
const account = (role) => rows.find((r) => r[0] === role)
const shot = (page, name, opts = {}) => page.screenshot({ path: path.join(OUT, `${name}.png`), ...opts })
const settle = (page) => page.waitForTimeout(1600)

async function workSignIn(page, role) {
  const [, id, pin] = account(role)
  await page.goto('/#/work')
  await page.getByLabel('User ID').fill(id)
  await page.getByLabel('PIN').fill(pin)
  await page.locator('#content').getByRole('button', { name: 'Sign in' }).click()
}

async function register(page) {
  await page.goto('/#/')
  await page.waitForLoadState('networkidle')
  await expect(async () => {
    await page.locator('[data-chapter="7"] .glass').scrollIntoViewIfNeeded()
    await page.getByRole('button', { name: /Yes, report it/ }).click({ timeout: 3000 })
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 3000 })
  }).toPass({ timeout: 30_000 })
  const modal = page.getByRole('dialog')
  await modal.getByLabel('Email').fill(`report.${Date.now()}@example.org`)
  await modal.getByRole('button', { name: 'Create my user ID' }).click()
  await modal.getByLabel('I have saved my user ID').check()
  await modal.getByRole('button', { name: /Continue/ }).click()
  await expect(page).toHaveURL(/#\/report/)
}

test('landing', async ({ page }) => {
  await page.goto('/#/')
  await expect(page.locator('.city3d canvas, .citymap')).toHaveCount(1, { timeout: 20_000 })
  await page.waitForTimeout(3500)
  await shot(page, 's01_landing')
})

test('resident report, track and the status bot', async ({ page }) => {
  await register(page)
  await page.locator('.tile', { hasText: 'Streets and lights' }).click()
  await page.getByRole('button', { name: 'Pothole', exact: true }).click()
  await page.getByLabel('Street address').fill('37-61 79')
  await page.locator('.suggest button:not([disabled])', { hasText: 'Queens' }).first().click()
  await expect(page.locator('.placecard')).toBeVisible()
  await settle(page); await shot(page, 's02_report_wizard')
  const area = page.locator('textarea')
  if (!(await area.isVisible())) await page.getByRole('button', { name: /Next/ }).click()
  await page.locator('textarea').fill('About a foot wide near the crossing')
  await page.getByRole('button', { name: /Next/ }).click()
  await page.getByRole('button', { name: /Send to the city/ }).click()
  const danger = page.getByRole('button', { name: /No, nobody/ })
  if (await danger.isVisible({ timeout: 3000 }).catch(() => false)) await danger.click()
  await expect(page).toHaveURL(/#\/track\/CO-/, { timeout: 30_000 })
  await page.getByLabel('Your question').fill('where is my request?')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.locator('.track-chat .msg.bot').last()).toContainText('queue', { timeout: 20_000 })
  await settle(page); await shot(page, 's03_track')
})

test('resident chat intake and the emergency stop', async ({ page }) => {
  await register(page)
  await page.getByRole('tab', { name: /Just tell us/ }).click()
  await page.getByLabel('Your message').fill('theres a big pothole on atlantic ave near 4th ave in brooklyn')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.locator('.msg').last()).toBeVisible({ timeout: 20_000 })
  await page.waitForTimeout(2500)
  await shot(page, 's04_chat_intake')
})

test('resident emergency stop', async ({ page }) => {
  await register(page)
  await page.getByRole('tab', { name: /Just tell us/ }).click()
  await page.getByLabel('Your message').fill('I can smell gas in the whole building and my neighbour fainted')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByRole('alertdialog')).toBeVisible({ timeout: 20_000 })
  await settle(page); await shot(page, 's05_emergency_stop')
})

test('city staff: queue, request reasoning, crew routes', async ({ page }) => {
  await workSignIn(page, 'staff')
  await expect(page.locator('.queue-item .aiwhy').first()).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 15_000 })   // the sign in message has gone
  await settle(page); await shot(page, 's06_staff_queue')
  await page.locator('.queue-item').first().click()
  const drawer = page.getByRole('dialog', { name: 'Request detail' })
  await drawer.getByRole('tab', { name: /Why/ }).click()
  await expect(drawer.locator('.journey li').first()).toBeVisible()
  await settle(page); await shot(page, 's07_request_why')
  await drawer.getByRole('button', { name: 'Close' }).click()
  await page.getByRole('tab', { name: /Crew routes/ }).click()
  await expect(page.locator('.job').filter({ hasText: 'sent to the crew' }).first()).toBeVisible({ timeout: 60_000 })
  // The crew with the longest route shows the planner best.
  const counts = await page.locator('.job .jobhead .bullet').allInnerTexts()
  const best = counts.map(Number).reduce((b, n, i, a) => (n > a[b] ? i : b), 0)
  const first = page.locator('.job').nth(best)
  await first.locator('.jobhead').click()
  await expect(first.locator('.citymap .road').first()).toBeAttached({ timeout: 15_000 })
  await expect(first.locator('.citymap .street').first()).toBeAttached({ timeout: 15_000 })
  await page.waitForTimeout(4000)
  await first.scrollIntoViewIfNeeded()
  await shot(page, 's08_crew_plan')
})

test('tech team components', async ({ page }) => {
  await workSignIn(page, 'tech')
  await expect(page.locator('main')).toContainText('Crew deployment', { timeout: 30_000 })
  await settle(page); await shot(page, 's09_tech_components')
})
