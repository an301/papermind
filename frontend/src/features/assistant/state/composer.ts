/**
 * Text another part of the panel wants in the composer ("Ask about this paper"
 * on an external-paper card fills it and focuses it; it never sends).
 */
import { create } from 'zustand'

interface ComposerPrefill {
  seq: number
  text: string
  prefill: (text: string) => void
}

export const useComposerPrefill = create<ComposerPrefill>()((set) => ({
  seq: 0,
  text: '',
  prefill: (text) => set((s) => ({ seq: s.seq + 1, text })),
}))
