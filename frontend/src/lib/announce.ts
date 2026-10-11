/**
 * The page's single polite live region (DESIGN.md §8.1). Every announcement
 * goes through here: answer ready, jump target, copied, knowledge changes,
 * depth change, ingestion stage changes, offline and online.
 *
 *   announce('Answer ready, 2 citations.')
 *
 * <LiveRegion/> (src/components/LiveRegion.tsx) is mounted once by the app shell.
 */
import { create } from 'zustand'

interface AnnounceState {
  message: string
  /** Bumped on every call so repeating the same sentence is announced again. */
  seq: number
}

export const useAnnouncer = create<AnnounceState>()(() => ({ message: '', seq: 0 }))

export function announce(message: string): void {
  useAnnouncer.setState((s) => ({ message, seq: s.seq + 1 }))
}
