/**
 * Papers that finished preparing in the last 4 s show "Ready" with a check in
 * --success before the right column settles to "Not opened" (DESIGN.md §5.4).
 */
import { create } from 'zustand'

export const READY_FLASH_MS = 4000

export const useRecentlyReady = create<{ ids: Record<string, number> }>()(() => ({ ids: {} }))

export function markRecentlyReady(paperId: string): void {
  useRecentlyReady.setState((s) => ({ ids: { ...s.ids, [paperId]: Date.now() } }))
  window.setTimeout(() => {
    useRecentlyReady.setState((s) => {
      const { [paperId]: _gone, ...rest } = s.ids
      return { ids: rest }
    })
  }, READY_FLASH_MS)
}
