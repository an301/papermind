import { Dialog as D } from 'radix-ui'
import type { ReactNode } from 'react'
import { clsx } from '../lib/clsx'
import s from './Dialog.module.css'

export interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  /** One or two sentences under the title. */
  description?: ReactNode
  /** Anything else (a radio list, a table). */
  children?: ReactNode
  /** Right-aligned buttons: Cancel (Secondary) to the left of the confirm button. */
  actions?: ReactNode
  /** 560px instead of 440px (the shortcuts sheet). */
  wide?: boolean
  /** Opened from the keyboard (`?`): appear without animation (§5.23). */
  instant?: boolean
  /**
   * Focus the least destructive control first (§5.23). Pass a selector inside
   * the dialog, e.g. '[data-autofocus]'; defaults to Radix's first focusable.
   */
  initialFocus?: string
}

/** A modal dialog: focus trapped, returned to the trigger on close, Esc closes. */
export function Dialog({ open, onOpenChange, title, description, children, actions, wide, instant, initialFocus }: DialogProps) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className={s.overlay} data-instant={instant || undefined} />
        <D.Content
          className={clsx(s.content, wide && s.wide, 'dialog')}
          data-instant={instant || undefined}
          {...(description ? {} : { 'aria-describedby': undefined })}
          onOpenAutoFocus={(e) => {
            if (!initialFocus) return
            const target = (e.currentTarget as HTMLElement | null)?.querySelector<HTMLElement>(initialFocus)
            if (target) {
              e.preventDefault()
              target.focus()
            }
          }}
        >
          <D.Title className={s.title}>{title}</D.Title>
          {description ? <D.Description className={s.description}>{description}</D.Description> : null}
          {children ? <div className={s.body}>{children}</div> : null}
          {actions ? <div className={s.actions}>{actions}</div> : null}
        </D.Content>
      </D.Portal>
    </D.Root>
  )
}

export const DialogClose = D.Close
