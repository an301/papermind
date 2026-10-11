/**
 * "Remove from your papers" is optimistic with Undo (DESIGN.md §5.3, §5.21):
 * the row disappears at once, a toast offers Undo for 6 s, and the DELETE is
 * only sent when the toast goes away without Undo. If the page is closed in
 * the meantime the DELETE still goes out (keepalive), because the reader
 * already saw the paper leave.
 */
import { deletePaper, removePaperFromCache, upsertPaperInCache } from '../../api/queries'
import { queryClient } from '../../api/queryClient'
import { qk } from '../../api/keys'
import type { PaperSummary } from '../../api/types'
import { toast } from '../../components/Toast'
import { announce } from '../../lib/announce'
import { forgetProgress } from '../../lib/readingProgress'
import { displayTitle } from './paperText'

const pending = new Map<string, PaperSummary>()

function commit(paperId: string, keepalive = false): void {
  if (!pending.delete(paperId)) return
  forgetProgress(paperId)
  deletePaper(paperId, { keepalive })
    .catch(() => {
      // The server still has it: it comes back on the next fetch, which is the honest outcome.
    })
    .finally(() => void queryClient.invalidateQueries({ queryKey: qk.papers, exact: true }))
}

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => {
    for (const id of [...pending.keys()]) commit(id, true)
  })
}

/** Remove with Undo. */
export function removeWithUndo(paper: PaperSummary): void {
  const removed = removePaperFromCache(queryClient, paper.paper_id) ?? paper
  pending.set(paper.paper_id, removed)
  const title = displayTitle(removed)
  toast({
    message: `Removed ${title}.`,
    action: {
      label: 'Undo',
      onAction: () => {
        pending.delete(paper.paper_id)
        upsertPaperInCache(queryClient, removed)
        announce(`${title} is back in your papers.`)
      },
    },
    onClose: (reason) => {
      if (reason !== 'action') commit(paper.paper_id)
    },
  })
}

/** Stop an ingestion (Cancel on a working row). No undo: re-adding is one paste. */
export function cancelIngestion(paper: PaperSummary): void {
  removePaperFromCache(queryClient, paper.paper_id)
  toast({ message: `Stopped adding ${displayTitle(paper)}.` })
  deletePaper(paper.paper_id)
    .catch(() => {})
    .finally(() => void queryClient.invalidateQueries({ queryKey: qk.papers, exact: true }))
}

/** Remove a failed row at once: there's nothing in it to lose. */
export function removeFailed(paper: PaperSummary): void {
  removePaperFromCache(queryClient, paper.paper_id)
  deletePaper(paper.paper_id)
    .catch(() => {})
    .finally(() => void queryClient.invalidateQueries({ queryKey: qk.papers, exact: true }))
}

/** True while a removal is waiting out its Undo window (the poll must not bring the row back). */
export const isPendingRemoval = (paperId: string): boolean => pending.has(paperId)
