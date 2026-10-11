import { QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { USER_ID_KEY } from '../lib/user'
import { INGEST_POLL_MS, papersPollInterval, useKnowledge, useUpdateUser, useUser } from './queries'
import { queryClient } from './queryClient'
import type { PaperSummary } from './types'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const profile = (user_id: string, extra: object = {}) => ({
  user_id,
  display_name: null,
  level: 'intermediate',
  onboarded: false,
  created_at: '2026-10-09T12:00:00Z',
  ...extra,
})

const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>

const paper = (status: PaperSummary['status']): PaperSummary => ({
  paper_id: status,
  source: 'arxiv',
  title: status,
  authors: [],
  num_pages: 0,
  status,
  progress: 0,
  added_at: '2026-10-09T12:00:00Z',
})

describe('papersPollInterval', () => {
  it('polls only while a paper is queued or processing', () => {
    expect(papersPollInterval(undefined)).toBe(false)
    expect(papersPollInterval([paper('ready'), paper('failed')])).toBe(false)
    expect(papersPollInterval([paper('ready'), paper('queued')])).toBe(INGEST_POLL_MS)
    expect(papersPollInterval([paper('processing')])).toBe(INGEST_POLL_MS)
  })
})

describe('user-scoped queries', () => {
  const realFetch = globalThis.fetch
  beforeEach(() => {
    queryClient.clear()
    window.localStorage.clear()
  })
  afterEach(() => {
    globalThis.fetch = realFetch
  })

  it('useUpdateUser applies the change at once and rolls it back if the server refuses', async () => {
    window.localStorage.setItem(USER_ID_KEY, 'u1')
    let refuse: () => void = () => {}
    globalThis.fetch = vi.fn(async (_url: string, init?: RequestInit) => {
      if ((init?.method ?? 'GET') === 'GET') return json(profile('u1'))
      // Hold the PATCH until the test has seen the optimistic state.
      await new Promise<void>((r) => (refuse = r))
      return json({ error: { code: 'internal', message: 'Neo4j is down.' } }, 503)
    }) as unknown as typeof fetch

    const { result } = renderHook(() => ({ user: useUser(), update: useUpdateUser() }), { wrapper })
    await waitFor(() => expect(result.current.user.data?.level).toBe('intermediate'))

    let pending: Promise<unknown> | undefined
    act(() => {
      pending = result.current.update.mutateAsync({ level: 'expert' }).catch(() => undefined)
    })
    await waitFor(() => expect(result.current.user.data?.level).toBe('expert')) // optimistic
    await act(async () => {
      refuse()
      await pending
    })
    await waitFor(() => expect(result.current.user.data?.level).toBe('intermediate')) // rolled back
  })

  it('a user-scoped call that finds the profile gone starts a fresh one', async () => {
    window.localStorage.setItem(USER_ID_KEY, 'u-old')
    let knowledgeCalls = 0
    let serverKnows = new Set(['u-old'])
    globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      if (method === 'POST' && url === '/api/users') {
        serverKnows = new Set(['u-new'])
        return json(profile('u-new'))
      }
      const m = /^\/api\/users\/([^/]+)(\/knowledge)?$/.exec(url)
      if (m) {
        if (!serverKnows.has(m[1]!)) return json({ error: { code: 'user_not_found', message: 'gone' } }, 404)
        if (m[2]) {
          knowledgeCalls++
          // The server restarted between the profile load and this call.
          if (m[1] === 'u-old') {
            serverKnows = new Set()
            return json({ error: { code: 'user_not_found', message: 'gone' } }, 404)
          }
          return json([])
        }
        return json(profile(m[1]!))
      }
      throw new Error(`unexpected ${method} ${url}`)
    }) as unknown as typeof fetch

    const { result } = renderHook(() => ({ user: useUser(), knowledge: useKnowledge() }), { wrapper })
    await waitFor(() => expect(result.current.user.data?.user_id).toBe('u-new'))
    await waitFor(() => expect(result.current.knowledge.data).toEqual([]))
    expect(window.localStorage.getItem(USER_ID_KEY)).toBe('u-new')
    expect(knowledgeCalls).toBe(2)
  })
})
