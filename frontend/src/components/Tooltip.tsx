import { Tooltip as T } from 'radix-ui'
import type { ReactElement, ReactNode } from 'react'
import { Kbd } from './Kbd'
import s from './Tooltip.module.css'

/**
 * Timing from DESIGN.md §5.24: opens after 600ms, the next one within 300ms
 * opens at once, closes instantly, opens immediately on keyboard focus.
 * Mount once near the root.
 */
export function TooltipProvider({ children }: { children: ReactNode }) {
  return (
    <T.Provider delayDuration={600} skipDelayDuration={300}>
      {children}
    </T.Provider>
  )
}

export interface TooltipProps {
  /** The label. Keep it short; it repeats the control's accessible name. */
  label: ReactNode
  /** Keyboard shortcut, already in platform text (e.g. `${MOD_KEY} F`). */
  shortcut?: string
  side?: 'top' | 'right' | 'bottom' | 'left'
  align?: 'start' | 'center' | 'end'
  /** Exactly one focusable child. Never a disabled control (§5). */
  children: ReactElement
  /** Render the trigger without a tooltip (e.g. a title that isn't truncated). */
  disabled?: boolean
}

/**
 * For icon-only buttons, truncated titles and the Depth descriptions only.
 * Never on touch, never on disabled controls, never with interactive content.
 */
export function Tooltip({ label, shortcut, side = 'bottom', align = 'center', children, disabled }: TooltipProps) {
  if (disabled) return children
  return (
    <T.Root>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content className={`${s.content} tooltip`} side={side} align={align} sideOffset={8} collisionPadding={8}>
          <span className={s.text}>{label}</span>
          {shortcut ? <Kbd tone="tooltip">{shortcut}</Kbd> : null}
        </T.Content>
      </T.Portal>
    </T.Root>
  )
}
