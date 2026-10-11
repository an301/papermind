# e2e: specs, the mock API and named UI states

Everything here runs without a backend and spends nothing: `mock-api.mjs`
answers every `/api/*` request from `fixtures/`, and Playwright drives the
installed Google Chrome (`channel: 'chrome'`).

```bash
npm run e2e                                  # all specs (desktop 1440×900 + phone 390×844)
npx playwright test e2e/library.spec.ts      # one file
node scripts/screenshots.mjs --list          # named states
SHOTS_DIR=/tmp/shots node scripts/screenshots.mjs 'knowledge-*' --theme=dark
```

## mock-api.mjs

```js
import { fixture, mockApi, startSseReplay } from './mock-api.mjs'
const api = await mockApi(page, {
  papers: 'papers-ingesting',   // fixture name or an array of PaperSummary rows
  user: 'user',                 // 'user' | 'user-new' (onboarded: false) | null (first visit)
  knowledge: 'knowledge',       // or 'knowledge-empty'
  health: 'health',             // 'health-no-key' | 'down' (every request fails at the network)
  session: 'full',              // POST /sessions for 1706.03762 returns the full conversation
  ingest: 'advance',            // in-flight papers move one stage per GET /papers poll
  delay: { 'GET /papers': 900 },              // ms, or Infinity to never answer (loading states)
  fail: { 'POST /papers': { status: 503, code: 'internal', message: '…' } },  // or 'network'
})
api.state.papers   // what the fake server holds now
api.state.calls    // ['GET /health', 'GET /users/:id', …]
api.state.bodies   // [{ key: 'POST /papers', body: { arxiv: '2006.11239' } }, …]
```

Route keys are `METHOD /path` with ids replaced: `/papers/:id`,
`/papers/:id/chunks/:chunk`, `/users/:id/knowledge/:concept`, `/sessions/:id`.

What it does, following `papermind/schemas/api.py`:

- **Papers:** `POST /papers` normalises the id like the server, answers 200 for
  a ready paper and 202 with a queued row otherwise; `2401.99999` fails with
  `not_found` at the download step. Uploads read the multipart filename and
  refuse non-PDF bytes (415 `not_a_pdf`). `GET /papers/:id/pdf` serves
  `data/papers/:id/source.pdf` from the repo when it exists.
- **Users and knowledge:** `POST /users` mints `u_new0000001`; a stored id the
  server doesn't know gets 404 `user_not_found`, like after a server reset.
  Knowledge add, forget and onboarding areas change the state (areas mark
  their foundation and core concepts, as `papermind/memory/users.py` does).
- **Concepts:** search over all 359 concepts (name, aliases), the 12 areas,
  and concept detail with prerequisites.
- **Sessions:** create, get and patch.
- **Chat:** `POST /chat` answers with `fixtures/chat-turn.sse` all at once.
  For a stream you can watch, start `startSseReplay({ gapMs: 60 })` and pass
  its `url` as `chatStreamUrl`; Playwright then forwards the POST to it and
  the events arrive one by one.

Reading progress for three papers (`pm:read:*`) and the stored user id are
written to `localStorage` before the app loads (once per tab).

## Fixtures

Built from the repository's real data, so titles, sections, chunk ids,
boxes and concept ids match what the server returns:

| File | What |
|---|---|
| `papers.json` | 8 ready benchmark papers + one indexing (140 of 212) + one failed upload |
| `papers-ingesting.json` | queued, downloading (no metadata yet), sectioning, indexing |
| `papers-failed.json` | not on arXiv, PDF couldn't be opened, processing failed, scanned PDF |
| `papers-many.json` | 14 papers (the filter appears from 12) with a 200-character title |
| `papers-empty.json` | first run |
| `metas.json` | `PaperMeta` for the ready papers (sections, page sizes) |
| `chunks-1706.03762.json` | every `ChunkDetail` of *Attention Is All You Need*, with boxes |
| `reading-progress.json` | `pm:read:{id}` records behind "You stopped at §3.2" and the page strips |
| `user.json`, `user-new.json` | onboarded reader, first-time reader |
| `knowledge.json`, `knowledge-empty.json` | onboarding, self-reported and inferred edges (some below the known threshold) |
| `areas.json`, `concepts.json`, `concept-tiers.json` | from `data/concepts/` |
| `session-1706.03762.json` | a two-turn conversation: one plain question, one from a selected passage, with sources, gaps, trail and follow-ups |
| `suggestions-1706.03762.json` | three starter questions (with math) |
| `chat-turn.sse` | one recorded-style turn in the order `papermind/schemas/events.py` defines: `turn.started`, parallel tool calls, `gaps`, `sources`, 49 `answer.delta`s, `answer.done`, `knowledge.updated`, `followups`, `usage`, `done` |
| `health.json`, `health-no-key.json` | server up; up but without an Anthropic key |

## Named states (scenarios.mjs)

Each entry is a route, mock options and an optional `setup(page)` that puts the
page into the state. Files come out as `<name>-<light|dark>-<1440|390>.png`;
phone shots are a 390×844 touch viewport at 2×, and every shot runs with
reduced motion so nothing is caught mid-animation. The script fails when a
page logs a console error, when one of the app's own files fails to load, or
when the page renders nothing.
