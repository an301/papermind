import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useReducedMotion } from '../../lib/motion'
import s from './Crossfade.module.css'

/**
 * Swap content with a 120ms crossfade (DESIGN.md §6.1): the outgoing text fades
 * out with a 2px blur while the new one fades in, in the same grid cell, so
 * nothing moves. `id` decides when the content counts as new. Under reduced
 * motion the content just swaps.
 */
export function Crossfade({
  id,
  children,
  className,
  itemClassName,
}: {
  id: string
  children: ReactNode
  className?: string
  /** Applied to the incoming and outgoing copies (a line clamp, say). */
  itemClassName?: string
}) {
  const reduced = useReducedMotion()
  const [prev, setPrev] = useState<{ id: string; node: ReactNode } | null>(null)
  const current = useRef({ id, node: children })
  const first = useRef(true)

  useEffect(() => {
    if (first.current) {
      first.current = false
      current.current = { id, node: children }
      return undefined
    }
    if (current.current.id === id) {
      current.current = { id, node: children }
      return undefined
    }
    const outgoing = current.current
    current.current = { id, node: children }
    if (reduced) return undefined
    setPrev(outgoing)
    const t = window.setTimeout(() => setPrev(null), 140)
    return () => window.clearTimeout(t)
  }, [id, children, reduced])

  return (
    <span className={`${s.stack} ${className ?? ''}`}>
      {prev ? (
        <span key={`out-${prev.id}`} className={`${s.out} ${itemClassName ?? ''}`} aria-hidden="true">
          {prev.node}
        </span>
      ) : null}
      <span key={`in-${id}`} className={`${prev ? s.in : s.still} ${itemClassName ?? ''}`}>
        {children}
      </span>
    </span>
  )
}
