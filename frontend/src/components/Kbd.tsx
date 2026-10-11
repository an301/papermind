import type { ReactNode } from 'react'
import { clsx } from '../lib/clsx'
import s from './Kbd.module.css'

/**
 * A key or chord. Pass the platform text already resolved (use MOD_KEY from
 * lib/platform); chords are glued with a no-break space: `${MOD_KEY} F`.
 */
export function Kbd({ children, tone = 'default', className }: { children: ReactNode; tone?: 'default' | 'tooltip'; className?: string }) {
  return <kbd className={clsx(s.kbd, tone === 'tooltip' && s.onTooltip, className)}>{children}</kbd>
}
