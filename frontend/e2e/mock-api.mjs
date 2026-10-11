/**
 * A fake PaperMind API for Playwright, built on page.route. It serves the
 * fixtures in ./fixtures and keeps a little state, so the UI can add, remove
 * and update things without a backend or any API spend.
 *
 * Used by the e2e specs and by scripts/screenshots.mjs:
 *
 *   import { mockApi } from './mock-api.mjs'
 *   const api = await mockApi(page, { papers: 'papers-ingesting', ingest: 'advance' })
 *   await page.goto('/')
 *   api.state.papers              // what the "server" holds now
 *
 * Shapes follow papermind/schemas/api.py exactly. Errors use the API's
 * {"error": {"code", "message"}} body.
 */
import { existsSync, readFileSync } from 'node:fs'

const FIXTURES = new URL('./fixtures/', import.meta.url)
const REPO = new URL('../../', import.meta.url)

/** @param {string} name */
export function fixture(name) {
  const file = /\.(json|sse)$/.test(name) ? name : `${name}.json`
  const text = readFileSync(new URL(file, FIXTURES), 'utf8')
  return file.endsWith('.json') ? JSON.parse(text) : text
}

const clone = (v) => structuredClone(v)

/**
 * @typedef {object} MockOptions
 * @property {string | object[]} [papers]      fixture name ('papers', 'papers-empty', 'papers-ingesting', 'papers-failed', 'papers-many') or rows
 * @property {'user' | 'user-new' | null} [user]  the stored reader; null = first visit (no pm:user, POST /users creates 'user-new')
 * @property {string | object[]} [knowledge]   'knowledge' | 'knowledge-empty' or rows
 * @property {'health' | 'health-no-key' | 'down'} [health]  'down' = every request fails at the network
 * @property {'full' | 'empty'} [session]      what POST /sessions returns for 1706.03762
 * @property {'advance' | 'freeze'} [ingest]   'advance' moves in-flight papers one step per GET /papers
 * @property {Record<string, number>} [delay]  ms per route key, e.g. {'GET /papers': 900}; Infinity = never answer
 * @property {Record<string, {status: number, code: string, message: string} | 'network'>} [fail]  per route key
 * @property {boolean} [seedStorage]           write pm:user and reading progress into localStorage (default true)
 * @property {Record<string, string>} [storage] extra localStorage entries to seed
 * @property {string} [chatStreamUrl]         stream POST /chat from a real SSE server (scripts/screenshots.mjs runs one); default: fulfil the recorded stream at once
 */

/**
 * Route keys are "METHOD /path" with ids replaced by their kind, e.g.
 * 'GET /papers', 'POST /papers', 'GET /papers/:id', 'POST /users/:id/knowledge'.
 * @param {string} method
 * @param {string} path  without the /api prefix
 */
export function routeKey(method, path) {
  const p = path
    .replace(/^\/papers\/(?!upload$)[^/]+/, '/papers/:id')
    .replace(/\/chunks\/[^/]+$/, '/chunks/:chunk')
    .replace(/^\/users\/[^/]+/, '/users/:id')
    .replace(/\/knowledge\/(?!areas$)[^/]+$/, '/knowledge/:concept')
    .replace(/^\/concepts\/(?!areas$)[^/]+$/, '/concepts/:id')
    .replace(/^\/sessions\/[^/]+$/, '/sessions/:id')
  return `${method} ${p}`
}

// Metadata the fake arXiv "finds" for ids added during a test.
const ARXIV = {
  '2006.11239': { title: 'Denoising Diffusion Probabilistic Models', authors: ['Jonathan Ho', 'Ajay Jain', 'Pieter Abbeel'], published: '2020-06-19', num_pages: 25 },
  '1409.0473': { title: 'Neural Machine Translation by Jointly Learning to Align and Translate', authors: ['Dzmitry Bahdanau', 'Kyunghyun Cho', 'Yoshua Bengio'], published: '2014-09-01', num_pages: 15 },
  '2203.02155': { title: 'Training language models to follow instructions with human feedback', authors: ['Long Ouyang', 'Jeff Wu', 'Xu Jiang'], published: '2022-03-04', num_pages: 68 },
  '1607.06450': { title: 'Layer Normalization', authors: ['Jimmy Lei Ba', 'Jamie Ryan Kiros', 'Geoffrey E. Hinton'], published: '2016-07-21', num_pages: 14 },
}
const UNKNOWN_ARXIV = new Set(['2401.99999'])

// The ingestion stages in order, with the overall progress at the start of each
// (mirrors _STAGE_SPAN in papermind/api/ingest_jobs.py).
const STAGES = [
  ['downloading', 0.0],
  ['extracting', 0.15],
  ['sectioning', 0.4],
  ['chunking', 0.5],
  ['indexing', 0.6],
]

const ARXIV_RE =
  /^\s*(?:arxiv:\s*)?(?:(?:https?:\/\/)?(?:www\.|export\.)?arxiv\.org\/(?:abs|pdf|html)\/)?(?:(\d{2}(?:0[1-9]|1[0-2])\.\d{4,5})|([a-z]+(?:-[a-z]+)?)(?:\.[a-z]{2,}(?:-[a-z]{2,})?)?\/(\d{2}(?:0[1-9]|1[0-2])\d{3}))(v[1-9]\d*)?(?:\.pdf)?\/?(?:[?#].*)?\s*$/i

/**
 * @param {import('@playwright/test').Page} page
 * @param {MockOptions} [options]
 */
export async function mockApi(page, options = {}) {
  const opts = {
    papers: 'papers',
    user: /** @type {'user' | 'user-new' | null} */ ('user'),
    knowledge: 'knowledge',
    health: 'health',
    session: 'full',
    ingest: 'freeze',
    delay: {},
    fail: {},
    seedStorage: true,
    storage: {},
    ...options,
  }

  const metas = fixture('metas')
  const chunks = fixture('chunks-1706.03762')
  const concepts = fixture('concepts')
  const tiers = fixture('concept-tiers')
  const areas = fixture('areas')
  const areaName = Object.fromEntries(areas.map((a) => [a.area_id, a.name]))

  const state = {
    papers: clone(typeof opts.papers === 'string' ? fixture(opts.papers) : opts.papers),
    user: opts.user ? fixture(opts.user) : null,
    knowledge: clone(typeof opts.knowledge === 'string' ? fixture(opts.knowledge) : opts.knowledge),
    sessions: /** @type {Record<string, any>} */ ({}),
    /** Every request the page made, as route keys, in order. */
    calls: /** @type {string[]} */ ([]),
    /** Bodies of mutating requests, keyed like `calls`. */
    bodies: /** @type {{key: string, body: any}[]} */ ([]),
  }
  if (opts.session === 'full') {
    const s = fixture('session-1706.03762')
    state.sessions[s.session_id] = s
  }

  if (opts.seedStorage) {
    const entries = { ...opts.storage }
    if (state.user) entries['pm:user'] = state.user.user_id
    for (const [id, progress] of Object.entries(fixture('reading-progress'))) {
      if (state.papers.some((p) => p.paper_id === id)) entries[`pm:read:${id}`] = JSON.stringify(progress)
    }
    // Seed once per tab: later navigations keep whatever the app wrote.
    await page.addInitScript((e) => {
      try {
        if (sessionStorage.getItem('pm:e2e-seeded')) return
        sessionStorage.setItem('pm:e2e-seeded', '1')
        for (const [k, v] of Object.entries(e)) localStorage.setItem(k, v)
      } catch {
        /* storage unavailable */
      }
    }, entries)
  }

  const json = (route, body, status = 200) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  const error = (route, status, code, message, extra = {}) =>
    json(route, { error: { code, message, ...extra } }, status)
  const noContent = (route) => route.fulfill({ status: 204, body: '' })

  const summaryOf = (id) => state.papers.find((p) => p.paper_id === id)

  function advance(paper) {
    if (paper.status !== 'queued' && paper.status !== 'processing') return
    if (paper.status === 'queued') {
      Object.assign(paper, { status: 'processing', stage: 'downloading', progress: 0.02 })
      return
    }
    const i = STAGES.findIndex(([s]) => s === paper.stage)
    if (UNKNOWN_ARXIV.has(paper.paper_id)) {
      Object.assign(paper, {
        status: 'failed',
        error_code: 'not_found',
        error: `arXiv has no paper ${paper.paper_id}. Check the ID, or upload the PDF instead.`,
      })
      return
    }
    if (paper.stage === 'downloading') {
      const found = ARXIV[paper.paper_id]
      if (found) Object.assign(paper, found)
    }
    if (paper.stage === 'indexing') {
      const total = paper.stage_detail?.total ?? 212
      const done = Math.min(total, (paper.stage_detail?.done ?? 0) + 71)
      if (done < total) {
        Object.assign(paper, { stage_detail: { done, total }, progress: 0.6 + 0.39 * (done / total) })
        return
      }
      Object.assign(paper, { status: 'ready', stage: 'done', progress: 1, stage_detail: null })
      return
    }
    const [stage, progress] = STAGES[Math.min(i + 1, STAGES.length - 1)]
    Object.assign(paper, { stage, progress, stage_detail: stage === 'indexing' ? { done: 0, total: 212 } : null })
  }

  function searchConcepts(q, limit) {
    const needle = q.trim().toLowerCase()
    const words = (n) => n.split(/[\s-]+/)
    // Names beat aliases; whole matches beat prefixes beat word prefixes beat substrings.
    const score = (c) => {
      const name = c.name.toLowerCase()
      const aliases = c.aliases.map((a) => a.toLowerCase())
      if (name === needle) return 0
      if (name.startsWith(needle)) return 1
      if (aliases.includes(needle)) return 2
      if (words(name).some((w) => w.startsWith(needle))) return 3
      if (aliases.some((a) => a.startsWith(needle))) return 4
      if (aliases.some((a) => words(a).some((w) => w.startsWith(needle)))) return 5
      if (name.includes(needle) || aliases.some((a) => a.includes(needle))) return 6
      return 9
    }
    return concepts
      .map((c) => [score(c), c])
      .filter(([s]) => s < 9)
      .sort((a, b) => a[0] - b[0] || a[1].name.localeCompare(b[1].name))
      .slice(0, limit)
      .map(([, c]) => c)
  }

  const resolveConcept = (raw) => {
    const n = String(raw).trim().toLowerCase()
    return concepts.find((c) => c.concept_id === n || c.name.toLowerCase() === n || c.aliases.some((a) => a.toLowerCase() === n))
  }

  function edge(c, source, confidence, evidence) {
    return {
      concept_id: c.concept_id,
      name: c.name,
      area: c.area,
      confidence,
      source,
      evidence,
      updated_at: new Date().toISOString(),
    }
  }

  function upsertKnowledge(e) {
    state.knowledge = [...state.knowledge.filter((k) => k.concept_id !== e.concept_id), e].sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }),
    )
  }

  async function handle(route) {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname.replace(/^\/api/, '') || '/'
    const method = req.method()
    const key = routeKey(method, path)
    state.calls.push(key)
    let body = null
    if (method !== 'GET' && method !== 'DELETE') {
      try {
        body = req.postDataJSON()
      } catch {
        body = req.postData()
      }
      state.bodies.push({ key, body })
    }

    if (opts.health === 'down') return route.abort('failed')
    const d = opts.delay[key]
    if (d === Infinity) return // never answer: the UI stays in its loading state
    if (d) await new Promise((r) => setTimeout(r, d))
    const f = opts.fail[key]
    if (f === 'network') return route.abort('failed')
    if (f) return error(route, f.status, f.code, f.message)

    let m
    // ---------------- health
    if (key === 'GET /health') return json(route, fixture(opts.health))

    // ---------------- papers
    if (key === 'GET /papers') {
      if (opts.ingest === 'advance') state.papers.forEach(advance)
      return json(route, state.papers)
    }
    if (key === 'POST /papers') {
      const raw = String(body?.arxiv ?? '')
      const mm = ARXIV_RE.exec(raw)
      if (!mm) return error(route, 422, 'invalid_input', 'That doesn’t look like an arXiv ID. Try one like 2401.12345, or drop a PDF.')
      const id = mm[1] ?? `${mm[2].toLowerCase()}_${mm[3]}`
      const existing = summaryOf(id)
      if (existing && existing.status !== 'failed') return json(route, existing, existing.status === 'ready' ? 200 : 202)
      const row = {
        paper_id: id,
        source: 'arxiv',
        title: `arXiv:${id}${mm[4] ?? ''}`,
        authors: [],
        published: null,
        num_pages: 0,
        status: 'queued',
        stage: null,
        progress: 0,
        stage_detail: null,
        error: null,
        error_code: null,
        added_at: new Date().toISOString(),
      }
      state.papers = [row, ...state.papers.filter((p) => p.paper_id !== id)]
      return json(route, row, 202)
    }
    if (key === 'POST /papers/upload') {
      const raw = req.postDataBuffer()?.toString('latin1') ?? ''
      const filename = /filename="([^"]+)"/.exec(raw)?.[1] ?? 'upload.pdf'
      if (!raw.includes('%PDF')) return error(route, 415, 'not_a_pdf', 'That file isn’t a PDF. PaperMind reads PDFs and arXiv papers.')
      const row = {
        paper_id: `up_${Math.random().toString(16).slice(2, 14)}`,
        source: 'upload',
        title: filename,
        authors: [],
        published: null,
        num_pages: 0,
        status: 'queued',
        stage: null,
        progress: 0,
        stage_detail: null,
        error: null,
        error_code: null,
        added_at: new Date().toISOString(),
      }
      state.papers = [row, ...state.papers]
      return json(route, row, 202)
    }
    if ((m = /^\/papers\/([^/]+)\/chunks\/(.+)$/.exec(path))) {
      const c = chunks[decodeURIComponent(m[2])]
      return c ? json(route, c) : error(route, 404, 'chunk_not_found', `Passage ${decodeURIComponent(m[2])} is not part of this paper.`)
    }
    if ((m = /^\/papers\/([^/]+)\/suggestions$/.exec(path))) {
      const s = fixture('suggestions-1706.03762')
      return json(route, { ...s, paper_id: decodeURIComponent(m[1]), level: url.searchParams.get('level') ?? 'intermediate' })
    }
    if ((m = /^\/papers\/([^/]+)\/pdf$/.exec(path))) {
      const file = new URL(`data/papers/${decodeURIComponent(m[1])}/source.pdf`, REPO)
      if (!existsSync(file)) return error(route, 404, 'pdf_not_found', 'The PDF for this paper is missing on the server.')
      return route.fulfill({ status: 200, contentType: 'application/pdf', body: readFileSync(file) })
    }
    if ((m = /^\/papers\/([^/]+)$/.exec(path))) {
      const id = decodeURIComponent(m[1])
      const summary = summaryOf(id)
      if (method === 'DELETE') {
        if (!summary) return error(route, 404, 'paper_not_found', `No paper with id ${id} is in the library. Add it first.`)
        state.papers = state.papers.filter((p) => p.paper_id !== id)
        return noContent(route)
      }
      if (!summary) return error(route, 404, 'paper_not_found', `No paper with id ${id} is in the library. Add it first.`)
      return json(route, { summary, meta: summary.status === 'ready' ? (metas[id] ?? null) : null })
    }

    // ---------------- users and knowledge
    if (key === 'POST /users') {
      state.user = { ...fixture('user-new'), created_at: new Date().toISOString() }
      return json(route, state.user)
    }
    if ((m = /^\/users\/([^/]+)(\/.*)?$/.exec(path))) {
      const id = decodeURIComponent(m[1])
      if (!state.user || state.user.user_id !== id) return error(route, 404, 'user_not_found', `No reader profile with id ${id}. Create one first.`)
      const rest = m[2] ?? ''
      if (rest === '' && method === 'GET') return json(route, state.user)
      if (rest === '' && method === 'PATCH') {
        for (const k of ['display_name', 'level', 'onboarded']) if (body?.[k] !== undefined && body?.[k] !== null) state.user[k] = body[k]
        return json(route, state.user)
      }
      if (rest === '/knowledge' && method === 'GET') return json(route, state.knowledge)
      if (rest === '/knowledge' && method === 'POST') {
        const c = resolveConcept(body?.concept ?? '')
        if (!c) return error(route, 404, 'concept_not_found', `“${body?.concept}” is not in the concept graph.`)
        const e = edge(c, 'self_reported', 0.9, 'The reader said they know this.')
        upsertKnowledge(e)
        return json(route, e)
      }
      if (rest === '/knowledge/areas' && method === 'POST') {
        const chosen = /** @type {string[]} */ (body?.areas ?? [])
        const unknown = chosen.filter((a) => !areaName[a])
        if (unknown.length) return error(route, 422, 'unknown_area', `Unknown area: ${unknown.join(', ')}.`)
        const added = []
        for (const c of concepts) {
          if (!chosen.includes(c.area) || tiers[c.concept_id] === 'advanced') continue
          const prev = state.knowledge.find((k) => k.concept_id === c.concept_id)
          if (prev && (prev.source === 'self_reported' || prev.confidence >= 0.9)) continue
          const e = edge(c, 'onboarding', 0.9, `Onboarding: comfortable with ${areaName[c.area]}.`)
          upsertKnowledge(e)
          added.push(e)
        }
        return json(route, added)
      }
      if ((m = /^\/knowledge\/(.+)$/.exec(rest)) && method === 'DELETE') {
        const cid = decodeURIComponent(m[1])
        if (!concepts.some((c) => c.concept_id === cid)) return error(route, 404, 'concept_not_found', `“${cid}” is not in the concept graph.`)
        state.knowledge = state.knowledge.filter((k) => k.concept_id !== cid)
        return noContent(route)
      }
    }

    // ---------------- concepts
    if (key === 'GET /concepts') {
      const q = url.searchParams.get('q') ?? ''
      if (!q.trim()) return error(route, 422, 'invalid_request', 'q must not be empty.')
      return json(route, searchConcepts(q, Number(url.searchParams.get('limit') ?? 20)))
    }
    if (key === 'GET /concepts/areas') return json(route, areas)
    if ((m = /^\/concepts\/(.+)$/.exec(path))) {
      const c = concepts.find((x) => x.concept_id === decodeURIComponent(m[1]))
      if (!c) return error(route, 404, 'concept_not_found', `“${decodeURIComponent(m[1])}” is not in the concept graph.`)
      const prerequisites = c.requires
        .map((id) => concepts.find((x) => x.concept_id === id))
        .filter(Boolean)
        .map((x) => ({ concept_id: x.concept_id, name: x.name }))
      const known = state.knowledge.some((k) => k.concept_id === c.concept_id && k.confidence >= 0.6)
      return json(route, { concept: c, prerequisites, known, learning_path: [] })
    }

    // ---------------- sessions and chat
    if (key === 'POST /sessions') {
      const full = fixture('session-1706.03762')
      const now = new Date().toISOString()
      const s =
        opts.session === 'full' && body?.paper_id === full.paper_id
          ? full
          : {
              session_id: `sess_${Math.random().toString(16).slice(2, 12)}`,
              user_id: body?.user_id,
              paper_id: body?.paper_id,
              level: body?.level ?? state.user?.level ?? 'intermediate',
              scroll: { page: 0, offset: 0, zoom: null },
              messages: [],
              created_at: now,
              updated_at: now,
            }
      state.sessions[s.session_id] = s
      return json(route, s)
    }
    if ((m = /^\/sessions\/([^/]+)$/.exec(path))) {
      const s = state.sessions[decodeURIComponent(m[1])]
      if (!s) return error(route, 404, 'session_not_found', 'This conversation expired. Start a new one.')
      if (method === 'PATCH') {
        if (body?.scroll) s.scroll = body.scroll
        if (body?.level) s.level = body.level
        s.updated_at = new Date().toISOString()
      }
      return json(route, s)
    }
    if (key === 'POST /chat') {
      if (opts.chatStreamUrl) return route.continue({ url: opts.chatStreamUrl })
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-cache' },
        body: fixture('chat-turn.sse'),
      })
    }
    if (key === 'POST /feedback') return noContent(route)

    return error(route, 404, 'not_found', `The mock API has no route for ${method} ${path}.`)
  }

  // Only the API: the app's own modules live under /src/api/ in dev and must load untouched.
  await page.route((url) => url.pathname.startsWith('/api/'), handle)
  return { state, options: opts }
}

/**
 * A minimal SSE server that replays a recorded stream with a pause between
 * events, so streaming UI can be watched (route.fulfill delivers all at once).
 * Returns { url, close }.
 * @param {{ gapMs?: number, file?: string }} [o]
 */
export async function startSseReplay(o = {}) {
  const { createServer } = await import('node:http')
  const gap = o.gapMs ?? 60
  const text = fixture(o.file ?? 'chat-turn.sse')
  const frames = text.split(/\n\n/).filter((f) => f.trim()).map((f) => `${f}\n\n`)
  const server = createServer((req, res) => {
    req.resume()
    res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' })
    let i = 0
    const tick = () => {
      if (i >= frames.length) return res.end()
      res.write(frames[i++])
      setTimeout(tick, gap)
    }
    tick()
  })
  await new Promise((r) => server.listen(0, '127.0.0.1', () => r(undefined)))
  const addr = /** @type {import('node:net').AddressInfo} */ (server.address())
  return { url: `http://127.0.0.1:${addr.port}/chat`, close: () => new Promise((r) => server.close(() => r(undefined))) }
}
