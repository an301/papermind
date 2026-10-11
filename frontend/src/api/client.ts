/**
 * The one way the app talks to the PaperMind API.
 *
 * Base URL: `VITE_API_ORIGIN` at build time (e.g. https://api.papermind.example
 * for a Vercel deploy whose API runs elsewhere), otherwise same-origin, which
 * the Vite dev server proxies to http://localhost:8000. vercel.json can't
 * rewrite /api to an origin chosen per environment, so the origin is baked in
 * here instead.
 *
 * Every non-2xx response from the API has the body {"error": {"code", "message"}}
 * (papermind/api/errors.py); it comes back as an `ApiError` carrying both.
 * A request that never reached the server comes back as a `NetworkError`.
 */
import { accessHeaders, isAccessCodeError, reportAccessRejected } from './access'
import type { ErrorBody, PaperSummary } from './types'

const RAW_ORIGIN: string = import.meta.env.VITE_API_ORIGIN ?? ''
export const API_ORIGIN = RAW_ORIGIN.trim().replace(/\/+$/, '')

/** Absolute or same-origin URL for an API path such as `/papers`. */
export function apiUrl(path: string, query?: Record<string, string | number | boolean | null | undefined>): string {
  const url = `${API_ORIGIN}/api${path.startsWith('/') ? path : `/${path}`}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== '') params.set(k, String(v))
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

/** The server answered with an error status. `code` is the API's machine-readable code. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: ErrorBody['error'] | null
  constructor(status: number, code: string, message: string, detail: ErrorBody['error'] | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}

/** The request never got an answer: the server is down, the network is off, or CORS refused it. */
export class NetworkError extends Error {
  constructor(message = 'The server didn’t respond.', options?: { cause?: unknown }) {
    super(message, options)
    this.name = 'NetworkError'
  }
}

export const isApiError = (e: unknown, code?: string): e is ApiError =>
  e instanceof ApiError && (code === undefined || e.code === code)

export const isNotFound = (e: unknown): boolean => e instanceof ApiError && e.status === 404

export const isAbort = (e: unknown): boolean =>
  (e instanceof DOMException || e instanceof Error) && e.name === 'AbortError'

/** Turn a non-2xx response into an ApiError, reading the standard error body when there is one. */
export async function errorFromResponse(res: Response): Promise<ApiError> {
  let text = ''
  try {
    text = await res.text()
  } catch {
    // Body unreadable; fall through to the status-only error.
  }
  if (text) {
    try {
      const body = JSON.parse(text) as Partial<ErrorBody>
      if (body && typeof body.error === 'object' && body.error && typeof body.error.code === 'string') {
        if (res.status === 401 && isAccessCodeError(body.error.code)) reportAccessRejected()
        return new ApiError(res.status, body.error.code, body.error.message ?? '', body.error)
      }
    } catch {
      // Not JSON (a proxy's HTML error page, say).
    }
  }
  return new ApiError(res.status, `http_${res.status}`, res.statusText || `HTTP ${res.status}`)
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  body?: unknown
  query?: Record<string, string | number | boolean | null | undefined>
  signal?: AbortSignal
  /** Ask fetch to finish the request even if the page unloads (used for deferred deletes). */
  keepalive?: boolean
}

/** JSON request → typed JSON response. 204 resolves to undefined. */
export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, signal, keepalive } = options
  const headers: Record<string, string> = { Accept: 'application/json', ...accessHeaders() }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let res: Response
  try {
    res = await fetch(apiUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
      keepalive,
      credentials: API_ORIGIN ? 'omit' : 'same-origin',
    })
  } catch (e) {
    if (isAbort(e)) throw e
    throw new NetworkError(undefined, { cause: e })
  }
  if (!res.ok) throw await errorFromResponse(res)
  if (res.status === 204) return undefined as T
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export interface UploadResult {
  summary: PaperSummary
  /** 200 = the paper was already in the library; 202 = a new ingestion started. */
  status: number
}

/**
 * Upload a PDF with byte progress. fetch() has no upload progress events, so
 * this one request uses XMLHttpRequest. `onProgress` gets 0..1.
 */
export function uploadPdf(file: File, onProgress?: (fraction: number) => void, signal?: AbortSignal): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', apiUrl('/papers/upload'))
    xhr.setRequestHeader('Accept', 'application/json')
    for (const [name, value] of Object.entries(accessHeaders())) xhr.setRequestHeader(name, value)
    xhr.responseType = 'text'
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.total ? e.loaded / e.total : 0)
    }
    xhr.onload = () => {
      const res = new Response(xhr.responseText || null, {
        status: xhr.status,
        statusText: xhr.statusText,
        headers: { 'Content-Type': xhr.getResponseHeader('Content-Type') ?? 'application/json' },
      })
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(1)
        try {
          resolve({ summary: JSON.parse(xhr.responseText) as PaperSummary, status: xhr.status })
        } catch (e) {
          reject(e)
        }
      } else {
        void errorFromResponse(res).then(reject)
      }
    }
    xhr.onerror = () => reject(new NetworkError())
    xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'))
    signal?.addEventListener('abort', () => xhr.abort(), { once: true })
    const form = new FormData()
    form.append('file', file, file.name)
    xhr.send(form)
  })
}

/** URL of a paper's PDF, for pdf.js (supports Range requests). */
export const pdfUrl = (paperId: string): string => apiUrl(`/papers/${encodeURIComponent(paperId)}/pdf`)
