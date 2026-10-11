import { useSyncExternalStore } from 'react'

const query = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(pointer: coarse)') : null

function subscribe(cb: () => void): () => void {
  query?.addEventListener('change', cb)
  return () => query?.removeEventListener('change', cb)
}

/** A touch screen is the main pointer: no hover, no drag and drop of files, 44px targets. */
export function useCoarsePointer(): boolean {
  return useSyncExternalStore(subscribe, () => query?.matches ?? false, () => false)
}
