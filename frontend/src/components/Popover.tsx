import { Popover as P } from 'radix-ui'
import type { ComponentProps, ReactNode } from 'react'
import { clsx } from '../lib/clsx'
import f from './floating.module.css'

export const Popover = P.Root
export const PopoverTrigger = P.Trigger
export const PopoverAnchor = P.Anchor
export const PopoverClose = P.Close

export interface PopoverContentProps extends ComponentProps<typeof P.Content> {
  children: ReactNode
  /** Appear without the scale-in (opened from the keyboard, §5.26). */
  instant?: boolean
  /** Width token, e.g. 'var(--combobox-w)'. */
  width?: string
}

/** The shared popover box: --raised, radius 12, padding 4, --shadow-md, offset 4 from the trigger. */
export function PopoverContent({ className, children, instant, width, style, sideOffset = 4, collisionPadding = 16, ...rest }: PopoverContentProps) {
  return (
    <P.Portal>
      <P.Content
        className={clsx(f.surface, f.popover, 'popover', className)}
        data-instant={instant || undefined}
        sideOffset={sideOffset}
        collisionPadding={collisionPadding}
        style={width ? { width, ...style } : style}
        {...rest}
      >
        {children}
      </P.Content>
    </P.Portal>
  )
}
