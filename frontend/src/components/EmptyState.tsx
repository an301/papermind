import type { ReactNode } from 'react'
import { clsx } from '../lib/clsx'
import s from './EmptyState.module.css'

export interface EmptyStateProps {
  /** One sentence. Sofia, never serif, never centred (§1.4). */
  title: ReactNode
  /** 'page' = the page's own h1 at 24/650 (Library first run); 'section' = 16/600. */
  size?: 'page' | 'section'
  /** Heading element; the Library first run uses h1 because it replaces the page title. */
  as?: 'h1' | 'h2' | 'h3' | 'p'
  description?: ReactNode
  /** The field or controls that end the empty state. */
  children?: ReactNode
  className?: string
}

/**
 * "The empty state is a sentence and a field" (§1.4): no illustration, no
 * mascot, no centred hero. It sits at the same left edge as the content it
 * stands in for.
 */
export function EmptyState({ title, size = 'section', as, description, children, className }: EmptyStateProps) {
  const Heading = as ?? (size === 'page' ? 'h1' : 'h2')
  return (
    <div className={clsx(s.root, size === 'page' && s.page, className)}>
      <Heading className={clsx(s.title, size === 'page' ? s.titlePage : s.titleSection)}>{title}</Heading>
      {description ? <p className={s.description}>{description}</p> : null}
      {children ? <div className={s.body}>{children}</div> : null}
    </div>
  )
}
