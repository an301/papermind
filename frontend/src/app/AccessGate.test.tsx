import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ACCESS_HEADER, getAccessCode, setAccessCode } from '../api/access'
import { api } from '../api/client'
import { AccessGate } from './AccessGate'

const CODE = 'reading-group'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const health = (required: boolean) =>
  json({ status: 'ok', version: '0.1.0', checks: {}, access_code_required: required, paper_delete_allowed: !required })

/** A server that wants `CODE` on everything but /health. */
function server(required: boolean) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/api/health')) return health(required)
    const sent = (init?.headers as Record<string, string> | undefined)?.[ACCESS_HEADER]
    if (required && sent !== CODE) {
      return json({ error: { code: sent ? 'access_code_invalid' : 'access_code_required', message: 'no' } }, 401)
    }
    return url.endsWith('/api/access') ? new Response(null, { status: 204 }) : json([])
  })
}

function renderGate() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AccessGate>
        <p>Your papers</p>
      </AccessGate>
    </QueryClientProvider>,
  )
}

describe('AccessGate', () => {
  const realFetch = globalThis.fetch
  beforeEach(() => setAccessCode(null))
  afterEach(() => {
    globalThis.fetch = realFetch
  })

  it('stays out of the way when the server needs no code', async () => {
    globalThis.fetch = server(false) as unknown as typeof fetch
    renderGate()
    expect(await screen.findByText('Your papers')).toBeInTheDocument()
    expect(screen.queryByLabelText('Access code')).not.toBeInTheDocument()
  })

  it('asks for the code, explains a wrong one, and lets the right one in', async () => {
    const fetchMock = server(true)
    globalThis.fetch = fetchMock as unknown as typeof fetch
    const user = userEvent.setup()
    renderGate()

    const field = await screen.findByLabelText('Access code')
    expect(screen.queryByText('Your papers')).not.toBeInTheDocument()

    await user.type(field, 'wrong')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByText('That code isn’t right. Check it and try again.')).toBeInTheDocument()
    expect(getAccessCode()).toBeNull()

    await user.clear(field)
    await user.type(field, ` ${CODE} `)
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByText('Your papers')).toBeInTheDocument()
    expect(getAccessCode()).toBe(CODE)

    // Every later request carries it.
    await api('/papers')
    const [, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit]
    expect((init.headers as Record<string, string>)[ACCESS_HEADER]).toBe(CODE)
  })

  it('remembers the code, and asks again when the server stops accepting it', async () => {
    setAccessCode('old-code')
    globalThis.fetch = server(true) as unknown as typeof fetch
    renderGate()
    expect(await screen.findByText('Your papers')).toBeInTheDocument()

    await expect(api('/papers')).rejects.toThrow()
    expect(await screen.findByText('That code no longer works. Ask for the current one.')).toBeInTheDocument()
    expect(getAccessCode()).toBeNull()
  })
})
