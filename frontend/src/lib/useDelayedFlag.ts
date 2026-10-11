import { useEffect, useRef, useState } from 'react'

/**
 * Loading indicators per DESIGN.md §5.27: show nothing for the first 200 ms,
 * and once shown, keep it for at least 400 ms so it never flashes.
 */
export function useDelayedFlag(active: boolean, delayMs = 200, minMs = 400): boolean {
  const [shown, setShown] = useState(false)
  const shownAt = useRef(0)

  useEffect(() => {
    if (active && !shown) {
      const t = window.setTimeout(() => {
        shownAt.current = Date.now()
        setShown(true)
      }, delayMs)
      return () => window.clearTimeout(t)
    }
    if (!active && shown) {
      const left = Math.max(0, minMs - (Date.now() - shownAt.current))
      const t = window.setTimeout(() => setShown(false), left)
      return () => window.clearTimeout(t)
    }
    return undefined
  }, [active, shown, delayMs, minMs])

  return shown
}
