/**
 * Contents (DESIGN.md §5.7): the paper's sections with hanging numerals, the
 * current one marked. Depth reads from the numerals alone; there is no indent
 * chrome. A paper with no detected headings lists its pages instead.
 */
import type { PaperMeta } from '../../api/types'
import { sectionAtPage, useReaderStore } from '../shared/readerStore'
import s from './Outline.module.css'

export function Outline({ meta, onNavigate }: { meta: PaperMeta; onNavigate?: () => void }) {
  const currentPage = useReaderStore((st) => st.currentPage)
  const current = sectionAtPage(meta, currentPage)
  const go = (page: number, y: number, height: number) => {
    useReaderStore.getState().requestScrollToPosition({ page, offset: height ? Math.max(0, y / height) : 0 }, 'outline')
    onNavigate?.()
  }

  const sections = meta.sections
  return (
    <nav className={s.outline} aria-label="Contents">
      <h2 className={s.heading}>{sections.length ? 'Contents' : 'Pages'}</h2>
      {!sections.length ? <p className={s.note}>No section headings found, so this lists pages.</p> : null}
      <ul className={s.list} role="list">
        {sections.length
          ? sections.map((sec) => {
              const height = meta.page_sizes[sec.page]?.[1] ?? 0
              const isCurrent = current?.section_id === sec.section_id
              return (
                <li key={sec.section_id}>
                  <button
                    type="button"
                    className={s.row}
                    aria-current={isCurrent ? 'location' : undefined}
                    onClick={() => go(sec.page, sec.y, height)}
                  >
                    <span className={s.number}>{sec.number ?? ''}</span>
                    <span className={s.title}>{sec.title}</span>
                  </button>
                </li>
              )
            })
          : Array.from({ length: meta.num_pages }, (_, i) => (
              <li key={i}>
                <button
                  type="button"
                  className={s.row}
                  aria-current={currentPage === i ? 'location' : undefined}
                  onClick={() => go(i, 0, 0)}
                >
                  <span className={s.number} />
                  <span className={s.title}>Page {i + 1}</span>
                </button>
              </li>
            ))}
      </ul>
    </nav>
  )
}
