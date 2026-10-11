import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { queryClient } from '../../api/queryClient'
import type { ChunkDetail, SourceRef } from '../../api/types'
import { clampPanelWidth, numeralsFor, passagesOnPage, stepZoom, useReaderStore } from './readerStore'

const chunk = (chunk_id: string, page: number): ChunkDetail => ({
  chunk_id,
  paper_id: '1706.03762',
  section_id: 'sec_3.2.1',
  section_number: '3.2.1',
  section_title: 'Scaled Dot-Product Attention',
  text: '…',
  page_start: page,
  page_end: page,
  boxes: [{ page, x0: 107.7, y0: 300, x1: 505.7, y1: 420 }],
})

const source = (label: string, chunk_id: string, page = 3): SourceRef => ({
  label,
  chunk_id,
  section_number: '3.2.1',
  section_title: 'Scaled Dot-Product Attention',
  page_start: page,
  page_end: page,
  snippet: '…',
})

describe('readerStore', () => {
  const realFetch = globalThis.fetch
  let resolvers: Record<string, (r: Response) => void> = {}

  beforeEach(() => {
    queryClient.clear()
    resolvers = {}
    globalThis.fetch = vi.fn(
      (url: string) =>
        new Promise<Response>((resolve) => {
          const id = decodeURIComponent(url.split('/chunks/')[1] ?? '')
          resolvers[id] = resolve
        }),
    ) as unknown as typeof fetch
    useReaderStore.getState().closePaper()
    useReaderStore.getState().openPaper('1706.03762')
  })
  afterEach(() => {
    globalThis.fetch = realFetch
  })

  const answer = (id: string, page: number) =>
    resolvers[id]!(new Response(JSON.stringify(chunk(id, page)), { status: 200, headers: { 'Content-Type': 'application/json' } }))

  it('requestScrollToChunk fetches the boxes, marks the passage, remembers where you were and asks the viewer to scroll', async () => {
    useReaderStore.getState().setPosition(10, 0.4)
    const pending = useReaderStore.getState().requestScrollToChunk('c1', { label: 'S1', messageId: 'm1', viaKeyboard: true })
    expect(useReaderStore.getState().pendingChunkId).toBe('c1')
    await vi.waitFor(() => expect(resolvers.c1).toBeDefined())
    answer('c1', 3)
    await expect(pending).resolves.toMatchObject({ chunk_id: 'c1' })

    const s = useReaderStore.getState()
    expect(s.pendingChunkId).toBeNull()
    expect(s.activeCitation).toEqual({ chunkId: 'c1', label: 'S1', messageId: 'm1' })
    expect(s.jumpHistory).toEqual([{ page: 10, offset: 0.4 }])
    expect(s.scrollRequest).toMatchObject({ reason: 'citation', chunkId: 'c1', focusBracket: true, boxes: [{ page: 3 }] })
    expect(passagesOnPage(s.passages, 3).map((p) => p.chunkId)).toEqual(['c1'])

    s.ackScroll(s.scrollRequest!.seq)
    expect(useReaderStore.getState().scrollRequest).toBeNull()
  })

  it('a newer jump wins over an older one that resolves late', async () => {
    const first = useReaderStore.getState().requestScrollToChunk('slow')
    const second = useReaderStore.getState().requestScrollToChunk('fast')
    await vi.waitFor(() => expect(resolvers.fast && resolvers.slow).toBeDefined())
    answer('fast', 5)
    await second
    answer('slow', 1)
    await expect(first).resolves.toBeNull()
    expect(useReaderStore.getState().scrollRequest?.chunkId).toBe('fast')
  })

  it('jumpBack returns to the position before the jump, once', () => {
    useReaderStore.setState({ jumpHistory: [{ page: 10, offset: 0.2 }] })
    expect(useReaderStore.getState().jumpBack()).toBe(true)
    expect(useReaderStore.getState().scrollRequest).toMatchObject({ reason: 'back', position: { page: 10, offset: 0.2 } })
    expect(useReaderStore.getState().jumpBack()).toBe(false)
  })

  it('showSources numbers passages by first appearance and keeps other answers’ pins', async () => {
    useReaderStore.setState({
      passages: [
        { chunkId: 'old', label: 'S1', numeral: 1, boxes: [], pageStart: 0, pageEnd: 0, sectionNumber: null, messageId: 'm0', pinned: true },
      ],
    })
    const done = useReaderStore
      .getState()
      .showSources('m1', [source('S1', 'a'), source('S2', 'b'), source('S3', 'unused')], {
        cited: ['S1', 'S2'],
        text: 'Scaling keeps softmax in range [S2], as the paper argues [S1].',
      })
    const marks = useReaderStore.getState().passages
    expect(marks.map((p) => [p.chunkId, p.numeral, p.pinned])).toEqual([
      ['old', 1, true],
      ['a', 2, false],
      ['b', 1, false],
    ])
    await vi.waitFor(() => expect(resolvers.a && resolvers.b).toBeDefined())
    answer('a', 3)
    answer('b', 4)
    await done
    expect(useReaderStore.getState().passages.find((p) => p.chunkId === 'b')?.boxes[0]?.page).toBe(4)
  })

  it('attachSelectionAsQuote moves the selection into the composer and asks for focus', () => {
    useReaderStore.getState().setSelection({ text: 'We suspect that…', page: 3, rects: [], sectionNumber: '3.2.1' })
    const before = useReaderStore.getState().composerFocusSeq
    useReaderStore.getState().attachSelectionAsQuote()
    const s = useReaderStore.getState()
    expect(s.quote).toEqual({ text: 'We suspect that…', page: 3, sectionNumber: '3.2.1' })
    expect(s.selection).toBeNull()
    expect(s.composerFocusSeq).toBe(before + 1)
  })

  it('requestAsk is consumed once', () => {
    useReaderStore.getState().setPanelOpen(false)
    useReaderStore.getState().requestAsk('Explain this passage.', { text: 'x', page: 1 })
    const req = useReaderStore.getState().askRequest!
    expect(useReaderStore.getState().panelOpen).toBe(true)
    useReaderStore.getState().consumeAsk(req.seq + 1)
    expect(useReaderStore.getState().askRequest).not.toBeNull()
    useReaderStore.getState().consumeAsk(req.seq)
    expect(useReaderStore.getState().askRequest).toBeNull()
  })
})

describe('helpers', () => {
  it('numeralsFor follows first appearance, then source order', () => {
    const n = numeralsFor([source('S1', 'a'), source('S2', 'b'), source('S3', 'c')], 'x [S3] y [S1] z [S3]')
    expect([...n.entries()]).toEqual([
      ['S3', 1],
      ['S1', 2],
      ['S2', 3],
    ])
  })

  it('stepZoom walks the §5.6 steps and stops at the ends', () => {
    expect(stepZoom(1, 1)).toBe(1.1)
    expect(stepZoom(1.03, -1)).toBe(1)
    expect(stepZoom(3, 1)).toBe(3)
    expect(stepZoom(0.5, -1)).toBe(0.5)
  })

  it('clampPanelWidth keeps the panel within 340–560', () => {
    expect(clampPanelWidth(200)).toBe(340)
    expect(clampPanelWidth(1000)).toBe(560)
    expect(clampPanelWidth(Number.NaN)).toBe(400)
  })
})
