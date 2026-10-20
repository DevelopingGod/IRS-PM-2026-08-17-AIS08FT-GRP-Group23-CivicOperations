// User acceptance and UI tests for every portal, run at phone, tablet and desktop sizes.
//
// Each flow is what a real user does. After every important step the page is audited for the
// problems a person would notice: the page scrolling sideways, anything sticking out of its card,
// text cut off, a card stuck invisible, and buttons too small to tap. Any browser error fails it.
import { expect, test } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const ACCOUNTS = JSON.parse(fs.readFileSync(path.join(import.meta.dirname, '.uat', 'accounts.json'), 'utf-8'))
const SCROLLERS = '.chips, .tablewrap, .chat, .suggest, .segmented, .wizard-steps, .chapters, .reqlist'

// ------------------------------------------------------------------ the audit
async function audit(page, where) {
  await page.waitForTimeout(900)                 // let entrance animations finish
  const issues = await page.evaluate((SCROLLERS) => {
    const out = []
    const W = document.documentElement.clientWidth
    const visible = (e) => {
      const r = e.getBoundingClientRect(); const cs = getComputedStyle(e)
      return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'
    }
    if (document.documentElement.scrollWidth > W + 1) out.push(`the page scrolls sideways (${document.documentElement.scrollWidth}px wide in ${W}px)`)
    const boxes = '.card, .kpi, .comp, .glass, .modal, .drawer, .tile, .queue-item, .reqcard, .facts > div, .minis > div, .btn, .chip, .pill, .ticket, .msg'
    for (const box of document.querySelectorAll(boxes)) {
      if (!visible(box)) continue
      const b = box.getBoundingClientRect()
      for (const ch of box.querySelectorAll('*')) {
        const sc = ch.closest(SCROLLERS)
        if (sc && box.contains(sc) && sc !== box) continue            // inside a row meant to scroll
        if (!visible(ch) || ch.closest('svg') && ch.tagName !== 'svg') continue
        const r = ch.getBoundingClientRect()
        if (r.right > b.right + 2 || r.left < b.left - 2) {
          out.push(`"${(ch.innerText || ch.getAttribute('aria-label') || ch.tagName).trim().slice(0, 40)}" sticks out of ${box.className.split(' ').slice(0, 2).join('.')}`)
          break
        }
      }
    }
    for (const e of document.querySelectorAll('h1, h2, h3, strong, p, button, a, td, th, label, .plate, .pill')) {
      if (!visible(e) || e.closest(SCROLLERS) || e.closest('.sr-only')) continue   // hidden labels for screen readers
      const cs = getComputedStyle(e)
      if ((cs.overflowX === 'hidden' || cs.textOverflow === 'ellipsis') && e.scrollWidth > e.clientWidth + 2) out.push(`text cut off: "${e.innerText.trim().slice(0, 40)}"`)
    }
    for (const e of document.querySelectorAll('.reveal')) {
      const r = e.getBoundingClientRect()
      if (r.width && r.top < innerHeight - 40 && r.bottom > 40 && Number(getComputedStyle(e).opacity) < 0.5) out.push(`on screen but invisible: ${e.className}`)
    }
    for (const e of document.querySelectorAll('button, a.btn, input:not([type=checkbox]), select, textarea, [role=button], [role=switch]')) {
      if (!visible(e) || e.closest('.chapters') || e.closest('.citymap') || e.closest('.chips')) continue
      const r = e.getBoundingClientRect()
      if (r.height < 36 || r.width < 30) out.push(`too small to tap (${Math.round(r.width)}x${Math.round(r.height)}): "${(e.innerText || e.getAttribute('aria-label') || e.placeholder || '').trim().slice(0, 30)}"`)
    }
    return [...new Set(out)]
  }, SCROLLERS)
  if (issues.length) await test.info().attach(`layout issues: ${where}`, { body: issues.join('\n'), contentType: 'text/plain' })
  expect.soft(issues, `layout at "${where}"`).toEqual([])
}

// Every browser error fails the test, except refused calls the test itself provokes.
test.beforeEach(async ({ page }, info) => {
  const errors = []
  page.on('pageerror', (e) => errors.push(`page error: ${e.message}`))
  page.on('console', (m) => {
    if (m.type() === 'error' && !/Failed to load resource: the server responded with a status of 4\d\d/.test(m.text())) errors.push(m.text())
  })
  info.errors_ = errors
})
test.afterEach(async ({}, info) => {
  expect.soft(info.errors_, 'no browser errors').toEqual([])
})

// The landing page renders twice while it loads (the session check), so wait for it to settle
// before scrolling to the question at the end.
async function openQuestion(page) {
  await page.goto('/#/')
  await page.waitForLoadState('networkidle')
  await expect(async () => {
    await page.locator('[data-chapter="7"] .glass').scrollIntoViewIfNeeded()
    await page.getByRole('button', { name: /Yes, report it/ }).click({ timeout: 3000 })
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 3000 })
  }).toPass({ timeout: 30_000 })
}

const unique = () => `uat.${Date.now()}.${Math.floor(Math.random() * 1e6)}@example.org`

async function register(page) {
  await openQuestion(page)
  const modal = page.getByRole('dialog')
  await modal.getByLabel('Email').fill(unique())
  await modal.getByRole('button', { name: 'Create my user ID' }).click()
  const id = (await modal.locator('.ticket-ref').innerText()).trim()
  expect(id).toMatch(/^NYC-[A-Z0-9]{4}-[A-Z0-9]{4}$/)
  await audit(page, 'user ID screen')
  await expect(modal.getByRole('button', { name: /Continue/ })).toBeDisabled()
  await modal.getByLabel('I have saved my user ID').check()
  await modal.getByRole('button', { name: /Continue/ }).click()
  await expect(page).toHaveURL(/#\/report/)
  return id
}

async function workSignIn(page, who) {
  await page.goto('/#/work')
  await page.getByLabel('User ID').fill(ACCOUNTS[who].id)
  await page.getByLabel('PIN').fill(ACCOUNTS[who].pin)
  await page.locator('#content').getByRole('button', { name: 'Sign in' }).click()
}

// ------------------------------------------------------------------ residents
test('landing: the story, the 3D city and the question at the end', async ({ page }) => {
  await page.goto('/#/')
  await expect(page.getByRole('heading', { name: /Every report heard/ })).toBeVisible()
  await expect(page.locator('.city3d canvas, .citymap')).toHaveCount(1, { timeout: 20_000 })
  await audit(page, 'landing, top')
  for (let i = 1; i <= 7; i++) {
    await page.locator(`[data-chapter="${i}"]`).scrollIntoViewIfNeeded()
    await expect(page.locator(`[data-chapter="${i}"] .glass`)).toBeVisible()
  }
  await audit(page, 'landing, the question')
  await expect(page.locator('footer')).toContainText('Sankalp Sandeep Indish')
  await expect(page.locator('footer')).toContainText('2026')
  await expect(page.getByRole('navigation', { name: 'Main' })).not.toContainText('City staff')
})

test('resident: register, report with the wizard, track, talk to the bot', async ({ page }) => {
  await register(page)
  await audit(page, 'report, step 1')
  await page.locator('.tile', { hasText: 'Streets and lights' }).click()
  await page.getByRole('button', { name: 'Pothole', exact: true }).click()
  await expect(page.getByLabel('Street address')).toBeVisible()
  await page.getByLabel('Street address').fill('37-61 79')
  await page.locator('.suggest button:not([disabled])', { hasText: 'Queens' }).first().click()
  await expect(page.locator('.placecard')).toBeVisible()
  await audit(page, 'report, place chosen')
  const area = page.locator('textarea')
  if (!(await area.isVisible())) await page.getByRole('button', { name: /Next/ }).click()
  await page.locator('textarea').fill('About a foot wide near the crossing')
  await page.getByRole('button', { name: /Next/ }).click()
  await expect(page.locator('.review')).toBeVisible()
  await audit(page, 'report, review')
  await page.getByRole('button', { name: /Send to the city/ }).click()
  const danger = page.getByRole('button', { name: /No, nobody/ })
  if (await danger.isVisible({ timeout: 3000 }).catch(() => false)) await danger.click()
  await expect(page).toHaveURL(/#\/track\/CO-[0-9A-F]{6}/, { timeout: 30_000 })
  await expect(page.locator('.ticket-ref')).toHaveText(/CO-[0-9A-F]{6}/)
  await expect(page.locator('.journey li')).toHaveCount(6)
  await audit(page, 'track, after filing')
  await page.getByLabel('Your question').fill('where is my request?')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.locator('.track-chat .msg.bot').last()).toContainText('queue', { timeout: 20_000 })
  await page.locator('.track-chat .chip', { hasText: 'Show all my requests' }).click()
  await expect(page.locator('.track-chat .msg.bot').last()).toContainText('You have 1 request')
  await audit(page, 'track, conversation')
})

test('resident: an emergency stops everything and fills the screen', async ({ page }) => {
  await register(page)
  await page.getByRole('tab', { name: /Just tell us/ }).click()
  await page.getByLabel('Your message').fill('I can smell gas in the whole building and my neighbour fainted')
  await page.getByRole('button', { name: 'Send' }).click()
  const stop = page.getByRole('alertdialog')
  await expect(stop).toBeVisible({ timeout: 20_000 })
  const box = await stop.boundingBox()
  const vp = page.viewportSize()
  expect(box.width).toBeGreaterThanOrEqual(vp.width - 1)
  expect(box.height).toBeGreaterThanOrEqual(vp.height - 1)
  await expect(stop).toContainText('Call 911 now')
  await audit(page, 'emergency stop')
  await stop.getByRole('button', { name: /I am safe/ }).click()
  await expect(stop).toBeHidden()
})

test('resident: sign out, sign back in with the saved ID, and stay out of staff pages', async ({ page }) => {
  const id = await register(page)
  await page.getByRole('button', { name: /Sign out/ }).click()
  await page.goto('/#/track')
  const modal = page.getByRole('dialog')
  await expect(modal).toBeVisible()
  await modal.getByRole('tab', { name: 'I have a user ID' }).click()
  await modal.getByLabel('User ID').fill(id.toLowerCase())
  await modal.getByRole('button', { name: 'Sign in' }).click()
  await expect(page).toHaveURL(/#\/track/)
  await expect(page.locator('.idchip')).toContainText(id)
  await page.goto('/#/staff/queue')
  await expect(page).not.toHaveURL(/#\/staff/)
  await page.goto('/#/tech/components')
  await expect(page).not.toHaveURL(/#\/tech/)
})

test('a second registration with the same email is refused without showing the ID', async ({ page }) => {
  const email = unique()
  for (const attempt of [1, 2]) {
    await openQuestion(page)
    await page.getByRole('dialog').getByLabel('Email').fill(email)
    await page.getByRole('dialog').getByRole('button', { name: 'Create my user ID' }).click()
    if (attempt === 1) {
      await page.getByRole('dialog').getByLabel('I have saved my user ID').check()
      await page.getByRole('dialog').getByRole('button', { name: /Continue/ }).click()
      await page.getByRole('button', { name: /Sign out/ }).click()
    }
  }
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('already has a user ID')
  await expect(page.getByRole('dialog').locator('.ticket-ref')).toHaveCount(0)
})

// ------------------------------------------------------------------ city staff
test('staff: every headline number filters and stays visible, the drawer, notes', async ({ page }) => {
  await workSignIn(page, 'staff')
  await expect(page).toHaveURL(/#\/staff\/queue/)
  await expect(page.locator('.queue-item').first()).toBeVisible({ timeout: 30_000 })
  await audit(page, 'staff queue')
  const kpis = page.locator('.kpi')
  const n = await kpis.count()
  expect(n).toBe(6)
  for (let i = 0; i < n; i++) {
    await kpis.nth(i).click()
    await page.waitForTimeout(400)
    for (let j = 0; j < n; j++) {
      await expect(kpis.nth(j), `headline card ${j + 1} after clicking card ${i + 1}`).toHaveCSS('opacity', '1')
    }
  }
  await kpis.first().click()
  await audit(page, 'staff queue after filtering')
  await page.locator('.queue-item').first().click()
  const drawer = page.getByRole('dialog', { name: 'Request detail' })
  await expect(drawer).toBeVisible()
  await audit(page, 'request drawer, overview')
  await drawer.getByRole('tab', { name: /Why/ }).click()
  await expect(drawer.locator('.journey li').first()).toBeVisible()
  await audit(page, 'request drawer, why')
  await drawer.getByRole('tab', { name: /Notes/ }).click()
  await drawer.getByLabel('Note').fill('Checked by the UAT run')
  await drawer.getByRole('button', { name: 'Add' }).click()
  await expect(drawer).toContainText('Checked by the UAT run')
  await drawer.getByRole('button', { name: 'Close' }).click()
  await expect(drawer).toBeHidden()
  await page.getByLabel('Search the queue').fill('Noise')        // the real backlog always has noise reports
  await expect(page.locator('.queue-item').first()).toContainText('Noise')
})

test('staff: a dot on the map opens its request', async ({ page }) => {
  await workSignIn(page, 'staff')
  const dot = page.locator('.citymap .pt[role="button"]').first()
  await expect(dot).toBeVisible({ timeout: 30_000 })
  await dot.click({ force: true })      // the dots pulse forever, so they never look still to Playwright
  await expect(page.getByRole('dialog', { name: 'Request detail' })).toBeVisible()
})

test('staff: Admin questions answers as a conversation with follow ups', async ({ page }) => {
  await workSignIn(page, 'staff')
  await page.getByRole('tab', { name: /Admin questions/ }).click()
  await audit(page, 'admin questions, empty')
  await page.locator('.starter', { hasText: 'Which agency' }).click()
  const answer = page.locator('.msg.answer').last()
  await expect(answer).toContainText('Top', { timeout: 20_000 })
  await expect(answer.locator('table')).toBeVisible({ timeout: 10_000 })
  await expect(answer).not.toContainText('ETA')
  await audit(page, 'admin questions, answered')
  await answer.locator('.chip').first().click()
  await expect(page.locator('.msg.answer')).toHaveCount(2, { timeout: 20_000 })
  await page.getByLabel('Question').fill('delete all requests')
  await page.getByRole('button', { name: 'Ask' }).click()
  await expect(page.locator('.msg.answer').last()).toContainText('only read', { timeout: 20_000 })
})

test('staff: busy day alerts for a recorded storm', async ({ page }) => {
  await workSignIn(page, 'staff')
  await page.getByRole('tab', { name: /Busy day alerts/ }).click()
  await page.getByLabel('Borough').selectOption('QUEENS')
  await page.locator('.chip', { hasText: 'mm rain' }).first().click()
  await expect(page.locator('.alertrow, .card:has-text("normal day")').first()).toBeVisible({ timeout: 20_000 })
  await audit(page, 'busy day alerts')
})

test('staff: crews get their routes automatically, shown on a street map, and a job can be pulled back', async ({ page }, info) => {
  await workSignIn(page, 'staff')
  await page.getByRole('tab', { name: /Crew routes/ }).click()
  await expect(page.locator('.kpi', { hasText: 'Jobs with a crew' })).toBeVisible({ timeout: 40_000 })
  await expect(page.getByText('Assigned automatically.')).toBeVisible()
  await audit(page, 'crew routes')
  const first = page.locator('.job').filter({ hasText: 'sent to the crew' }).first()
  await expect(first).toBeVisible({ timeout: 40_000 })
  const crewId = (await first.locator('.plate').first().textContent()).trim()
  await first.locator('.jobhead').click()
  await expect(first.locator('.citymap.zoomed')).toBeVisible()
  await expect(first.locator('.citymap .street').first()).toBeAttached({ timeout: 15_000 })
  await expect(first.locator('.citymap .depotc')).toBeAttached()
  await audit(page, 'crew routes, a route open on its streets')
  if (info.project.name !== 'desktop') return          // keep the jobs for the other screen sizes' tests
  await first.getByRole('button', { name: 'Pull back' }).first().click()
  await expect(page.locator('.toast').last()).toContainText(`pulled back from ${crewId}`)
})

// ------------------------------------------------------------------ crews
test('crew: own jobs in order, close one with a confirmation', async ({ page }) => {
  await workSignIn(page, 'crew')
  await expect(page).toHaveURL(/#\/crew/)
  await expect(page.locator('.job').first()).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('a[href*="google"]')).toHaveCount(0)
  await audit(page, 'crew jobs')
  const before = await page.locator('.job').count()
  await page.locator('.jobhead').first().click()
  await page.getByRole('button', { name: 'Fixed, the problem was there' }).click()
  const confirm = page.getByRole('dialog')
  await expect(confirm).toBeVisible()
  await audit(page, 'crew, confirm close')
  await confirm.getByRole('button', { name: /Yes, close it/ }).click()
  await expect(page.locator('.done-card').first()).toBeVisible()
  await expect(page.locator('.job')).toHaveCount(before - 1)
})

// ------------------------------------------------------------------ tech team
test('tech: component cards fit, a switch reaches staff, details open', async ({ page, browser }) => {
  await workSignIn(page, 'tech')
  await expect(page).toHaveURL(/#\/tech\/components/)
  await expect(page.locator('.comp')).toHaveCount(10)
  await audit(page, 'tech components')
  const card = page.locator('.comp', { has: page.locator('h3', { hasText: /^Admin questions$/ }) })
  await card.getByRole('switch').click()
  await expect(card.locator('.pill').first()).toHaveText('Switched off')
  const other = await browser.newContext()
  const staff = await other.newPage()
  await workSignIn(staff, 'staff')
  await staff.getByRole('tab', { name: /Admin questions/ }).click()
  await staff.locator('.starter').first().click()
  await expect(staff.locator('.msg.answer').last()).toContainText('switched off', { timeout: 20_000 })
  await other.close()
  await card.getByRole('switch').click()
  await expect(card.locator('.pill').first()).toHaveText('Live')
  const safety = page.locator('.comp', { has: page.locator('h3', { hasText: 'Emergency check' }) })
  await expect(safety.getByRole('switch')).toBeDisabled()
  await card.getByRole('button', { name: 'Details' }).click()
  await expect(page.getByRole('dialog')).toContainText('Safe default')
  await audit(page, 'component details')
})

test('tech: create an account, see its PIN once, give a new PIN, switch it off', async ({ page }) => {
  await workSignIn(page, 'tech')
  await page.getByRole('tab', { name: /Accounts/ }).click()
  const name = `UAT staff ${Date.now()}`
  await page.getByLabel('Name').fill(name)
  await page.getByRole('button', { name: 'Create' }).click()
  const modal = page.getByRole('dialog')
  await expect(modal).toContainText('Shown once')
  await expect(modal.locator('.facts strong').nth(1)).toHaveText(/^\d{6}$/)
  await audit(page, 'new credentials')
  await modal.getByRole('button', { name: 'Done' }).click()
  const row = page.locator('tbody tr', { hasText: name })
  await row.getByRole('button', { name: 'New PIN' }).click()
  await expect(page.getByRole('dialog')).toContainText('Shown once')
  await page.getByRole('dialog').getByRole('button', { name: 'Done' }).click()
  await row.getByRole('button', { name: 'Switch off' }).click()
  await expect(row).toContainText('Off')
  await audit(page, 'accounts')
  await page.getByRole('tab', { name: /System/ }).click()
  await expect(page.locator('main')).toContainText('How each component was chosen')
  const card = page.locator('.expcard').first()
  await card.getByRole('button', { name: 'Show every method compared' }).click()
  await expect(card.locator('.explist li.shipped').first()).toBeVisible()
  await page.mouse.move(0, 0)            // the button's hover shine is clipped, but would count as overflow
  await audit(page, 'system')
})

test('work sign in: a wrong PIN is refused, and residents cannot use it', async ({ page }) => {
  await page.goto('/#/work')
  await page.getByLabel('User ID').fill(ACCOUNTS.staff.id)
  await page.getByLabel('PIN').fill('000000')
  await page.locator('#content').getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toContainText(/do not match|locked/)
  await audit(page, 'work sign in, refused')
})

test('staff: each card says why the AI ranked it, and a school can be confirmed', async ({ page }) => {
  await workSignIn(page, 'staff')
  await expect(page.locator('.queue-item').first()).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('.queue-item .aiwhy').first()).toContainText('Ranked 1 by AI')
  await page.locator('.kpi', { hasText: 'Near a school or hospital' }).click()
  await page.locator('.queue-item').first().click()
  const drawer = page.getByRole('dialog', { name: 'Request detail' })
  await expect(drawer).toContainText('External factors found a')
  const confirm = drawer.getByRole('button', { name: /Confirm it affects the/ })
  if (await confirm.count()) await confirm.click()      // an earlier screen size may have confirmed it already
  await expect(drawer).toContainText('Confirmed, so Prioritisation counts it.')
  await audit(page, 'request drawer, impact confirmed')
})

test('staff: the queue map zooms into a borough with its real streets', async ({ page }) => {
  await workSignIn(page, 'staff')
  await expect(page.locator('.queue-item').first()).toBeVisible({ timeout: 30_000 })
  await page.locator('.mapzoom').getByRole('button', { name: 'Brooklyn' }).click()
  await expect(page.locator('.mapcard .citymap.zoomed')).toBeVisible()
  await expect(page.locator('.mapcard .citymap .street').first()).toBeAttached({ timeout: 15_000 })
  await audit(page, 'queue map, Brooklyn')
  await page.locator('.mapzoom').getByRole('button', { name: 'All of NYC' }).click()
  await expect(page.locator('.mapcard .citymap.zoomed')).toHaveCount(0)
})

test('everyone: the Features page says what the product does', async ({ page }) => {
  await page.goto('/#/features')
  await expect(page.locator('main')).toContainText('The twelve components')
  await expect(page.locator('.part')).toHaveCount(12)
  await audit(page, 'features')
})
