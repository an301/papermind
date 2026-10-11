/**
 * Named UI states for scripts/screenshots.mjs. Each one is a route, the mock
 * API's options (see ./mock-api.mjs) and an optional setup that puts the page
 * into the state (typing, dragging, opening a menu).
 *
 * Append new states here; keep names stable, they become file names:
 * <name>-<light|dark>-<1440|390>.png.
 *
 * @typedef {object} ScenarioContext
 * @property {number} width
 * @property {'light' | 'dark'} theme
 * @property {boolean} mobile
 *
 * @typedef {object} Scenario
 * @property {string} name
 * @property {string} path
 * @property {import('./mock-api.mjs').MockOptions} [mock]
 * @property {(page: import('@playwright/test').Page, ctx: ScenarioContext) => Promise<void>} [setup]
 * @property {boolean} [fullPage]   default true
 * @property {number} [settleMs]    wait after setup, default 400
 * @property {number[]} [widths]    default [1440, 390]
 * @property {string} [note]        what a reviewer should look at
 */
import { fixture } from './mock-api.mjs'

const ready = () => fixture('papers').filter((p) => p.status === 'ready')
const ADD_FIELD = 'Paste an arXiv link or ID'

/** Fire window-level drag events carrying files, the way the browser does when a file is dragged in. */
async function dragFilesOver(page, files) {
  await page.evaluate((list) => {
    const dt = new DataTransfer()
    for (const f of list) dt.items.add(new File(['%PDF-1.7\n'], f.name, { type: f.type }))
    const target = document.querySelector('main') ?? document.body
    target.dispatchEvent(new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer: dt }))
    target.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt }))
  }, files)
}

/** @type {Scenario[]} */
export const scenarios = [
  // ---------------------------------------------------------------- Library
  { name: 'library-empty', path: '/', mock: { papers: 'papers-empty' }, note: 'First run: the three lines only (§4.3)' },
  { name: 'library-populated', path: '/', mock: { papers: ready() }, note: 'Ready papers, page strips, "You stopped at"' },
  { name: 'library-mixed', path: '/', mock: { papers: 'papers' }, note: 'One indexing, one failed upload, the rest ready' },
  { name: 'library-processing', path: '/', mock: { papers: 'papers-ingesting' }, note: 'Queued, downloading (no metadata yet), sectioning, indexing 140 of 212' },
  { name: 'library-failed', path: '/', mock: { papers: 'papers-failed' }, note: 'Failed rows with reasons, Try again and Remove' },
  { name: 'library-many', path: '/', mock: { papers: 'papers-many' }, note: '14 papers: filter field appears; a 200-character title clamps to 2 lines' },
  {
    name: 'library-filter-empty',
    path: '/',
    mock: { papers: 'papers-many' },
    setup: async (page) => {
      await page.getByPlaceholder('Filter by title or author').fill('diffusion transformers for proteins')
    },
  },
  {
    name: 'library-adding',
    path: '/',
    mock: { papers: ready(), delay: { 'POST /papers': Infinity } },
    note: 'Busy button after Enter',
    setup: async (page) => {
      const field = page.getByPlaceholder(ADD_FIELD)
      await field.fill('https://arxiv.org/abs/2006.11239')
      await field.press('Enter')
    },
    settleMs: 700,
    fullPage: false,
  },
  {
    name: 'library-added',
    path: '/',
    mock: { papers: ready(), ingest: 'advance' },
    note: 'The new row at the top, filling in as stages finish (§6.1)',
    setup: async (page) => {
      const field = page.getByPlaceholder(ADD_FIELD)
      await field.fill('2006.11239')
      await field.press('Enter')
      await page.waitForTimeout(3600) // a couple of polls: metadata arrives, steps tick
    },
    fullPage: false,
  },
  {
    name: 'library-invalid',
    path: '/',
    mock: { papers: ready() },
    setup: async (page) => {
      const field = page.getByPlaceholder(ADD_FIELD)
      await field.fill('attention is all you need')
      await field.press('Enter')
    },
    fullPage: false,
  },
  {
    name: 'library-already-added',
    path: '/',
    mock: { papers: ready() },
    setup: async (page) => {
      const field = page.getByPlaceholder(ADD_FIELD)
      await field.fill('arXiv:1706.03762v7')
      await field.press('Enter')
    },
    fullPage: false,
  },
  {
    name: 'library-dragover',
    path: '/',
    mock: { papers: ready() },
    setup: (page) => dragFilesOver(page, [{ name: 'attention.pdf', type: 'application/pdf' }]),
    fullPage: false,
  },
  {
    name: 'library-dragover-not-pdf',
    path: '/',
    mock: { papers: ready() },
    setup: (page) => dragFilesOver(page, [{ name: 'notes.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }]),
    fullPage: false,
    widths: [1440],
  },
  { name: 'library-loading', path: '/', mock: { delay: { 'GET /papers': Infinity } }, settleMs: 700, fullPage: false },
  { name: 'library-error', path: '/', mock: { fail: { 'GET /papers': { status: 500, code: 'internal', message: 'Redis is down.' } } }, settleMs: 900 },
  { name: 'library-server-down', path: '/', mock: { health: 'down' }, settleMs: 1500 },
  {
    name: 'library-offline',
    path: '/',
    mock: { papers: ready() },
    setup: async (page) => {
      await page.evaluate(() => {
        Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => false })
        window.dispatchEvent(new Event('offline'))
      })
    },
    fullPage: false,
  },
  { name: 'library-no-key', path: '/', mock: { papers: ready(), health: 'health-no-key' }, settleMs: 800, fullPage: false },
  {
    name: 'library-row-menu',
    path: '/',
    mock: { papers: ready() },
    setup: async (page) => {
      await page.getByRole('button', { name: /^More for Attention Is All You Need/ }).click()
    },
    fullPage: false,
    widths: [1440],
  },

  // ---------------------------------------------------------------- Onboarding (in the panel, first paper ever, §4.6)
  { name: 'onboarding', path: '/paper/1706.03762', mock: { user: 'user-new', papers: ready() }, fullPage: false },
  {
    name: 'onboarding-areas',
    path: '/paper/1706.03762',
    mock: { user: 'user-new', papers: ready() },
    setup: async (page) => {
      await page.getByRole('button', { name: 'Continue' }).click()
    },
    fullPage: false,
  },

  {
    name: 'settings-onboarding',
    path: '/settings',
    mock: { user: 'user' },
    note: 'The first-run questions re-opened inline from Settings',
    setup: async (page) => {
      await page.getByRole('button', { name: /starting questions again/i }).click()
    },
  },

  // ---------------------------------------------------------------- What you know
  { name: 'knowledge-empty', path: '/know', mock: { knowledge: 'knowledge-empty' } },
  { name: 'knowledge-populated', path: '/know', mock: { knowledge: 'knowledge' } },
  {
    name: 'knowledge-add',
    path: '/know',
    mock: { knowledge: 'knowledge' },
    setup: async (page) => {
      await page.getByRole('button', { name: 'Add something you know' }).click()
      await page.keyboard.type('soft', { delay: 30 })
    },
    settleMs: 700,
    fullPage: false,
  },
  { name: 'knowledge-loading', path: '/know', mock: { delay: { 'GET /users/:id/knowledge': Infinity } }, settleMs: 700, fullPage: false },

  // ---------------------------------------------------------------- Settings, 404, Reader placeholder
  { name: 'settings', path: '/settings' },
  { name: 'settings-no-key', path: '/settings', mock: { health: 'health-no-key' }, settleMs: 800 },
  { name: 'not-found', path: '/this/page/does/not/exist', fullPage: false },
  { name: 'reader', path: '/paper/1706.03762', mock: { papers: ready() }, fullPage: false },
]
