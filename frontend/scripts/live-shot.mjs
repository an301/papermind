// Ad-hoc screenshots of the running app (dev server + real API). Usage:
//   node scripts/live-shot.mjs <path> <out.png> [width] [height] [actions-json]
import { chromium } from '@playwright/test'

const [, , path = '/', out = 'shot.png', w = '1440', h = '900', actions = '[]'] = process.argv
// A persistent profile, so the anonymous user and their sessions survive between shots.
const profile = process.env.PM_PROFILE ?? '/tmp/papermind-shot-profile'
const browser = await chromium.launchPersistentContext(profile, {
  channel: 'chrome',
  viewport: { width: Number(w), height: Number(h) },
  deviceScaleFactor: 2,
  colorScheme: process.env.PM_SCHEME ?? 'light',
})
const page = browser.pages()[0] ?? (await browser.newPage())
page.on('console', (m) => { if (m.type() === 'error') console.log('console error:', m.text()) })
page.on('pageerror', (e) => console.log('page error:', e.message))
await page.goto(`http://localhost:5173${path}`, { waitUntil: 'networkidle' })
for (const a of JSON.parse(actions)) {
  if (a.wait) await page.waitForTimeout(a.wait)
  if (a.click) await page.click(a.click)
  if (a.fill) await page.fill(a.fill[0], a.fill[1])
  if (a.press) await page.keyboard.press(a.press)
  if (a.waitFor) await page.waitForSelector(a.waitFor, { timeout: a.timeout ?? 60000 })
  if (a.eval) await page.evaluate(a.eval)
}
await page.waitForTimeout(1500)
await page.screenshot({ path: out })
await browser.close()
console.log('saved', out)
