import { afterEach, describe, expect, it, vi } from 'vitest'
import { USER_ID_KEY } from '../lib/userId'
import { api, ApiError, apiUrl, errorFromResponse, isApiError, isNotFound, NetworkError } from './client'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

describe('apiUrl', () => {
  it('prefixes /api and drops empty query values', () => {
    expect(apiUrl('/papers')).toBe('/api/papers')
    expect(apiUrl('concepts', { q: 'soft max', limit: 8, user_id: null, x: undefined, y: '' })).toBe(
      '/api/concepts?q=soft+max&limit=8',
    )
  })
})

describe('api', () => {
  const realFetch = globalThis.fetch
  afterEach(() => {
    globalThis.fetch = realFetch
  })

  it('returns parsed JSON and sends JSON bodies', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ user_id: 'u1' }))
    globalThis.fetch = fetchMock
    await expect(api('/users/u1', { method: 'PATCH', body: { level: 'expert' } })).resolves.toEqual({ user_id: 'u1' })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/users/u1')
    expect(init.method).toBe('PATCH')
    expect(init.body).toBe('{"level":"expert"}')
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('sends the stored reader id, which decides which added papers the server shows', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(json([])))
    globalThis.fetch = fetchMock
    const sentHeaders = async () => {
      await api('/papers')
      const [, init] = fetchMock.mock.lastCall as unknown as [string, RequestInit]
      return init.headers
    }
    expect(await sentHeaders()).not.toHaveProperty('X-Reader-Id')
    window.localStorage.setItem(USER_ID_KEY, 'u_abc')
    try {
      expect(await sentHeaders()).toHaveProperty('X-Reader-Id', 'u_abc')
    } finally {
      window.localStorage.removeItem(USER_ID_KEY)
    }
  })

  it('resolves 204 to undefined', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    await expect(api('/papers/x', { method: 'DELETE' })).resolves.toBeUndefined()
  })

  it('turns {"error": {code, message}} into a typed ApiError, keeping extra fields', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      json(
        {
          error: {
            code: 'newer_version',
            message: 'You pasted v3. The latest version is v5.',
            latest_version: 'v5',
            paper_id: '1706.03762',
          },
        },
        422,
      ),
    )
    const err = await api('/papers', { method: 'POST', body: { arxiv: '1706.03762v3' } }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 422, code: 'newer_version', message: 'You pasted v3. The latest version is v5.' })
    expect((err as ApiError).detail?.latest_version).toBe('v5')
    expect(isApiError(err, 'newer_version')).toBe(true)
    expect(isApiError(err, 'not_found')).toBe(false)
  })

  it('falls back to an http_<status> code when the body is not the error shape', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response('<h1>Bad gateway</h1>', { status: 502, statusText: 'Bad Gateway' }))
    const err = (await api('/papers').catch((e: unknown) => e)) as ApiError
    expect(err.code).toBe('http_502')
    expect(err.status).toBe(502)
    expect(err.message).toBe('Bad Gateway')
  })

  it('reports a request that never got an answer as NetworkError', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))
    const err = await api('/health').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(NetworkError)
    expect((err as NetworkError).message).toBe('The server didn’t respond.')
  })

  it('rethrows aborts as they are', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new DOMException('aborted', 'AbortError'))
    await expect(api('/health', { signal: AbortSignal.abort() })).rejects.toMatchObject({ name: 'AbortError' })
  })
})

describe('errorFromResponse and helpers', () => {
  it('recognises 404s', async () => {
    const err = await errorFromResponse(json({ error: { code: 'user_not_found', message: 'No reader profile.' } }, 404))
    expect(isNotFound(err)).toBe(true)
    expect(isNotFound(new NetworkError())).toBe(false)
  })
})
