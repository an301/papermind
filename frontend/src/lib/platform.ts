/** Platform-aware shortcut text (DESIGN.md §5.1): "⌘" on Apple platforms, "Ctrl" elsewhere. */
const nav: Navigator | undefined = typeof navigator === 'undefined' ? undefined : navigator

export const isApple: boolean = !!nav && /Mac|iPhone|iPad|iPod/i.test(
  (nav as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform ?? nav.platform ?? nav.userAgent,
)

export const MOD_KEY = isApple ? '⌘' : 'Ctrl'

/** True when focus is somewhere a typed letter belongs (single-key shortcuts must not fire there). */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  const tag = target.tagName
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (tag === 'INPUT') {
    const type = (target as HTMLInputElement).type
    return !['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'color', 'file'].includes(type)
  }
  return false
}

/** Modifier and key names as the platform writes them, for kbd and tooltips. */
export const SHIFT_KEY = isApple ? '⇧' : 'Shift'
export const ALT_KEY = isApple ? '⌥' : 'Alt'
export const ENTER_KEY = isApple ? '↵' : 'Enter'

/** Join a chord with a no-break space so it never wraps: chord(MOD_KEY, 'F') → "⌘ F". */
export const chord = (...keys: string[]): string => keys.join('\u00a0')
