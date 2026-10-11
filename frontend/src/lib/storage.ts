/**
 * localStorage that never throws (Safari private mode, a full quota, a
 * sandboxed iframe). Every key the app uses starts with `pm:`; the full list
 * is in src/features/shared/README.md.
 */
export const storage = {
  get(key: string): string | null {
    try {
      return window.localStorage.getItem(key)
    } catch {
      return null
    }
  },
  set(key: string, value: string): void {
    try {
      window.localStorage.setItem(key, value)
    } catch {
      // Storage full or unavailable: the app keeps working without persistence.
    }
  },
  remove(key: string): void {
    try {
      window.localStorage.removeItem(key)
    } catch {
      // Nothing to do.
    }
  },
  getJson<T>(key: string, fallback: T): T {
    const raw = storage.get(key)
    if (raw === null) return fallback
    try {
      return JSON.parse(raw) as T
    } catch {
      return fallback
    }
  },
  setJson(key: string, value: unknown): void {
    storage.set(key, JSON.stringify(value))
  },
}
