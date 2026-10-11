import type { Icon } from '@phosphor-icons/react'
import type { ReactNode } from 'react'
import { clsx } from '../lib/clsx'
import s from './StatusBar.module.css'

export interface StatusBarProps {
  icon: Icon
  /** Colour of the icon only; the text is always --ink. */
  tone?: 'neutral' | 'warn' | 'success'
  children: ReactNode
  action?: ReactNode
  className?: string
}

/**
 * The 32px bar under the header (or at the top of the assistant panel):
 * offline, back online, and the missing API key (§5.25). --surface with a
 * 1px --line-subtle rule; it pushes content down rather than floating.
 */
export function StatusBar({ icon: IconCmp, tone = 'neutral', children, action, className }: StatusBarProps) {
  return (
    <div className={clsx(s.bar, className)}>
      <IconCmp size={16} aria-hidden="true" className={clsx(s.icon, tone === 'warn' && s.warn, tone === 'success' && s.success)} />
      <p className={s.text}>{children}</p>
      {action ? <div className={s.action}>{action}</div> : null}
    </div>
  )
}
