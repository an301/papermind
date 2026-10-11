import { FilePdfIcon } from '@phosphor-icons/react'
import { useEffect, useRef, useState } from 'react'
import { addFiles } from './addFlow'
import s from './DropOverlay.module.css'

type DragState = { kind: 'pdf'; count: number } | { kind: 'other' } | null

/** Files being dragged, judged from their types (browsers hide names until the drop). */
function judge(dt: DataTransfer | null): DragState {
  if (!dt || !Array.from(dt.types).includes('Files')) return null
  const items = Array.from(dt.items ?? []).filter((i) => i.kind === 'file')
  if (!items.length) return { kind: 'pdf', count: 1 }
  // Some browsers report no type while dragging; give those the benefit of the doubt.
  const pdfs = items.filter((i) => i.type === 'application/pdf' || i.type === '')
  return pdfs.length ? { kind: 'pdf', count: pdfs.length } : { kind: 'other' }
}

/**
 * The whole window is the drop target while the Library is open (§5.4). The
 * overlay appears and disappears with no animation.
 */
export function DropOverlay() {
  const [drag, setDrag] = useState<DragState>(null)
  const depth = useRef(0)

  useEffect(() => {
    const onEnter = (e: DragEvent) => {
      const state = judge(e.dataTransfer)
      if (!state) return
      e.preventDefault()
      depth.current += 1
      setDrag(state)
    }
    const onOver = (e: DragEvent) => {
      if (!judge(e.dataTransfer)) return
      e.preventDefault()
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
    }
    const onLeave = (e: DragEvent) => {
      if (!judge(e.dataTransfer)) return
      depth.current = Math.max(0, depth.current - 1)
      if (depth.current === 0) setDrag(null)
    }
    const onDrop = (e: DragEvent) => {
      if (!judge(e.dataTransfer)) return
      e.preventDefault()
      depth.current = 0
      setDrag(null)
      void addFiles(Array.from(e.dataTransfer?.files ?? []))
    }
    window.addEventListener('dragenter', onEnter)
    window.addEventListener('dragover', onOver)
    window.addEventListener('dragleave', onLeave)
    window.addEventListener('drop', onDrop)
    return () => {
      window.removeEventListener('dragenter', onEnter)
      window.removeEventListener('dragover', onOver)
      window.removeEventListener('dragleave', onLeave)
      window.removeEventListener('drop', onDrop)
    }
  }, [])

  if (!drag) return null
  return (
    <div className={s.overlay} aria-hidden="true">
      <div className={s.frame}>
        {drag.kind === 'pdf' ? (
          <>
            <FilePdfIcon size={20} className={s.icon} />
            <p className={s.text}>{drag.count > 1 ? `Drop to add ${drag.count} PDFs` : 'Drop to add this PDF'}</p>
          </>
        ) : (
          <p className={`${s.text} ${s.muted}`}>Only PDFs can be added</p>
        )}
      </div>
    </div>
  )
}
