#!/usr/bin/env node
// Screenshots of named UI states (e2e/scenarios.mjs) with a mocked API, in
// light and dark, at desktop (1440×900) and phone (390×844) sizes. No backend,
// no API spend, no browser download: it drives the installed Google Chrome.
//
//   npm run screenshots                         # every scenario
//   node scripts/screenshots.mjs library-empty  # by name
//   node scripts/screenshots.mjs 'library-*' knowledge-populated --theme=dark --width=390
//   SHOTS_DIR=/some/dir node scripts/screenshots.mjs --list
//
// Files: $SHOTS_DIR (default test-results/shots)/<name>-<light|dark>-<1440|390>.png.
// Runs Vite in-process on a free port. Prints console errors per shot and
// exits non-zero if any page logged one.
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'
import { createServer } from 'vite'
import { mockApi } from '../e2e/mock-api.mjs'
import { scenarios } from '../e2e/scenarios.mjs'

const root = fileURLToPath(new URL('..', import.meta.url))
const args = process.argv.slice(2)
const flag = (name) => args.find((a) => a.startsWith(`--${name}=`))?.split('=')[1]
const patterns = args.filter((a) => !a.startsWith('--'))
const outDir = resolve(process.env.SHOTS_DIR || resolve(root, 'test-results/shots'))
const themes = flag('theme') ? [flag('theme')] : ['light', 'dark']
const widthFilter = flag('width') ? Number(flag('width')) : null

const VIEWPORTS = {
  1440: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false },
  390: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
}

const match = (name) =>
  !patterns.length || patterns.some((p) => (p.includes('*') ? new RegExp(`^${p.replaceAll('*', '.*')}$`).test(name) : p === name))
const chosen = scenarios.filter((s) => match(s.name))

if (args.includes('--list')) {
  for (const s of scenarios) console.log(`${s.name.padEnd(28)} ${s.path.padEnd(26)} ${s.note ?? ''}`)
  process.exit(0)
}
if (!chosen.length) {
  console.error(`No scenario matches ${patterns.join(', ')}. Run with --list to see them.`)
  process.exit(2)
}

await import('./copy-pdfjs-assets.mjs')
mkdirSync(outDir, { recursive: true })

const server = await createServer({ root, logLevel: 'error', server: { port: 5190, strictPort: false, host: '127.0.0.1' } })
await server.listen()
const base = (server.resolvedUrls?.local[0] ?? 'http://127.0.0.1:5190/').replace(/\/$/, '')

const browser = await chromium.launch({ channel: 'chrome', headless: true })
let failures = 0
const started = Date.now()

try {
  for (const scenario of chosen) {
    const widths = (scenario.widths ?? [1440, 390]).filter((w) => !widthFilter || w === widthFilter)
    for (const theme of themes) {
      for (const width of widths) {
        const device = VIEWPORTS[width]
        const context = await browser.newContext({
          ...device,
          colorScheme: /** @type {'light' | 'dark'} */ (theme),
          reducedMotion: 'reduce',
          locale: 'en-US',
          timezoneId: 'America/Los_Angeles',
        })
        const page = await context.newPage()
        const errors = []
        page.on('console', (m) => {
          if (m.type() === 'error') errors.push(m.text())
        })
        page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
        // The app's own files must load; /api failures are part of some scenarios.
        page.on('response', (r) => {
          if (r.status() >= 400 && !new URL(r.url()).pathname.startsWith('/api/')) errors.push(`HTTP ${r.status()} ${r.url()}`)
        })
        await mockApi(page, scenario.mock ?? {})
        const file = resolve(outDir, `${scenario.name}-${theme}-${width}.png`)
        try {
          await page.goto(base + scenario.path, { waitUntil: 'load' })
          await page.evaluate(() => document.fonts.ready)
          await page.waitForTimeout(250)
          await scenario.setup?.(page, { width, theme: /** @type {'light' | 'dark'} */ (theme), mobile: device.isMobile })
          await page.waitForTimeout(scenario.settleMs ?? 400)
          await page.evaluate(() => document.fonts.ready)
          if (!(await page.evaluate(() => !!document.getElementById('root')?.childElementCount))) errors.push('blank page: #root is empty')
          const fullPage = scenario.fullPage ?? true
          // A full-page capture of a scrolled page draws sticky and fixed layers mid-page; start from the top.
          if (fullPage) await page.evaluate(() => window.scrollTo(0, 0))
          await page.screenshot({ path: file, fullPage, animations: 'disabled', caret: 'hide' })
          // Network failures the scenario asked for are expected; anything else is a bug.
          const unexpected = errors.filter((e) => !/^Failed to load resource/.test(e))
          if (unexpected.length) {
            failures++
            console.log(`✗ ${file}\n    ${unexpected.join('\n    ')}`)
          } else console.log(`✓ ${file}`)
        } catch (e) {
          failures++
          console.log(`✗ ${scenario.name}-${theme}-${width}: ${e instanceof Error ? e.message.split('\n')[0] : e}`)
        } finally {
          await context.close()
        }
      }
    }
  }
} finally {
  await browser.close()
  await server.close()
}

console.log(`\n${chosen.length} scenario(s) in ${((Date.now() - started) / 1000).toFixed(1)} s → ${outDir}`)
process.exit(failures ? 1 : 0)
