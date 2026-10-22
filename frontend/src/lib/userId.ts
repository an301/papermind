/**
 * Where the anonymous reader id is kept (see src/lib/user.ts). Separate from
 * user.ts so the API client can read it without importing the module that
 * calls the API.
 */
import { storage } from './storage'

export const USER_ID_KEY = 'pm:user'

export const storedUserId = (): string | null => storage.get(USER_ID_KEY)

export function forgetStoredUser(): void {
  storage.remove(USER_ID_KEY)
}
