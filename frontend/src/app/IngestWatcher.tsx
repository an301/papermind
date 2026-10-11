import { useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { usePapers } from '../api/queries'
import type { IngestStage, PaperSummary } from '../api/types'
import { toast } from '../components/Toast'
import { displayTitle, rememberStageDetail, stageAnnouncement } from '../features/library/paperText'
import { markRecentlyReady } from '../features/library/recentlyReady'
import { announce } from '../lib/announce'

const ORDER: IngestStage[] = ['downloading', 'extracting', 'sectioning', 'chunking', 'mapping', 'indexing', 'done']

/**
 * Follows ingestions wherever the reader is (DESIGN.md §5.4, §5.21):
 * - in the Library, each finished stage is announced through the live region
 *   (the rows themselves have no live region);
 * - anywhere else, a paper that becomes ready or fails gets a toast, because
 *   that happened away from where the reader is.
 */
export function IngestWatcher() {
  const { data } = usePapers()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const previous = useRef<Map<string, PaperSummary> | null>(null)

  useEffect(() => {
    if (!data) return
    data.forEach(rememberStageDetail)
    const before = previous.current
    previous.current = new Map(data.map((p) => [p.paper_id, p]))
    if (!before) return // first load: nothing changed, it was already like this

    const inLibrary = pathname === '/'
    for (const p of data) {
      const was = before.get(p.paper_id)
      if (!was || (was.status !== 'queued' && was.status !== 'processing')) continue
      const title = displayTitle(p)

      if (p.status === 'ready') {
        markRecentlyReady(p.paper_id)
        if (inLibrary) announce(`${title} is ready.`)
        else if (pathname !== `/paper/${p.paper_id}`)
          toast({
            message: `${title} is ready.`,
            action: { label: 'Open', onAction: () => navigate(`/paper/${encodeURIComponent(p.paper_id)}`) },
          })
        continue
      }
      if (p.status === 'failed') {
        if (inLibrary) announce(`Couldn’t finish preparing ${title}.`)
        else
          toast({
            message: `Couldn’t finish preparing ${title}.`,
            duration: Infinity,
            action: { label: 'Show', onAction: () => navigate('/') },
          })
        continue
      }
      // Still working: announce each stage that finished since the last poll.
      if (inLibrary && was.stage && p.stage && was.stage !== p.stage) {
        const from = ORDER.indexOf(was.stage)
        const to = ORDER.indexOf(p.stage)
        if (to > from) announce(stageAnnouncement(p, ORDER[to - 1]!))
      }
    }
  }, [data, pathname, navigate])

  return null
}
