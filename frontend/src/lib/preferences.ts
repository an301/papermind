/**
 * Per-device reader preferences (Settings and the Appearance popover).
 * They apply instantly; there is no Save button (DESIGN.md §4.7).
 *
 * Stored in localStorage: the theme under `pm:theme` (index.html reads it
 * before first paint) and everything else under `pm:prefs`. The default depth
 * is not here: it is the server-side `UserProfile.level`.
 */
import { create } from 'zustand'
import { storage } from './storage'

export type ThemeChoice = 'system' | 'light' | 'dark'
/** How a PDF page looks in the dark theme (§2.2 PDF page tokens). Light theme always uses paper. */
export type PageMode = 'dimmed' | 'night' | 'inverted'
export type AnswerSize = 's' | 'm' | 'l'

export interface Preferences {
  theme: ThemeChoice
  pageMode: PageMode
  answerSize: AnswerSize
  /**
   * Run-in "Background" labels on uncited paragraphs. Off by default until the
   * eval gate in DESIGN.md §5.13 / Appendix A13 passes.
   */
  labelBackground: boolean
  /** E, A, H, 1–4, [, ], / and ? outside text fields (WCAG 2.1.4). */
  singleKeyShortcuts: boolean
  /**
   * How many times a one-time hint has been shown, keyed by hint name:
   * 'depth-help' (first 3 panel opens), 'selection-tip' (first 2 selections),
   * 'legend' (first cited answer, until "Got it").
   */
  seen: Record<string, number>
}

interface PreferencesStore extends Preferences {
  set: <K extends keyof Omit<Preferences, 'seen'>>(key: K, value: Preferences[K]) => void
  /** Count one more showing of a one-time hint; returns the new count. */
  bumpSeen: (hint: string, by?: number) => number
}

export const THEME_KEY = 'pm:theme'
const PREFS_KEY = 'pm:prefs'

const DEFAULTS: Preferences = {
  theme: 'system',
  pageMode: 'dimmed',
  answerSize: 'm',
  labelBackground: false,
  singleKeyShortcuts: true,
  seen: {},
}

function load(): Preferences {
  const saved = storage.getJson<Partial<Preferences>>(PREFS_KEY, {})
  const theme = storage.get(THEME_KEY)
  return {
    ...DEFAULTS,
    ...saved,
    seen: { ...(saved.seen ?? {}) },
    theme: theme === 'light' || theme === 'dark' ? theme : 'system',
  }
}

function save(p: Preferences): void {
  const { theme, ...rest } = p
  storage.setJson(PREFS_KEY, rest)
  if (theme === 'system') storage.remove(THEME_KEY)
  else storage.set(THEME_KEY, theme)
}

export const usePreferences = create<PreferencesStore>()((set, get) => ({
  ...load(),
  set: (key, value) => {
    set({ [key]: value } as Partial<Preferences>)
    save(pick(get()))
  },
  bumpSeen: (hint, by = 1) => {
    const next = (get().seen[hint] ?? 0) + by
    set({ seen: { ...get().seen, [hint]: next } })
    save(pick(get()))
    return next
  },
}))

function pick(s: PreferencesStore): Preferences {
  return {
    theme: s.theme,
    pageMode: s.pageMode,
    answerSize: s.answerSize,
    labelBackground: s.labelBackground,
    singleKeyShortcuts: s.singleKeyShortcuts,
    seen: s.seen,
  }
}

// Another tab changed a preference: follow it.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key === PREFS_KEY || e.key === THEME_KEY) usePreferences.setState(load())
  })
}
