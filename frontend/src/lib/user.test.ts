import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { bootstrapUser, storedUserId, USER_ID_KEY } from './user'

const profile = (user_id: string) => ({
  user_id,
  display_name: null,
  level: 'intermediate',
  onboarded: false,
  created_at: '2026-10-09T12:00:00Z',
})

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

/** A fake API: GET /users/{id} knows `known`; POST /users mints ids in order. */
function fakeApi(known: string[], { failGet }: { failGet?: number } = {}) {
  let minted = 0
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (method === 'POST' && url === '/api/users') return json(profile(`new-${++minted}`))
    const m = /^\/api\/users\/([^/]+)$/.exec(url)
    if (method === 'GET' && m) {
      if (failGet) return json({ error: { code: 'internal', message: 'Neo4j is down.' } }, failGet)
      const id = decodeURIComponent(m[1]!)
      return known.includes(id)
        ? json(profile(id))
        : json({ error: { code: 'user_not_found', message: `No reader profile with id ${id}.` } }, 404)
    }
    throw new Error(`unexpected ${method} ${url}`)
  })
}

describe('bootstrapUser', () => {
  const realFetch = globalThis.fetch
  beforeEach(() => window.localStorage.clear())
  afterEach(() => {
    globalThis.fetch = realFetch
  })

  it('creates a profile on the first visit and remembers its id', async () => {
    const fetchMock = fakeApi([])
    globalThis.fetch = fetchMock as unknown as typeof fetch
    const user = await bootstrapUser()
    expect(user.user_id).toBe('new-1')
    expect(storedUserId()).toBe('new-1')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('reuses the stored id when the server still knows it', async () => {
    window.localStorage.setItem(USER_ID_KEY, 'u-42')
    const fetchMock = fakeApi(['u-42'])
    globalThis.fetch = fetchMock as unknown as typeof fetch
    expect((await bootstrapUser()).user_id).toBe('u-42')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/users/u-42')
  })

  it('starts a new profile when the server forgot the stored one (404)', async () => {
    window.localStorage.setItem(USER_ID_KEY, 'lost-after-reset')
    globalThis.fetch = fakeApi([]) as unknown as typeof fetch
    expect((await bootstrapUser()).user_id).toBe('new-1')
    expect(storedUserId()).toBe('new-1')
  })

  it('keeps the stored id when the server fails for another reason', async () => {
    window.localStorage.setItem(USER_ID_KEY, 'u-42')
    globalThis.fetch = fakeApi(['u-42'], { failGet: 503 }) as unknown as typeof fetch
    await expect(bootstrapUser()).rejects.toMatchObject({ status: 503 })
    expect(storedUserId()).toBe('u-42')
  })

  it('shares one request between concurrent callers, so a double mount never creates two users', async () => {
    const fetchMock = fakeApi([])
    globalThis.fetch = fetchMock as unknown as typeof fetch
    const [a, b] = await Promise.all([bootstrapUser(), bootstrapUser()])
    expect(a.user_id).toBe('new-1')
    expect(b.user_id).toBe('new-1')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
