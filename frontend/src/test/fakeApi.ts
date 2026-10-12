/**
 * A small in-memory PaperMind API for component tests: install it as
 * `globalThis.fetch`, then assert on `calls` and `bodies`. Mirrors the parts of
 * e2e/mock-api.mjs that unit tests need, without Playwright.
 */
import { vi } from 'vitest'
import type { KnownConcept, PaperSummary, UserProfile } from '../api/types'

export interface FakeApiOptions {
  papers?: PaperSummary[]
  user?: UserProfile
  knowledge?: KnownConcept[]
  /** Route key ("POST /papers") → status + error body, or 'network' to fail the request outright. */
  fail?: Record<string, { status: number; code: string; message: string; extra?: Record<string, unknown> } | 'network'>
}

export const USER: UserProfile = {
  user_id: 'u_test',
  display_name: null,
  level: 'intermediate',
  onboarded: true,
  created_at: '2026-10-02T09:00:00Z',
}

export function paper(over: Partial<PaperSummary> & Pick<PaperSummary, 'paper_id' | 'title'>): PaperSummary {
  return {
    source: 'arxiv',
    authors: ['Ashish Vaswani', 'Noam Shazeer', 'Niki Parmar'],
    published: '2017-06-12',
    num_pages: 15,
    status: 'ready',
    stage: 'done',
    progress: 1,
    stage_detail: null,
    error: null,
    error_code: null,
    added_at: '2026-10-03T09:12:00Z',
    ...over,
  }
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const error = (status: number, code: string, message: string) => json({ error: { code, message } }, status)

const routeKey = (method: string, path: string) =>
  `${method} ${path
    .replace(/^\/papers\/(?!upload$)[^/]+/, '/papers/:id')
    .replace(/^\/users\/[^/]+/, '/users/:id')
    .replace(/\/knowledge\/(?!areas$)[^/]+$/, '/knowledge/:concept')}`

export function installFakeApi(options: FakeApiOptions = {}) {
  const state = {
    papers: [...(options.papers ?? [])],
    user: options.user ?? USER,
    knowledge: [...(options.knowledge ?? [])],
    calls: [] as string[],
    bodies: [] as { key: string; body: unknown }[],
  }
  window.localStorage.setItem('pm:user', state.user.user_id)

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost')
    const path = url.pathname.replace(/^\/api/, '')
    const method = init?.method ?? 'GET'
    const key = routeKey(method, path)
    state.calls.push(key)
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined
    if (body !== undefined) state.bodies.push({ key, body })

    const fail = options.fail?.[key]
    if (fail === 'network') throw new TypeError('Failed to fetch')
    if (fail) {
      // A one-off failure: the next identical request goes through.
      delete options.fail![key]
      return json({ error: { code: fail.code, message: fail.message, ...fail.extra } }, fail.status)
    }

    if (key === 'GET /health') return json({ status: 'ok', version: '0.4.0', checks: { anthropic_key: { ok: true } } })
    if (key === 'GET /papers') return json(state.papers)
    if (key === 'POST /papers') {
      const id = String((body as { arxiv?: string })?.arxiv ?? '').replace(/v\d+$/, '')
      const existing = state.papers.find((p) => p.paper_id === id)
      if (existing) return json(existing, existing.status === 'ready' ? 200 : 202)
      const row = paper({ paper_id: id, title: `arXiv:${id}`, authors: [], published: null, num_pages: 0, status: 'queued', stage: null, progress: 0, added_at: new Date().toISOString() })
      state.papers = [row, ...state.papers]
      return json(row, 202)
    }
    const m = /^\/papers\/([^/]+)$/.exec(path)
    if (m && method === 'DELETE') {
      state.papers = state.papers.filter((p) => p.paper_id !== decodeURIComponent(m[1]!))
      return new Response(null, { status: 204 })
    }
    if (key === 'GET /users/:id') return json(state.user)
    if (key === 'GET /users/:id/knowledge') return json(state.knowledge)
    return error(404, 'not_found', `No fake route for ${key}`)
  })
  globalThis.fetch = fetchMock as unknown as typeof fetch
  return { state, fetchMock }
}
