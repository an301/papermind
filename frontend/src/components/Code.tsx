import type { ReactNode } from 'react'
import s from './Code.module.css'

/** Inline code in UI copy ("start it with `make dev`"): mono 0.875em on --hover, radius 4 (§2.3). */
export function Code({ children }: { children: ReactNode }) {
  return (
    <code className={s.code} translate="no">
      {children}
    </code>
  )
}
