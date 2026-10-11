/**
 * Anonymous reader identity.
 *
 * There are no accounts: the first visit creates a profile (POST /api/users)
 * and keeps its id in localStorage. The server can lose that profile (the
 * in-memory graph store forgets users on restart, or someone resets Neo4j),
 * so a 404 for the stored id quietly creates a new profile rather than
 * locking the reader out.
 */
import { api, isNotFound } from '../api/client'
import type { UserProfile } from '../api/types'
import { storage } from './storage'

export const USER_ID_KEY = 'pm:user'

export const storedUserId = (): string | null => storage.get(USER_ID_KEY)

export function forgetStoredUser(): void {
  storage.remove(USER_ID_KEY)
}

let inflight: Promise<UserProfile> | null = null

async function load(): Promise<UserProfile> {
  const id = storedUserId()
  if (id) {
    try {
      return await api<UserProfile>(`/users/${encodeURIComponent(id)}`)
    } catch (e) {
      if (!isNotFound(e)) throw e
      // The server no longer knows this id: start over with a fresh profile.
      forgetStoredUser()
    }
  }
  const user = await api<UserProfile>('/users', { method: 'POST' })
  storage.set(USER_ID_KEY, user.user_id)
  return user
}

/**
 * The current reader's profile, creating one on first visit. Concurrent
 * callers share one request, so React StrictMode's double mount (or two
 * components asking at once) never creates two users.
 */
export function bootstrapUser(): Promise<UserProfile> {
  inflight ??= load().finally(() => {
    inflight = null
  })
  return inflight
}
