/**
 * Server-Sent Events over a POST (EventSource can only GET), following the
 * WHATWG "event stream interpretation" rules:
 *  - lines end in \n, \r\n or \r (a \r at a chunk boundary may be followed by \n in the next chunk)
 *  - a blank line dispatches; `:` lines are comments (keep-alives)
 *  - `field: value` strips ONE leading space; `field` alone means an empty value
 *  - several `data:` lines join with \n; no data means nothing is dispatched
 *  - unknown fields are ignored; a leading UTF-8 BOM is ignored
 *
 * Never retries. A chat POST runs the agent (and spends money), so a dropped
 * stream is reported to the caller, who shows "The connection dropped partway
 * through" with a Try again button (DESIGN.md §5.25).
 */
import { API_ORIGIN, NetworkError, apiUrl, errorFromResponse, isAbort, ApiError } from './client'
import { CHAT_EVENT_TYPES, type ChatEvent, type ChatEventType, type ChatRequest } from './types'

export interface SseMessage {
  /** Defaults to "message". */
  event: string
  data: string
  id?: string
}

/** The response wasn't an event stream (a proxy error page, say). */
export class SseProtocolError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'SseProtocolError'
  }
}

/**
 * Parse a byte stream into SSE messages. Abort by aborting the fetch signal;
 * breaking out of the for-await loop cancels the body (closes the connection).
 */
export async function* readSse(body: ReadableStream<Uint8Array<ArrayBuffer>>): AsyncGenerator<SseMessage> {
  // TextDecoderStream handles multi-byte UTF-8 split across chunks.
  const reader = body.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  let data: string[] = []
  let event = ''
  let lastId: string | undefined // the spec's "last event ID buffer" persists across events
  let finished = false

  function* lines(flush: boolean): Generator<string> {
    let start = 0
    for (let i = 0; i < buf.length; i++) {
      const c = buf.charCodeAt(i)
      if (c !== 10 && c !== 13) continue
      if (c === 13 && i === buf.length - 1 && !flush) break // lone \r at chunk end: a \n may follow
      const line = buf.slice(start, i)
      if (c === 13 && buf.charCodeAt(i + 1) === 10) i++
      start = i + 1
      yield line
    }
    buf = buf.slice(start)
  }

  try {
    let first = true
    for (;;) {
      const { value, done } = await reader.read()
      if (!done) {
        buf += value
        if (first) {
          first = false
          if (buf.charCodeAt(0) === 0xfeff) buf = buf.slice(1)
        }
      }
      for (const line of lines(done)) {
        if (line === '') {
          if (data.length) yield { event: event || 'message', data: data.join('\n'), id: lastId }
          data = []
          event = ''
          continue
        }
        if (line.charCodeAt(0) === 58) continue // ":" comment or heartbeat
        const colon = line.indexOf(':')
        const field = colon === -1 ? line : line.slice(0, colon)
        let val = colon === -1 ? '' : line.slice(colon + 1)
        if (val.charCodeAt(0) === 32) val = val.slice(1)
        if (field === 'data') data.push(val)
        else if (field === 'event') event = val
        else if (field === 'id' && !val.includes('\0')) lastId = val
        // "retry" is ignored on purpose: a POST is never re-sent automatically.
      }
      if (done) {
        finished = true // an unterminated trailing event is discarded, per the spec
        return
      }
    }
  } finally {
    if (!finished) await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

const KNOWN = new Set<string>(CHAT_EVENT_TYPES)

/**
 * Decode one SSE message into a ChatEvent. Returns null for frames the app
 * doesn't know (a newer server) or can't parse, so one bad frame can't end a turn.
 */
export function toChatEvent(message: SseMessage): ChatEvent | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(message.data)
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== 'object') return null
  const obj = parsed as { type?: unknown }
  const type = typeof obj.type === 'string' ? obj.type : message.event
  if (!KNOWN.has(type)) return null
  return { ...(obj as object), type: type as ChatEventType } as ChatEvent
}

function resolveUrl(url: string): string {
  if (/^https?:\/\//.test(url)) return url
  // Accept both '/chat' and '/api/chat'; either way honour VITE_API_ORIGIN.
  return apiUrl(url.startsWith('/api/') ? url.slice(4) : url)
}

/**
 * POST JSON, then iterate typed ChatEvents as they arrive.
 *
 * Throws `ApiError` for a non-2xx answer (it never started streaming: the
 * question is "Not sent."), `NetworkError` if the server can't be reached,
 * `SseProtocolError` if the response isn't an event stream, and an
 * `AbortError` DOMException when `signal` aborts (the reader pressed Stop).
 * If the stream ends without a `done` event the generator just returns; the
 * caller treats that as a dropped connection.
 */
export async function* postSse(url: string, body: unknown, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
  let res: Response
  try {
    res = await fetch(resolveUrl(url), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal,
      cache: 'no-store',
      credentials: API_ORIGIN ? 'omit' : 'same-origin',
    })
  } catch (e) {
    if (isAbort(e)) throw e
    throw new NetworkError(undefined, { cause: e })
  }
  if (!res.ok) throw await errorFromResponse(res)
  if (!res.headers.get('content-type')?.startsWith('text/event-stream') || !res.body) {
    throw new SseProtocolError(`Expected text/event-stream, got ${res.headers.get('content-type') ?? 'nothing'}`)
  }
  for await (const message of readSse(res.body as ReadableStream<Uint8Array<ArrayBuffer>>)) {
    const event = toChatEvent(message)
    if (event) yield event
  }
}

/** POST /api/chat. One call per question; see `postSse` for what it throws. */
export const streamChat = (request: ChatRequest, signal?: AbortSignal): AsyncGenerator<ChatEvent> =>
  postSse('/chat', request, signal)

export { ApiError }
