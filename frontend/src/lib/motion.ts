import { useSyncExternalStore } from 'react'

/**
 * prefers-reduced-motion, read once and kept current (DESIGN.md §6.5). It
 * decides the scroll mode for jumps and whether streamed words fade.
 */
const query = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null

export const prefersReducedMotion = (): boolean => query?.matches ?? false

function subscribe(cb: () => void): () => void {
  query?.addEventListener('change', cb)
  return () => query?.removeEventListener('change', cb)
}

export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, prefersReducedMotion, () => false)
}
