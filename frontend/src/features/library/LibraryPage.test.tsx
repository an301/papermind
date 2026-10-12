import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { queryClient } from '../../api/queryClient'
import { AppProviders } from '../../app/App'
import { installFakeApi, paper, type FakeApiOptions } from '../../test/fakeApi'
import { useAddHint } from './addFlow'
import { LibraryPage } from './LibraryPage'

const ATTENTION = paper({ paper_id: '1706.03762', title: 'Attention Is All You Need' })
const BERT = paper({
  paper_id: '1810.04805',
  title: 'BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding',
  authors: ['Jacob Devlin', 'Ming-Wei Chang'],
  published: '2018-10-11',
  num_pages: 16,
  added_at: '2026-10-06T18:40:00Z',
})

const realFetch = globalThis.fetch

function renderLibrary(options: FakeApiOptions = {}) {
  const api = installFakeApi(options)
  const user = userEvent.setup()
  render(
    <AppProviders>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<LibraryPage />} />
          <Route path="/paper/:paperId" element={<p>Reader for the paper</p>} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
  return { api, user }
}

const addField = () => screen.getByRole('textbox', { name: 'arXiv link or ID' })

describe('Library', () => {
  beforeEach(() => {
    queryClient.clear()
    useAddHint.setState({ hint: { kind: 'idle' } })
  })
  afterEach(() => {
    globalThis.fetch = realFetch
  })

  it('lists papers as links into the reader, with the reader’s place in each', async () => {
    window.localStorage.setItem(
      'pm:read:1706.03762',
      JSON.stringify({ paperId: '1706.03762', openedAt: '2026-10-09T14:20:00Z', lastPage: 3, lastSection: '3.2', pagesRead: [0, 1, 2, 3], highlightsByPage: {}, pinsByPage: {} }),
    )
    renderLibrary({ papers: [BERT, ATTENTION] })
    expect(await screen.findByRole('heading', { level: 1, name: 'Your papers' })).toBeInTheDocument()
    const attention = await screen.findByRole('link', { name: /Attention Is All You Need/ })
    expect(attention).toHaveAttribute('href', '/paper/1706.03762')
    expect(screen.getByText('You stopped at §3.2')).toBeInTheDocument()
    expect(screen.getByText('Not opened')).toBeInTheDocument()
    // Last opened first, then newest added.
    const links = screen.getAllByRole('link').filter((a) => a.getAttribute('href')?.startsWith('/paper/'))
    expect(links[0]).toHaveAttribute('href', '/paper/1706.03762')
  })

  it('first run is the start sentence and the field, nothing else', async () => {
    renderLibrary({ papers: [] })
    expect(await screen.findByRole('heading', { level: 1, name: 'Start with a paper you’ve been meaning to read.' })).toBeInTheDocument()
    expect(addField()).toBeInTheDocument()
    expect(screen.getByText(/to see how it works/)).toBeInTheDocument()
  })

  it('explains an input that isn’t an arXiv id, keeps the text, and sends nothing', async () => {
    const { api, user } = renderLibrary({ papers: [ATTENTION] })
    await screen.findByRole('link', { name: /Attention Is All You Need/ })
    await user.type(addField(), 'attention is all you need{Enter}')
    expect(await screen.findByText('That doesn’t look like an arXiv ID. Try one like 2401.12345, or drop a PDF.')).toBeInTheDocument()
    expect(addField()).toHaveValue('attention is all you need')
    expect(addField()).toHaveFocus()
    expect(api.state.calls).not.toContain('POST /papers')
  })

  it('adds a paper by link: posts the normalised id, clears the field and shows the new row at once', async () => {
    const { api, user } = renderLibrary({ papers: [ATTENTION] })
    await screen.findByRole('link', { name: /Attention Is All You Need/ })
    await user.type(addField(), 'https://arxiv.org/abs/2006.11239v2{Enter}')
    await waitFor(() => expect(api.state.bodies).toContainEqual({ key: 'POST /papers', body: { arxiv: '2006.11239v2' } }))
    await waitFor(() => expect(addField()).toHaveValue(''))
    expect(addField()).toHaveFocus()
    expect((await screen.findAllByText('Waiting to start…'))[0]).toBeInTheDocument()
  })

  it('offers the latest version or the pasted one when arXiv has a newer version, instead of a retry', async () => {
    const { api, user } = renderLibrary({
      papers: [ATTENTION],
      fail: {
        'POST /papers': {
          status: 422,
          code: 'newer_version',
          message: 'You pasted v1. The latest version is v2.',
          extra: { latest_version: 'v2', paper_id: '2006.11239' },
        },
      },
    })
    await screen.findByRole('link', { name: /Attention Is All You Need/ })
    await user.type(addField(), '2006.11239v1{Enter}')
    expect(await screen.findByText(/The latest version is v2/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add v2' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add v1' }))
    await waitFor(() => expect(api.fetchMock.mock.calls.some(([url]) => String(url).includes('keep_version=true'))).toBe(true))
  })

  it('says when a paper is already there, with a way to open it', async () => {
    const { api, user } = renderLibrary({ papers: [ATTENTION] })
    await screen.findByRole('link', { name: /Attention Is All You Need/ })
    await user.type(addField(), 'arXiv:1706.03762v7{Enter}')
    expect(await screen.findByText(/is already in your papers/)).toBeInTheDocument()
    expect(api.state.calls).not.toContain('POST /papers')
    await user.click(screen.getByRole('link', { name: 'Open it' }))
    expect(await screen.findByText('Reader for the paper')).toBeInTheDocument()
  })

  it('removes a paper with Undo, and sends no DELETE while Undo is still possible', async () => {
    const { api, user } = renderLibrary({ papers: [ATTENTION, BERT] })
    await screen.findByRole('link', { name: /Attention Is All You Need/ })
    await user.click(screen.getByRole('button', { name: 'More for Attention Is All You Need' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Remove from your papers' }))
    await waitFor(() => expect(screen.queryByRole('link', { name: /Attention Is All You Need/ })).not.toBeInTheDocument())
    const toast = (await screen.findAllByText('Removed Attention Is All You Need.'))[0]!
    expect(toast).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Undo' }))
    expect(await screen.findByRole('link', { name: /Attention Is All You Need/ })).toBeInTheDocument()
    expect(api.state.calls).not.toContain('DELETE /papers/:id')
  })

  it('shows a failed ingestion with its reason; Try again only where retrying can help', async () => {
    const notOnArxiv = paper({
      paper_id: '2401.99999',
      title: 'arXiv:2401.99999',
      authors: [],
      status: 'failed',
      stage: 'downloading',
      error: 'arXiv has no paper 2401.99999. Check the ID, or upload the PDF instead.',
      error_code: 'not_found',
    })
    const indexingFailed = paper({
      paper_id: '2112.10752',
      title: 'High-Resolution Image Synthesis with Latent Diffusion Models',
      status: 'failed',
      stage: 'indexing',
      error: 'Couldn’t finish preparing this paper: processing failed. Try adding it again.',
      error_code: 'ingest_failed',
    })
    const { api, user } = renderLibrary({ papers: [notOnArxiv, indexingFailed, ATTENTION] })
    const rowOf = async (text: RegExp) => (await screen.findByText(text)).closest('li[aria-labelledby]') as HTMLElement

    const missing = await rowOf(/arXiv has no paper 2401\.99999/)
    expect(within(missing).queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()

    const retryable = await rowOf(/processing failed/)
    await user.click(within(retryable).getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(api.state.bodies).toContainEqual({ key: 'POST /papers', body: { arxiv: '2112.10752' } }))

    await user.click(within(missing).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(api.state.calls).toContain('DELETE /papers/:id'))
    await waitFor(() => expect(screen.queryByText(/arXiv has no paper 2401\.99999/)).not.toBeInTheDocument())
  })

  it('offers a filter from 12 papers and says plainly when nothing matches', async () => {
    const many = Array.from({ length: 12 }, (_, i) =>
      paper({ paper_id: `2401.${String(10000 + i)}`, title: i === 0 ? 'Denoising Diffusion Probabilistic Models' : `Paper number ${i + 1}`, added_at: `2026-09-${String(10 + i).padStart(2, '0')}T10:00:00Z` }),
    )
    const { user } = renderLibrary({ papers: many })
    const filter = await screen.findByRole('searchbox', { name: 'Filter your papers' })
    await user.type(filter, 'diffusion')
    expect(screen.getAllByRole('link').filter((a) => a.getAttribute('href')?.startsWith('/paper/'))).toHaveLength(1)
    await user.clear(filter)
    await user.type(filter, 'proteins')
    expect(await screen.findByText(/No papers match ‘proteins’\./)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear filter' }))
    expect(filter).toHaveValue('')
  })

  it('says the server can’t be reached in place of the list, and tries again', async () => {
    const { user } = renderLibrary({ fail: { 'GET /papers': 'network' } })
    expect(await screen.findByText(/Can’t reach the PaperMind server/, {}, { timeout: 4000 })).toBeInTheDocument()
    installFakeApi({ papers: [ATTENTION] }) // the server is back
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('link', { name: /Attention Is All You Need/ })).toBeInTheDocument()
  })
})
