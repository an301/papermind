# Reader ↔ assistant contract

The Reader (`src/features/reader/`, the PDF viewer and its chrome) and the
assistant panel (`src/features/assistant/`) are built by different people and
never import each other. They meet in one zustand store,
[`readerStore.ts`](./readerStore.ts), plus the shared API layer in `src/api/`.

Read DESIGN.md §4.4 (layout), §5.6–5.20 (components) and §6.2–6.3 (the bracket
draw and the jump) first; this file only says who owns which piece of state.

```ts
import { useReaderStore } from '../shared/readerStore'
const level = useReaderStore((s) => s.level)            // subscribe to one field
useReaderStore.getState().requestScrollToChunk(id)      // call an action outside render
```

Subscribe to single fields (or use `useShallow`) so a scroll doesn't re-render
the thread.

## Ownership

| Field | Written by | Read by | Notes |
|---|---|---|---|
| `paperId`, `meta` | Reader (`openPaper` on route change, `setMeta` when `usePaper` resolves) | both | `meta` is null while the paper is still being prepared |
| `session` | Assistant (`setSession` after `POST /sessions` or `GET /sessions/{id}`) | both | Remember the id per paper in `localStorage` under `pm:session:{paperId}`; a 404 means it expired (24 h): create a new one |
| `level` | Assistant (Depth control, `setLevel`); Reader seeds it from `?depth=` | both | Sent as `ChatRequest.level`. Reader mirrors it to `?depth=` |
| `currentPage`, `currentOffset` | Reader (`setPosition` on scroll settle) | Assistant (locators, "You stopped at"), Reader | Reader also PATCHes `/sessions/{id}` `{scroll}` every 2 s while reading and mirrors `?p=` |
| `zoom`, `effectiveScale` | Reader | Reader (top bar) | `zoom` persists per device (`pm:zoom`) |
| `selection` | Reader (text layer, `setSelection`) | Reader (selection popover) | PDF points, top-left, 0-based pages |
| `quote` | Reader (`attachSelectionAsQuote` on "Ask about this" / A) and Assistant (`setQuote(null)` on send or remove) | Assistant (quote chip, `ChatRequest.quote`) | |
| `passages` | Assistant (`showSources` when an answer becomes active, `setPassagePinned`, `clearPassages` on a new question) | Reader (bracket layer, `passagesOnPage`) | The assistant's marks: strokes only (§1.1). Not the reader's highlighter fills |
| `activeCitation` | `requestScrollToChunk` (and Assistant `setActiveCitation`) | Assistant (active chip ring), Reader | |
| `pendingChunkId` | `requestScrollToChunk` | Assistant (optional busy chip) | |
| `jumpHistory` | `requestScrollToChunk`, `jumpBack` | Reader ("Back to p. 11" pill, Alt+←) | Max 20 |
| `scrollRequest` | anyone via `requestScrollToChunk`, `requestScrollToPosition`, `jumpBack` | Reader (performs it, then `ackScroll(seq)`) | |
| `askRequest` | Reader (`requestAsk("Explain this passage.", quote)` on E), anyone | Assistant (sends it, then `consumeAsk(seq)`) | Opens the panel |
| `composerFocusSeq` | anyone via `requestComposerFocus` (`/`, A) | Assistant (focuses the composer when it changes) | |
| `panelOpen`, `panelWidth` | Reader (top bar toggle, `]`, resize separator) | both | Width is clamped 340–560 and persists per device (`pm:panel-width`). `?panel=` mirrors `panelOpen` |
| `outlineOpen` | Reader | Reader | `?outline=` |

## The jump, end to end (DESIGN.md §5.15, §6.3)

1. A chip is clicked. Assistant calls
   `requestScrollToChunk(source.chunk_id, { label: 'S2', messageId, viaKeyboard })`.
2. The store fetches `GET /papers/{id}/chunks/{chunk_id}` through the shared
   query cache (one request per chunk per session), makes sure the passage is
   in `passages`, sets `activeCitation`, pushes the current position onto
   `jumpHistory`, and sets `scrollRequest = { seq, reason: 'citation', boxes, chunkId, behaviour: 'auto', focusBracket }`.
3. Reader sees the new `scrollRequest.seq`, scrolls so the first box sits 30%
   from the top (smooth within 2 viewport heights, instant beyond or under
   reduced motion), re-inks the bracket and fades the exact-span underline,
   shows the back pill, moves focus to the bracket if `focusBracket`, announces
   "Showing section 3.2.1, page 4", and calls `ackScroll(seq)`.
4. A second click before the first chunk arrives wins: the older jump resolves
   `null` and never scrolls.

A request made before the viewer has mounted (opening a paper from "Open it" in
the Library, say) stays in `scrollRequest` until the viewer acks it.

## Helpers

- `numeralsFor(sources, text)`: chip and bracket numerals by first appearance of `[S1]`… in the answer.
- `passagesOnPage(passages, page)`: what to bracket on one page.
- `sectionAtPage(meta, page)`: the section a page starts in, for locators ("You stopped at §3.2").
- `stepZoom(scale, ±1)`, `ZOOM_STEPS`, `clampPanelWidth`, `PANEL_MIN/MAX/DEFAULT`.

## Elsewhere, not in this store

- **Server state:** `src/api/queries.ts` (`usePaper`, `useSession`, `useCreateSession`,
  `useUpdateSession`, `useSuggestions`, `useChunk`, `useKnowledge`, `useUser`,
  `useUpdateUser`, …). Streaming answers: `streamChat(request, signal)` in
  `src/api/sse.ts` yields typed `ChatEvent`s and never retries.
- **Reading progress for the Library** (last page and section, pages read, marks
  per page): `src/lib/readingProgress.ts`. The Reader calls `recordOpen`,
  `recordPosition`, `markPagesRead` and `setMarks`.
- **Preferences** (theme, page mode in dark, answer size, Background labels,
  single-key shortcuts, one-time hint counters): `src/lib/preferences.ts`.
- **Announcements:** `announce()` from `src/lib/announce.ts`, the page's one polite live region.
- **Onboarding** (first paper ever, `user.onboarded === false`): mount it at the
  top of the panel in place of the empty state:
  ```tsx
  import { Onboarding, useNeedsOnboarding } from '../onboarding'
  const needs = useNeedsOnboarding()          // profile loaded and not yet onboarded or skipped
  {needs ? <Onboarding onDone={() => requestComposerFocus()} /> : <EmptyPanel />}
  ```
  It makes its own writes (PATCH level, POST knowledge/areas, PATCH onboarded)
  and keeps gap callouts' precondition honest: hide callouts while `needs` is
  true (§5.16). `LEVEL_OPTIONS` from the same module holds the three depth
  labels, onboarding descriptions and Depth tooltips (§5.9) for the Depth control.
  The Reader placeholder (`src/app/ReaderPlaceholder.tsx`) shows it mounted.
- **Shared primitives** for both features: `src/components/` (Button, IconButton
  with tooltip, TextField, SegmentedControl, Tooltip, Popover, Menu, Dialog,
  `toast()`, Skeleton, ProgressBar, Kbd, VisuallyHidden, EmptyState, ErrorState,
  StatusBar for the panel's offline bar, Spinner for busy buttons only) and the
  four custom glyphs in `src/components/glyphs.tsx` (BracketGlyph,
  CaretInsertGlyph, PinToPageGlyph, HighlighterGlyph). Keyboard sheet:
  `openShortcuts()` from `src/app/ShortcutsDialog.tsx` (More menu → Keyboard shortcuts).
- **The reader's own highlights and pins** (Appendix A12): stored per paper in
  `localStorage` by the Reader until the server keeps marks.

## localStorage keys

All keys start with `pm:`.

| Key | Owner | Value |
|---|---|---|
| `pm:user` | `src/lib/userId.ts` | anonymous user id; sent as `X-Reader-Id`, it decides which added papers the server shows |
| `pm:theme` | `src/lib/preferences.ts` | `light` \| `dark` (absent = system); read by `index.html` before first paint |
| `pm:prefs` | `src/lib/preferences.ts` | other preferences and hint counters |
| `pm:read:{paperId}` | `src/lib/readingProgress.ts` | reading progress for the Library |
| `pm:zoom` | this store | zoom choice |
| `pm:panel-width` | this store | docked panel width |
| `pm:session:{paperId}` | Assistant | session id for that paper |
| `pm:draft:{paperId}` | Assistant | composer draft (§5.20) |
| `pm:suggestions-hidden:{paperId}` | Assistant | "Hide suggestions" (§5.17) |
| `pm:marks:{paperId}` | Reader | highlights and pins (Appendix A12) |
