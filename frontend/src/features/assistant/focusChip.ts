/**
 * Bracket → chip (DESIGN.md §5.8): scroll the thread to the answer that made a
 * bracket and move focus to the chip with that label.
 */
export function focusCitationChip(messageId: string | null, label: string): boolean {
  const selector = `[data-chip-message="${CSS.escape(messageId ?? '')}"][data-chip-label="${CSS.escape(label)}"]`
  const chip = document.querySelector<HTMLElement>(selector) ?? document.querySelector<HTMLElement>(`[data-chip-label="${CSS.escape(label)}"]`)
  if (!chip) return false
  chip.scrollIntoView({ block: 'center', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
  chip.focus({ preventScroll: true })
  return true
}
