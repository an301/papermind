# PaperMind frontend

React 19 + Vite 8 + TypeScript 6. The design spec is
[`docs/design/DESIGN.md`](../docs/design/DESIGN.md); the API contract is
`papermind/schemas/` (mirrored in `src/api/types.ts`, same names).

## Commands

```bash
npm install
npm run dev          # http://localhost:5173, proxies /api to http://localhost:8000
npm run build        # typecheck + production build into dist/
npm run preview      # serve dist/ (also proxies /api)
npm run typecheck
npm test             # vitest (jsdom): API client, SSE parser, user bootstrap, stores, components
npm run lint         # oxlint + scripts/check-copy.mjs (DESIGN.md §9 greps)
npm run e2e          # Playwright against the dev server, API mocked, installed Chrome
npm run screenshots  # every named UI state, light and dark, 1440 and 390 wide
```

`predev` and `prebuild` copy pdf.js's runtime files (cmaps, standard fonts,
wasm, ICC profiles) into `public/pdfjs/`, which git and the linters ignore.

Start the API with `make dev` from the repo root (or
`.venv/bin/uvicorn papermind.main:create_app --factory --port 8000`).

## Environment

| Variable | Read by | Meaning |
|---|---|---|
| `VITE_API_ORIGIN` | the browser bundle, at build time | Origin of the API without `/api` or a trailing slash, e.g. `https://api.papermind.example`. Empty means same origin. |
| `VITE_MAX_UPLOAD_MB` | the browser bundle | Upload limit shown and checked before uploading (default 50; keep it equal to the server's `MAX_UPLOAD_MB`). |
| `PAPERMIND_API_PROXY` | `vite` / `vite preview` only | Where `/api` is proxied in development (default `http://localhost:8000`). |

See `.env.example`.

## Deploying on Vercel

`vercel.json` rewrites every path to `index.html` (client-side routes such as
`/paper/1706.03762` load directly) and caches hashed assets for a year.

`vercel.json` can't proxy `/api` to an origin chosen per environment (a
rewrite needs a fixed URL), so the API origin is compiled in instead: set
`VITE_API_ORIGIN` in the Vercel project's environment variables and add the
Vercel origin to the API's `CORS_ORIGINS`. Requests then go straight to the
API; chat streams are plain `fetch` POSTs reading `text/event-stream`, so the
API (or its load balancer) must not buffer them (`X-Accel-Buffering: no`).

## Layout

```
src/
  api/          types.ts (wire types) · client.ts (fetch + typed errors) · sse.ts (SSE over POST, never retries)
                queries.ts (TanStack Query hooks) · keys.ts · queryClient.ts
  lib/          user bootstrap, preferences, reading progress, formatters, arXiv parsing, platform keys
  styles/       tokens.css (DESIGN.md §2.2, the only place a raw colour may appear) · base.css
  components/   primitives: Button, IconButton, TextField, SegmentedControl, Tooltip, Popover, Dialog, Menu, Toast …
  app/          App (providers, routes), AppShell (header, nav, status bars), error boundary, 404, shortcuts
  features/
    library/    Your papers (/)
    knowledge/  What you know (/know; /knowledge redirects)
    settings/   Settings (/settings)
    onboarding/ first-run questions, shown in the assistant panel and re-openable from Settings
    shared/     readerStore.ts: the contract between the Reader and the assistant panel (see its README)
    reader/     the Reader (/paper/:paperId), lazy-loaded
    assistant/  the assistant panel
e2e/            Playwright specs, fixtures from the real benchmark data, mock-api.mjs, scenarios.mjs
scripts/        copy-pdfjs-assets.mjs · check-copy.mjs · screenshots.mjs
```

## Testing without a backend

`e2e/mock-api.mjs` is a stateful fake of the whole API on top of
`page.route`: adding a paper moves it through the real ingestion stages one
poll at a time, knowledge can be added and forgotten, and `POST /chat` replays
`e2e/fixtures/chat-turn.sse`, a recorded-style turn in the order
`papermind/schemas/events.py` defines. `startSseReplay()` serves the same
stream with pauses between events, for watching the streaming UI.

Screenshots of named states (`e2e/scenarios.mjs`):

```bash
node scripts/screenshots.mjs --list
SHOTS_DIR=/tmp/shots node scripts/screenshots.mjs 'library-*' --theme=dark --width=390
```

Files are `<name>-<light|dark>-<1440|390>.png` in `$SHOTS_DIR` (default
`test-results/shots`). Phone shots use a 390×844 touch viewport at 2×. The
script fails if any page logs a console error.

## Lint notes

`check-copy.mjs` runs the §9 greps (raw hex outside `tokens.css`, `...`,
exclamation marks, banned words, gradients, `transition: all`, emoji,
backdrop blur, edge stripes). A few oxlint rules are off on purpose:
`prefer-tag-over-role` and `no-noninteractive-element-to-interactive-role`
(ARIA combobox, listbox and status patterns are deliberate),
`no-redundant-roles` (`<ul role="list">` keeps list semantics in Safari when
markers are removed) and `no-irregular-whitespace` (no-break spaces are part of
the typography: "p.&nbsp;4", "50&nbsp;MB", "⌘&nbsp;F").
