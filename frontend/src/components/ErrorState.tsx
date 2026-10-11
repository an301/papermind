import type { Icon } from '@phosphor-icons/react'
import { WarningCircleIcon } from '@phosphor-icons/react'
import type { ReactNode } from 'react'
import { clsx } from '../lib/clsx'
import s from './ErrorState.module.css'

export interface ErrorStateProps {
  /** One or two sentences: the cause, then what to do (§5.25). */
  children: ReactNode
  /** WarningCircle by default. Pass null for states that need no icon. */
  icon?: Icon | null
  /**
   * `danger` only when something was lost or failed (§2.6); recoverable
   * states use the neutral icon colour.
   */
  tone?: 'danger' | 'warn' | 'neutral'
  /** The next step, usually a Secondary "Try again" button. */
  action?: ReactNode
  className?: string
}

/**
 * An error rendered where the thing would have been: one icon, the cause and
 * the next step. Never centred, never a modal, never "Something went wrong".
 */
export function ErrorState({ children, icon = WarningCircleIcon, tone = 'neutral', action, className }: ErrorStateProps) {
  const IconCmp = icon
  return (
    <div className={clsx(s.root, className)} role="alert">
      {IconCmp ? (
        <IconCmp size={16} aria-hidden="true" className={clsx(s.icon, tone === 'danger' && s.danger, tone === 'warn' && s.warn)} />
      ) : null}
      <div className={s.body}>
        <p className={s.text}>{children}</p>
        {action ? <div className={s.action}>{action}</div> : null}
      </div>
    </div>
  )
}
