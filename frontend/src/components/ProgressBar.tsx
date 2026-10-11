import type { CSSProperties } from 'react'
import { clsx } from '../lib/clsx'
import s from './ProgressBar.module.css'

export interface ProgressBarProps {
  /** 0..1 */
  value: number
  /** Accessible name, e.g. "Uploading attention.pdf". */
  label: string
  className?: string
}

/**
 * Determinate progress for a byte transfer. Ingestion itself is shown as named
 * steps, not a bar (DESIGN.md §5.4); this line only covers the upload before
 * the server's steps begin, sitting where the page strip will be.
 */
export function ProgressBar({ value, label, className }: ProgressBarProps) {
  const v = Math.min(1, Math.max(0, value))
  return (
    <span
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(v * 100)}
      className={clsx(s.track, className)}
      style={{ '--value': v } as CSSProperties}
    >
      <span className={s.fill} />
    </span>
  )
}
