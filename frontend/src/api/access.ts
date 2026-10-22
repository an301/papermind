/**
 * The server's access code, when it has one (a public deployment; see
 * DEPLOY.md). Kept in localStorage so a reader types it once per browser, and
 * sent as `X-Access-Code` on every API request: JSON, uploads, the chat
 * stream and pdf.js's PDF fetches.
 *
 * The same requests carry the reader's anonymous id as `X-Reader-Id`: a paper
 * a reader adds (arXiv or upload) is listed and served only to them.
 *
 * A request refused for its code tells the gate (`onAccessRejected`), which
 * clears the stored code and asks again: the operator may have changed it.
 */

import { storedUserId } from '../lib/userId'

export const ACCESS_HEADER = 'X-Access-Code'
export const READER_HEADER = 'X-Reader-Id'
const STORAGE_KEY = 'papermind.accessCode'

let current: string | null = read()
const listeners = new Set<() => void>()

function read(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

export function getAccessCode(): string | null {
  return current
}

export function setAccessCode(code: string | null): void {
  current = code && code.trim() ? code.trim() : null
  try {
    if (current) localStorage.setItem(STORAGE_KEY, current)
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Private mode or blocked storage: the code lasts until the tab closes.
  }
}

/** Headers to add to an API request: the access code and reader id, each when stored. */
export function accessHeaders(): Record<string, string> {
  const headers: Record<string, string> = {}
  if (current) headers[ACCESS_HEADER] = current
  // Read at request time: the id appears once the first visit creates the profile.
  const readerId = storedUserId()
  if (readerId) headers[READER_HEADER] = readerId
  return headers
}

export const isAccessCodeError = (code: string): boolean =>
  code === 'access_code_required' || code === 'access_code_invalid'

/** Called by the API client when the server refused a request for its code. */
export function reportAccessRejected(): void {
  for (const listener of listeners) listener()
}

export function onAccessRejected(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
