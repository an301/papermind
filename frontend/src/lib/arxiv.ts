/**
 * arXiv input normalisation, mirroring papermind/ingest/arxiv.py (ARXIV_ID_RE)
 * so the field can validate on paste without a round trip (DESIGN.md §5.4).
 *
 * Accepts 1706.03762, 1706.03762v5, arXiv:1706.03762, arxiv.org/abs/…,
 * arxiv.org/pdf/…v5, …/pdf/1706.03762.pdf, http(s):// and www. variants,
 * old-style ids like cs/0112017 or math.GT/0309136, the abs page's "Cite as"
 * line (arXiv:1706.03762 [cs.CL]) and the DOI (10.48550/arXiv.1706.03762).
 */
const ARXIV_ID_RE =
  /^\s*(?:(?:https?:\/\/)?(?:doi\.org\/)?10\.48550\/arxiv\.)?(?:arxiv:\s*)?(?:(?:https?:\/\/)?(?:www\.|export\.)?arxiv\.org\/(?:abs|pdf|html)\/)?(?:(?<new>\d{2}(?:0[1-9]|1[0-2])\.\d{4,5})|(?<archive>[a-z]+(?:-[a-z]+)?)(?:\.[a-z]{2,}(?:-[a-z]{2,})?)?\/(?<old>\d{2}(?:0[1-9]|1[0-2])\d{3}))(?<version>v[1-9]\d*)?(?:\.pdf)?\/?(?:[?#].*)?(?:\s*\[[\w.-]+\])?\s*$/i

export interface ArxivRef {
  /** Canonical, unversioned: '1706.03762', 'hep-th/9901001'. */
  arxivId: string
  /** 'v5' when the input pinned a version. */
  version: string | null
  /** What to show back to the reader, e.g. '1706.03762v5'. */
  display: string
  /** The library's paper_id for it (old-style slashes become underscores). */
  paperId: string
}

export function parseArxiv(raw: string): ArxivRef | null {
  const m = ARXIV_ID_RE.exec(raw ?? '')
  if (!m?.groups) return null
  const { new: modern, archive, old, version } = m.groups
  const arxivId = modern ?? `${archive!.toLowerCase()}/${old}`
  const v = version ? version.toLowerCase() : null
  return { arxivId, version: v, display: `${arxivId}${v ?? ''}`, paperId: arxivId.replace('/', '_') }
}

/** Cheap check for "the reader probably meant an arXiv id", used to pick the right error copy. */
export const looksLikeAttempt = (raw: string): boolean => raw.trim().length > 0
