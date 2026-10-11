import { expect, test } from '@playwright/test'
import { mockApi } from './mock-api.mjs'

test.describe('Reader identity', () => {
  test('the first visit creates one anonymous reader and remembers it', async ({ page }) => {
    const api = await mockApi(page, { user: null })
    await page.goto('/know')
    await expect(page.getByRole('heading', { level: 1, name: 'What you know' })).toBeVisible()
    await expect.poll(() => page.evaluate(() => localStorage.getItem('pm:user'))).toBe('u_new0000001')
    expect(api.state.calls.filter((c) => c === 'POST /users')).toHaveLength(1)
  })

  test('a reader the server forgot quietly gets a new profile', async ({ page }) => {
    const api = await mockApi(page, { user: null, storage: { 'pm:user': 'u_lost_in_a_reset' } })
    await page.goto('/know')
    await expect.poll(() => page.evaluate(() => localStorage.getItem('pm:user'))).toBe('u_new0000001')
    expect(api.state.calls).toContain('GET /users/:id')
    expect(api.state.calls).toContain('POST /users')
  })
})

test.describe('What you know', () => {
  test('groups concepts by area with the state in words', async ({ page }) => {
    await mockApi(page)
    await page.goto('/know')
    await expect(page.getByRole('heading', { level: 2, name: /Linear algebra/ })).toBeVisible()
    await expect(page.getByRole('heading', { level: 2, name: /Attention & transformers/ })).toBeVisible()
    await expect(page.getByText('Comfortable').first()).toBeVisible()
    await expect(page.getByText('Seen it').first()).toBeVisible()
  })

  test('the empty profile offers the areas and saves them', async ({ page }) => {
    const api = await mockApi(page, { knowledge: 'knowledge-empty' })
    await page.goto('/know')
    await expect(page.getByText('This fills in as you read.')).toBeVisible()
    await page.getByRole('checkbox', { name: 'Linear algebra' }).check()
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByRole('heading', { level: 2, name: /Linear algebra/ })).toBeVisible()
    expect(api.state.bodies.find((b) => b.key === 'POST /users/:id/knowledge/areas')?.body).toEqual({ areas: ['linear-algebra'] })
  })
})

test.describe('App shell', () => {
  test('the main nav marks the current page', async ({ page }) => {
    await mockApi(page)
    await page.goto('/know')
    const nav = page.getByRole('navigation', { name: 'Main' })
    await expect(nav.getByRole('link', { name: 'What you know' })).toHaveAttribute('aria-current', 'page')
    await nav.getByRole('link', { name: 'Your papers' }).click()
    await expect(page).toHaveURL(/\/$/)
    await expect(nav.getByRole('link', { name: 'Your papers' })).toHaveAttribute('aria-current', 'page')
  })

  test('/knowledge is an alias of /know', async ({ page }) => {
    await mockApi(page)
    await page.goto('/knowledge')
    await expect(page).toHaveURL(/\/know$/)
  })

  test('an unknown address has a way back', async ({ page }) => {
    await mockApi(page)
    await page.goto('/no/such/page')
    await page.getByRole('main').getByRole('link', { name: /your papers/i }).click()
    await expect(page).toHaveURL(/\/$/)
  })

  test('a theme picked in Settings applies at once and survives a reload', async ({ page }) => {
    await mockApi(page)
    await page.goto('/settings')
    await page.getByRole('radio', { name: 'Dark' }).first().click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    await page.reload()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  })
})
