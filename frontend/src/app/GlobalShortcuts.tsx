import { useEffect } from 'react'
import { usePreferences } from '../lib/preferences'
import { isTypingTarget } from '../lib/platform'
import { openShortcuts } from './ShortcutsDialog'

/**
 * Shortcuts that work on every screen (§8.2 "Everywhere"). Single keys never
 * fire inside text fields and can be turned off in Settings (WCAG 2.1.4).
 * The Reader adds its own on top.
 */
export function GlobalShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key !== '?') return
      if (!usePreferences.getState().singleKeyShortcuts || isTypingTarget(e.target)) return
      if (document.querySelector('[role="dialog"]')) return
      e.preventDefault()
      openShortcuts({ instant: true })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  return null
}
