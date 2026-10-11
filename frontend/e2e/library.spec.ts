import { expect, test } from '@playwright/test'
import { fixture, mockApi } from './mock-api.mjs'

const ready = () => (fixture('papers') as { status: string }[]).filter((p) => p.status === 'ready')
const addField = (page: import('@playwright/test').Page) => page.getByPlaceholder('Paste an arXiv link or ID')

test.describe('Library', () => {
  test('first run is a sentence and a field', async ({ page }) => {
    await mockApi(page, { papers: 'papers-empty' })
    await page.goto('/')
    await expect(page.getByRole('heading', { level: 1, name: 'Start with a paper you’ve been meaning to read.' })).toBeVisible()
    await expect(addField(page)).toBeVisible()
  })

  test('lists papers as links to the reader', async ({ page }) => {
    await mockApi(page, { papers: ready() })
    await page.goto('/')
    await expect(page.getByRole('heading', { level: 1, name: 'Your papers' })).toBeVisible()
    const row = page.getByRole('link', { name: /Attention Is All You Need/ })
    await expect(row).toHaveAttribute('href', '/paper/1706.03762')
  })

  test('adds a paper by arXiv link; its row fills in as it is prepared', async ({ page }) => {
    const api = await mockApi(page, { papers: ready(), ingest: 'advance' })
    await page.goto('/')
    await addField(page).fill('https://arxiv.org/abs/2006.11239v2')
    await addField(page).press('Enter')
    await expect(addField(page)).toHaveValue('')
    await expect(addField(page)).toBeFocused()
    await expect(page.getByText('Denoising Diffusion Probabilistic Models').first()).toBeVisible({ timeout: 10_000 })
    // The field normalises the link before it posts (DESIGN.md §5.4).
    expect(api.state.bodies.find((b) => b.key === 'POST /papers')?.body).toEqual({ arxiv: '2006.11239v2' })
    await expect(page.getByText(/Indexing \d+ of 212 passages…|Ready|Not opened/).first()).toBeVisible({ timeout: 15_000 })
  })

  test('explains an input that is not an arXiv id, in place, and keeps the text', async ({ page }) => {
    const api = await mockApi(page, { papers: ready() })
    await page.goto('/')
    await addField(page).fill('attention is all you need')
    await addField(page).press('Enter')
    await expect(page.getByText('That doesn’t look like an arXiv ID. Try one like 2401.12345, or drop a PDF.')).toBeVisible()
    await expect(addField(page)).toHaveValue('attention is all you need')
    await expect(addField(page)).toBeFocused()
    expect(api.state.calls).not.toContain('POST /papers')
  })

  test('removing a paper is undoable, and nothing is deleted until the undo window closes', async ({ page }) => {
    const api = await mockApi(page, { papers: ready() })
    await page.goto('/')
    await page.getByRole('button', { name: /^More for Attention Is All You Need/ }).click()
    await page.getByRole('menuitem', { name: 'Remove from your papers' }).click()
    await expect(page.getByRole('link', { name: /Attention Is All You Need/ })).toHaveCount(0)
    await expect(page.getByText('Removed Attention Is All You Need.', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Undo' }).click()
    await expect(page.getByRole('link', { name: /Attention Is All You Need/ })).toBeVisible()
    expect(api.state.calls).not.toContain('DELETE /papers/:id')
  })

  test('a failed ingestion says why and offers Try again and Remove', async ({ page }) => {
    await mockApi(page, { papers: 'papers-failed' })
    await page.goto('/')
    await expect(page.getByText(/arXiv has no paper 2401\.99999/)).toBeVisible()
    await expect(page.getByRole('button', { name: /Try again/ }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: /Remove/ }).first()).toBeVisible()
  })
})
