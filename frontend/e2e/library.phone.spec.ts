import { expect, test } from '@playwright/test'
import { fixture, mockApi } from './mock-api.mjs'

const ready = () => (fixture('papers') as { status: string }[]).filter((p) => p.status === 'ready')

test.describe('Library on a phone', () => {
  test('a PDF can be chosen with a tap, since there is nothing to drag', async ({ page }) => {
    await mockApi(page, { papers: 'papers-empty' })
    await page.goto('/')
    const choose = page.getByRole('button', { name: 'Choose PDF' })
    await expect(choose).toBeVisible()
    const chooser = page.waitForEvent('filechooser')
    await choose.click()
    await expect(chooser).resolves.toBeTruthy()
  })

  test('nothing overflows the 390px viewport', async ({ page }) => {
    await mockApi(page, { papers: ready() })
    await page.goto('/')
    await expect(page.getByRole('heading', { level: 1, name: 'Your papers' })).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(0)
  })

  test('touch targets in the header are at least 44px', async ({ page }) => {
    await mockApi(page, { papers: ready() })
    await page.goto('/')
    const settings = page.getByRole('link', { name: 'Settings' })
    const box = await settings.boundingBox()
    expect(box?.width).toBeGreaterThanOrEqual(44)
    expect(box?.height).toBeGreaterThanOrEqual(44)
  })
})
