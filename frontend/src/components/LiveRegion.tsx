import { useEffect, useState } from 'react'
import { useAnnouncer } from '../lib/announce'
import { VisuallyHidden } from './VisuallyHidden'

/**
 * The page's single polite live region (DESIGN.md §8.1). Mounted once by the
 * app shell; everything else calls `announce()` from lib/announce.
 *
 * The text is cleared for a moment before each message so a repeated
 * sentence ("Copied") is read again.
 */
export function LiveRegion() {
  const { message, seq } = useAnnouncer()
  const [text, setText] = useState('')
  useEffect(() => {
    if (!seq) return undefined
    setText('')
    const t = window.setTimeout(() => setText(message), 60)
    return () => window.clearTimeout(t)
  }, [message, seq])
  return (
    <VisuallyHidden>
      <div role="status" aria-live="polite" aria-atomic="true" data-testid="live-region">
        {text}
      </div>
    </VisuallyHidden>
  )
}
