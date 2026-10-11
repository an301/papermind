/**
 * Applies the theme and answer size from preferences to <html>.
 *
 * - `data-theme="light|dark"` only when the reader picked one; no attribute
 *   means "follow the system" (tokens.css handles prefers-color-scheme).
 * - The browser chrome colour (theme-color) follows the surface colour.
 * - Theme changes never animate (DESIGN.md §6.4): transitions are switched
 *   off for one frame around the change.
 */
import { useSyncExternalStore } from 'react'
import { usePreferences, type AnswerSize, type ThemeChoice } from '../lib/preferences'

const darkQuery = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null

export type ResolvedTheme = 'light' | 'dark'

export const resolveTheme = (choice: ThemeChoice): ResolvedTheme =>
  choice === 'system' ? (darkQuery?.matches ? 'dark' : 'light') : choice

function applyTheme(choice: ThemeChoice, animate: boolean): void {
  const root = document.documentElement
  if (!animate) root.classList.add('theme-switching')
  if (choice === 'system') delete root.dataset.theme
  else root.dataset.theme = choice
  // index.html ships one theme-color per scheme. For a forced theme, both
  // follow the computed --surface; for System, the originals come back.
  const surface = getComputedStyle(root).getPropertyValue('--surface').trim()
  document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((m) => {
    m.dataset.original ??= m.content
    m.content = choice === 'system' || !surface ? m.dataset.original : surface
  })
  if (!animate) {
    // Two frames: one to apply the new colours with transitions off, one to restore them.
    requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove('theme-switching')))
  }
}

function applyAnswerSize(size: AnswerSize): void {
  const root = document.documentElement
  if (size === 'm') delete root.dataset.answerSize
  else root.dataset.answerSize = size
}

/** Call once at startup. Returns an unsubscribe for tests. */
export function initTheme(): () => void {
  const { theme, answerSize } = usePreferences.getState()
  applyTheme(theme, true)
  applyAnswerSize(answerSize)
  const unsub = usePreferences.subscribe((s, prev) => {
    if (s.theme !== prev.theme) applyTheme(s.theme, false)
    if (s.answerSize !== prev.answerSize) applyAnswerSize(s.answerSize)
  })
  const onSystemChange = () => {
    if (usePreferences.getState().theme === 'system') applyTheme('system', false)
  }
  darkQuery?.addEventListener('change', onSystemChange)
  return () => {
    unsub()
    darkQuery?.removeEventListener('change', onSystemChange)
  }
}

function subscribeSystem(cb: () => void): () => void {
  darkQuery?.addEventListener('change', cb)
  return () => darkQuery?.removeEventListener('change', cb)
}

/** 'light' or 'dark' as actually shown, following the system when the choice is System. */
export function useResolvedTheme(): ResolvedTheme {
  const choice = usePreferences((s) => s.theme)
  const systemDark = useSyncExternalStore(subscribeSystem, () => darkQuery?.matches ?? false, () => false)
  return choice === 'system' ? (systemDark ? 'dark' : 'light') : choice
}
