import type { CSSProperties } from 'react'
import { clsx } from '../lib/clsx'
import s from './Skeleton.module.css'

export interface SkeletonProps {
  /** CSS width: '60%' or 'var(--strip-w)'. */
  width: string
  /** Height in px; match the text it stands in for (19, 13, 12 …). */
  height: number
  className?: string
  style?: CSSProperties
}

/**
 * One static bar of a skeleton that mirrors the final layout exactly. Show it
 * through useDelayedFlag (after 200ms, for at least 400ms). The containing
 * region carries aria-busy; the bars themselves are hidden from assistive tech.
 */
export function Skeleton({ width, height, className, style }: SkeletonProps) {
  return <span className={clsx(s.bar, className)} style={{ width, height, ...style }} aria-hidden="true" />
}
