import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PaperMeta, Session } from '../../../api/types'
import { useReaderStore } from '../../shared/readerStore'
import { useConversation } from './conversation'
import { activeAnswer } from './types'

const PAPER = '2610.11673'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const apiError = (status: number, code: string, message: string) => json({ error: { code, message } }, status)

const session = (id: string): Session => ({
  session_id: id,
  user_id: 'u1',
  paper_id: PAPER,
  level: 'intermediate',
  scroll: { page: 0, offset: 0, zoom: null },
  messages: [],
  created_at: '2026-10-11T00:00:00Z',
  updated_at: '2026-10-11T00:00:00Z',
})

/** A turn that starts and finishes with no answer text: enough to prove the question was sent. */
const sse = (sessionId: string) =>
  new Response(
    [
      `event: turn.started\ndata: ${JSON.stringify({ type: 'turn.started', turn_id: 't1', session_id: sessionId, level: 'intermediate', user_message_id: 'm1' })}\n\n`,
      `event: done\ndata: ${JSON.stringify({ type: 'done' })}\n\n`,
    ].join(''),
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
  )

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>

function serve(handler: Handler) {
  const calls: { method: string; url: string; body: unknown }[] = []
  globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? 'GET', url, body: init?.body ? JSON.parse(String(init.body)) : null })
    return handler(url, init)
  }) as unknown as typeof fetch
  return calls
}

const answer = () => activeAnswer(useConversation.getState().chat.turns.at(-1)!)
const settle = () => vi.waitFor(() => expect(['sending', 'queued', 'working']).not.toContain(answer().phase))

describe('conversation sessions', () => {
  const realFetch = globalThis.fetch
  beforeEach(() => {
    useConversation.setState({ paperId: null, sessionStatus: 'idle', sessionRenewed: false, sessionError: null, chat: { turns: [] } })
    useReaderStore.getState().setSession(null)
    useReaderStore.getState().setMeta({ paper_id: PAPER } as PaperMeta)
  })
  afterEach(() => {
    globalThis.fetch = realFetch
  })

  it('a question asked while the session is still opening waits for it', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    const calls = serve(async (url) => {
      if (url.endsWith('/api/sessions')) {
        await gate
        return json(session('s1'))
      }
      if (url.endsWith('/api/chat')) return sse('s1')
      return json({})
    })
    const opening = useConversation.getState().open(PAPER, 'u1', 'intermediate')
    useConversation.getState().ask({ question: 'What problem does it solve?' })
    release()
    await opening
    await settle()
    expect(answer().phase).not.toBe('not_sent')
    expect(calls.find((c) => c.url.endsWith('/api/chat'))?.body).toMatchObject({ session_id: 's1' })
  })

  it('retries opening a session that failed, instead of refusing every question', async () => {
    let up = false
    serve((url) => {
      if (url.endsWith('/api/sessions')) return up ? json(session('s2')) : Promise.reject(new TypeError('Failed to fetch'))
      if (url.endsWith('/api/chat')) return sse('s2')
      return json({})
    })
    await useConversation.getState().open(PAPER, 'u1', 'intermediate')
    expect(useConversation.getState().sessionStatus).toBe('failed')
    up = true // the server is back
    useConversation.getState().ask({ question: 'What problem does it solve?' })
    await settle()
    expect(answer().phase).not.toBe('not_sent')
    expect(useConversation.getState().sessionStatus).toBe('ready')
  })

  it('starts a new session when the old one expired, and sends the question once', async () => {
    const calls = serve((url, init) => {
      if (url.endsWith('/api/sessions')) return json(session('s_new'))
      if (url.endsWith('/api/chat')) {
        const body = JSON.parse(String(init?.body)) as { session_id: string }
        return body.session_id === 's_old'
          ? apiError(404, 'session_not_found', 'This reading session has expired or does not exist.')
          : sse(body.session_id)
      }
      return json({})
    })
    useConversation.setState({ paperId: PAPER, sessionStatus: 'ready' })
    await useConversation.getState().open(PAPER, 'u1', 'intermediate') // records who the session is for
    useReaderStore.getState().setSession(session('s_old'))
    useConversation.getState().ask({ question: 'And the method?' })
    await settle()
    expect(answer().phase).not.toBe('not_sent')
    expect(useConversation.getState().sessionRenewed).toBe(true)
    const chats = calls.filter((c) => c.url.endsWith('/api/chat')).map((c) => (c.body as { session_id: string }).session_id)
    expect(chats).toEqual(['s_old', 's_new'])
  })

  it('reopening a paper after going back to the library reconnects its session', async () => {
    const calls = serve((url) => {
      if (url.endsWith('/api/sessions')) return json(session('s1'))
      if (url.includes('/api/sessions/s1')) return json({ ...session('s1'), messages: [] })
      if (url.endsWith('/api/chat')) return sse('s1')
      return json({})
    })
    await useConversation.getState().open(PAPER, 'u1', 'intermediate')
    useReaderStore.getState().closePaper() // back to the library
    useReaderStore.getState().openPaper(PAPER, { meta: { paper_id: PAPER } as PaperMeta })
    await useConversation.getState().open(PAPER, 'u1', 'intermediate')
    expect(useReaderStore.getState().session?.session_id).toBe('s1')
    useConversation.getState().ask({ question: 'Still there?' })
    await settle()
    expect(answer().phase).not.toBe('not_sent')
    expect(calls.some((c) => c.url.endsWith('/api/chat'))).toBe(true)
  })

  it('a question asked while an answer streams waits its turn instead of running alongside it', async () => {
    let finishFirst!: () => void
    const firstDone = new Promise<void>((r) => (finishFirst = r))
    const order: string[] = []
    serve(async (url, init) => {
      if (url.endsWith('/api/sessions')) return json(session('s1'))
      if (url.endsWith('/api/chat')) {
        const { message } = JSON.parse(String(init?.body)) as { message: string }
        order.push(`start ${message}`)
        if (message === 'first') await firstDone
        order.push(`end ${message}`)
        return sse('s1')
      }
      return json({})
    })
    await useConversation.getState().open(PAPER, 'u1', 'intermediate')
    useConversation.getState().ask({ question: 'first' })
    useConversation.getState().ask({ question: 'second' })
    await vi.waitFor(() => expect(order).toEqual(['start first']))
    expect(answer().phase).toBe('queued')
    expect(answer().queueReason).toBe('turn')
    finishFirst()
    await vi.waitFor(() => expect(order).toEqual(['start first', 'end first', 'start second', 'end second']))
  })

  it('says why a question was not sent', async () => {
    serve((url) => {
      if (url.endsWith('/api/sessions')) return json(session('s1'))
      if (url.endsWith('/api/chat')) return apiError(429, 'rate_limited', 'You have asked 20 questions this hour.')
      return json({})
    })
    await useConversation.getState().open(PAPER, 'u1', 'intermediate')
    useConversation.getState().ask({ question: 'One more?' })
    await settle()
    expect(answer().phase).toBe('not_sent')
    expect(answer().problem).toMatchObject({ kind: 'not_sent', reason: 'You have asked 20 questions this hour.' })
  })
})
