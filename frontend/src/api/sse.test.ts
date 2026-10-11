// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, NetworkError } from './client'
import { postSse, readSse, SseProtocolError, toChatEvent, type SseMessage } from './sse'
import type { ChatEvent } from './types'

const enc = new TextEncoder()

/** A byte stream that delivers `chunks` one read at a time. */
function streamOf(chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array<ArrayBuffer>> {
  let i = 0
  return new ReadableStream<Uint8Array<ArrayBuffer>>({
    pull(controller) {
      if (i >= chunks.length) return controller.close()
      const c = chunks[i++]!
      controller.enqueue((typeof c === 'string' ? enc.encode(c) : c) as Uint8Array<ArrayBuffer>)
    },
  })
}

async function collect(chunks: (string | Uint8Array)[]): Promise<SseMessage[]> {
  const out: SseMessage[] = []
  for await (const m of readSse(streamOf(chunks))) out.push(m)
  return out
}

describe('readSse', () => {
  it('parses events with names, ids and multi-line data', async () => {
    const msgs = await collect(['event: a\nid: 1\ndata: one\ndata: two\n\n', 'data: plain\n\n'])
    expect(msgs).toEqual([
      { event: 'a', data: 'one\ntwo', id: '1' },
      { event: 'message', data: 'plain', id: '1' }, // the last event id persists
    ])
  })

  it('handles CRLF and bare CR line endings, including a CR split from its LF across chunks', async () => {
    const msgs = await collect(['data: a\r', '\n\r\ndata: b\r\rdata: c\r\n', '\r\n'])
    expect(msgs.map((m) => m.data)).toEqual(['a', 'b', 'c'])
  })

  it('ignores comments, unknown fields, retry and a leading BOM', async () => {
    const msgs = await collect(['﻿: ping\nretry: 10\nfoo: bar\ndata: x\n\n'])
    expect(msgs).toEqual([{ event: 'message', data: 'x', id: undefined }])
  })

  it('strips exactly one leading space and treats a bare field name as empty', async () => {
    const msgs = await collect(['data:  two spaces\ndata\n\n'])
    expect(msgs[0]!.data).toBe(' two spaces\n')
  })

  it('does not dispatch blocks without data, nor an unterminated trailing event', async () => {
    const msgs = await collect(['event: lonely\n\n', 'data: done\n\n', 'data: cut off'])
    expect(msgs.map((m) => m.data)).toEqual(['done'])
  })

  it('decodes multi-byte UTF-8 split across chunks', async () => {
    const bytes = enc.encode('data: “√dₖ”\n\n')
    const msgs = await collect([bytes.slice(0, 8), bytes.slice(8, 9), bytes.slice(9)])
    expect(msgs[0]!.data).toBe('“√dₖ”')
  })

  it('gives the same events however the stream is chunked', async () => {
    const wire =
      'event: turn.started\r\ndata: {"type":"turn.started"}\r\n\r\n: keep-alive\n\nevent: answer.delta\ndata: {"type":"answer.delta","text":"Softmax “saturates” …"}\n\n' +
      'event: done\rdata: {"type":"done"}\r\r'
    const whole = await collect([wire])
    const bytes = enc.encode(wire)
    // Deterministic pseudo-random cut points, so a failure reproduces.
    let seed = 7
    const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647)
    for (let run = 0; run < 200; run++) {
      const parts: Uint8Array[] = []
      let at = 0
      while (at < bytes.length) {
        const n = 1 + Math.floor(rand() * 12)
        parts.push(bytes.slice(at, at + n))
        at += n
      }
      expect(await collect(parts)).toEqual(whole)
    }
    expect(whole.map((m) => m.event)).toEqual(['turn.started', 'answer.delta', 'done'])
  })

  it('cancels the body when the consumer stops early', async () => {
    const cancel = vi.fn()
    const body = new ReadableStream<Uint8Array<ArrayBuffer>>({
      start(c) {
        c.enqueue(enc.encode('data: 1\n\ndata: 2\n\n') as Uint8Array<ArrayBuffer>)
      },
      cancel,
    })
    for await (const m of readSse(body)) {
      expect(m.data).toBe('1')
      break
    }
    expect(cancel).toHaveBeenCalled()
  })
})

describe('toChatEvent', () => {
  it('decodes known types and takes the type from the event name when the JSON lacks it', () => {
    expect(toChatEvent({ event: 'answer.delta', data: '{"text":"hi"}' })).toEqual({ type: 'answer.delta', text: 'hi' })
    expect(toChatEvent({ event: 'message', data: '{"type":"done"}' })).toEqual({ type: 'done' })
  })

  it('drops frames it cannot use instead of ending the turn', () => {
    expect(toChatEvent({ event: 'future.thing', data: '{"type":"future.thing"}' })).toBeNull()
    expect(toChatEvent({ event: 'answer.delta', data: 'not json' })).toBeNull()
    expect(toChatEvent({ event: 'answer.delta', data: '42' })).toBeNull()
  })
})

describe('postSse', () => {
  const realFetch = globalThis.fetch
  afterEach(() => {
    globalThis.fetch = realFetch
  })

  const sse = (body: string, init: ResponseInit = {}) =>
    new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream; charset=utf-8' }, ...init })

  it('POSTs the body as JSON and yields typed events in order', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sse(
        'event: turn.started\ndata: {"type":"turn.started","turn_id":"t","session_id":"s","level":"beginner","user_message_id":"m"}\n\n' +
          'event: answer.delta\ndata: {"type":"answer.delta","text":"When "}\n\n' +
          'event: done\ndata: {"type":"done"}\n\n',
      ),
    )
    globalThis.fetch = fetchMock
    const events: ChatEvent[] = []
    for await (const e of postSse('/chat', { session_id: 's', message: 'Why?' })) events.push(e)
    expect(events.map((e) => e.type)).toEqual(['turn.started', 'answer.delta', 'done'])
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/chat')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ session_id: 's', message: 'Why?' })
    expect(new Headers(init.headers).get('Accept')).toBe('text/event-stream')
  })

  it('throws ApiError with the server code for a non-2xx answer, and never retries', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'session_not_found', message: 'That session expired.' } }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    globalThis.fetch = fetchMock
    const run = async () => {
      for await (const _ of postSse('/api/chat', {})) void _
    }
    await expect(run()).rejects.toMatchObject({ name: 'ApiError', status: 404, code: 'session_not_found', message: 'That session expired.' })
    await expect(run()).rejects.toBeInstanceOf(ApiError)
    expect(fetchMock).toHaveBeenCalledTimes(2) // once per call, no retries
  })

  it('throws SseProtocolError when a proxy answers with HTML', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response('<html>', { status: 200, headers: { 'Content-Type': 'text/html' } }))
    const run = async () => {
      for await (const _ of postSse('/chat', {})) void _
    }
    await expect(run()).rejects.toBeInstanceOf(SseProtocolError)
  })

  it('reports an unreachable server as NetworkError and passes aborts through untouched', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))
    const run = async (signal?: AbortSignal) => {
      for await (const _ of postSse('/chat', {}, signal)) void _
    }
    await expect(run()).rejects.toBeInstanceOf(NetworkError)

    globalThis.fetch = vi.fn().mockRejectedValue(new DOMException('The user aborted a request.', 'AbortError'))
    await expect(run(AbortSignal.abort())).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('ends quietly when the stream closes without done (the caller shows "connection dropped")', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(sse('event: answer.delta\ndata: {"type":"answer.delta","text":"Partial"}\n\n'))
    const types: string[] = []
    for await (const e of postSse('/chat', {})) types.push(e.type)
    expect(types).toEqual(['answer.delta'])
  })
})
