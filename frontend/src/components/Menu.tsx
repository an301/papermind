import { CheckIcon, type Icon } from '@phosphor-icons/react'
import { DropdownMenu as M } from 'radix-ui'
import { useRef, useState, type ComponentProps, type ReactNode } from 'react'
import { clsx } from '../lib/clsx'
import f from './floating.module.css'
import { Kbd } from './Kbd'

export interface MenuProps {
  /** The trigger: usually an IconButton (DotsThree "More for …"). */
  trigger: ReactNode
  children: ReactNode
  align?: 'start' | 'center' | 'end'
  side?: 'top' | 'right' | 'bottom' | 'left'
  open?: boolean
  onOpenChange?: (open: boolean) => void
  modal?: boolean
  contentClassName?: string
}

/**
 * Radix DropdownMenu in the shared box (§5.26). Roving focus with arrows,
 * Home/End and type-ahead come from Radix. Pointer-opened menus scale in;
 * keyboard-opened ones appear at once.
 */
export function Menu({ trigger, children, align = 'end', side = 'bottom', open, onOpenChange, modal = false, contentClassName }: MenuProps) {
  const viaKeyboard = useRef(false)
  const [instant, setInstant] = useState(false)
  return (
    <M.Root
      open={open}
      onOpenChange={(o) => {
        if (o) setInstant(viaKeyboard.current)
        onOpenChange?.(o)
      }}
      modal={modal}
    >
      <M.Trigger
        asChild
        onPointerDown={() => {
          viaKeyboard.current = false
        }}
        onKeyDown={() => {
          viaKeyboard.current = true
        }}
      >
        {trigger}
      </M.Trigger>
      <M.Portal>
        <M.Content
          className={clsx(f.surface, f.menu, 'menu', contentClassName)}
          data-instant={instant || undefined}
          align={align}
          side={side}
          sideOffset={4}
          collisionPadding={16}
          loop
        >
          {children}
        </M.Content>
      </M.Portal>
    </M.Root>
  )
}

export interface MenuItemProps extends Omit<ComponentProps<typeof M.Item>, 'children'> {
  children: ReactNode
  icon?: Icon
  shortcut?: string
  /** Destructive: --danger text ("Remove from your papers"). */
  danger?: boolean
}

export function MenuItem({ children, icon: IconCmp, shortcut, danger, className, ...rest }: MenuItemProps) {
  return (
    <M.Item className={clsx(f.item, danger && f.itemDanger, className)} {...rest}>
      {IconCmp ? <IconCmp size={16} className={f.itemIcon} aria-hidden="true" /> : null}
      <span className={f.itemLabel}>{children}</span>
      {shortcut ? <Kbd className={f.itemShortcut}>{shortcut}</Kbd> : null}
    </M.Item>
  )
}

export interface MenuCheckboxItemProps extends Omit<ComponentProps<typeof M.CheckboxItem>, 'children'> {
  children: ReactNode
}

/** A checked item: a 16px check in a left column (§5.26). */
export function MenuCheckboxItem({ children, className, ...rest }: MenuCheckboxItemProps) {
  return (
    <M.CheckboxItem className={clsx(f.item, className)} {...rest}>
      <span className={f.itemCheck}>
        <M.ItemIndicator>
          <CheckIcon size={16} aria-hidden="true" />
        </M.ItemIndicator>
      </span>
      <span className={f.itemLabel}>{children}</span>
    </M.CheckboxItem>
  )
}

export function MenuSeparator() {
  return <M.Separator className={f.separator} />
}
