# PaperMind design specification

Status: definitive for v1. Front-end engineers implement this exactly. If something here is wrong or impossible, change this file in the same PR as the code and say why in the PR description.

Direction: **The Annotated Copy** (judge totals A 24, B 19, C 19). It includes every must-fix from the three judges (traced in Appendix B) and grafts from Apparatus (B) and Office Hours (C) where they strengthen it.

Related contracts: `docs/architecture/ARCHITECTURE.md` (API, SSE events), `papermind/schemas/events.py` and `papermind/schemas/api.py` (wire shapes). Appendix A lists the small backend additions this design depends on.

Contents

1. Design intent, signature element, what we avoid
2. Tokens (ready-to-paste CSS) and contrast ratios
3. Icons
4. Layouts and wireframes
5. Component specs
6. Motion
7. Voice and microcopy
8. Accessibility checklist and keyboard map
9. Anti-slop review checklist
- Appendix A: backend contract additions
- Appendix B: must-fix traceability

---

## 1. Design intent

### 1.1 Intent

Anyone who has worked through a hard paper knows what their copy looks like afterwards: highlighter over the sentences they wanted to keep, pencil brackets beside the paragraphs that mattered, a small number pointing to a note in the margin. In PaperMind the PDF is **your copy** and the assistant panel is **its outer margin**. The assistant writes in a second hand.

One rule runs the whole system:

> **You mark with highlighter. The assistant marks with pencil.**
> Your marks are filled bands in your colours. The assistant's marks on the page are strokes only, in one ink (mulberry `--pencil`). Nothing the assistant leaves on the page is ever a fill.

The rule keeps the reading yours: in the margin-notes study, people's sense of ownership collapsed when the AI chose what to mark. It also gives a nervous undergrad something that feels like a friend's pencilled copy, and gives an expert line-level provenance they can check at a glance.

Posture:
- **The paper is the main thing.** The chrome steps back: neutral greys, small type, few icons. Mulberry appears only where the assistant has made a mark.
- **The assistant is a margin, not a chat.** Its answers are typeset prose with no bubbles, avatars or greeting. It never says "I". It talks about the paper, not about itself or its machinery.
- **Calm.** Nothing moves unless the reader did something, and every motion explains a cause and its effect.
- **Honest.** The UI shows which parts of an answer come from the paper and which don't, which tool steps ran, and what failed.

### 1.2 The signature element: the numbered pencil bracket

When an answer cites the paper, a thin mulberry bracket is drawn in the page margin beside exactly the cited lines. It carries a numeral that matches the citation chip in the answer: chip `2 §3.2.1` goes with bracket `2`.

- **Hover or focus a chip:** after 150 ms its bracket draws top to bottom over 220 ms, which pulls the eye from the panel to the page. Focus draws it immediately.
- **Click a chip:** the page scrolls to the passage. The bracket **re-inks**: a 2.25 px stroke over the resting 1.75 px stroke fades out over 1.2 s. A 1.25 px pencil underline under the exact cited lines fades out over the same 1.2 s. The page is never filled.
- **Hover or focus a bracket:** the exact-span underline comes back while you hold it, so an expert can re-check which lines were cited.
- **Click a bracket:** the panel scrolls back to the answer that made it, and focus returns to the chip.
- **Pin to page:** keeps an answer's brackets on your copy after you move on, so the copy builds up the way a real one does.

The bracket is the only hand-drawn gesture in the app; everything else is set type. **It has one meaning: "these exact words are from the paper."** It appears on the page beside cited lines, and in the panel beside a verbatim quote of the paper. It never appears anywhere else (not in the profile, not on headings, not as decoration).

### 1.3 Supporting decisions (short)

| Decision | Choice | Why |
|---|---|---|
| Reading face | Literata (opsz 7–72), for answers, verbatim quotes, paper titles and profile evidence only | Built for long reading on screens; real Greek, so "β-VAE" never falls back mid-sentence |
| UI face | Sofia Sans, for all chrome, labels and questions | Soft humanist sans; x-height .488 sits level with Literata's .507; has `tnum` (measured) |
| Light neutrals | Graphite with OKLCH chroma ≤ 0.005 at hue ≈ 295 | Mulberry is the only hue on screen. It is not cream, lilac or "brand-tinted" |
| Accent | Mulberry `#883564` (OKLCH 0.46 0.126 348) | Marks the assistant's ink. Kept clear of red-pen correction and of the error colour (57° away) |
| Primary actions, focus, selection | Ink (near-black graphite), never mulberry | Mulberry stays reserved for assistant marks |
| Light vs dark | Light is the default. Dark is a designed peer. Theme setting is System / Light / Dark | Positive polarity reads better. Dark-by-default is a tell |
| Depth control | "Depth": From basics · Standard · Technical | Describes the answer, not the person, and fits a 340 px panel |

### 1.4 What we deliberately avoid (product anti-slop list)

Merged from the research checklist, the judges' notes and this product's specific risks. Each item maps to a review check in §9.

**Colour**
- No indigo, violet or purple primary. No gradients of any kind, no gradient text.
- No cream, sand or parchment ground, and no terracotta or orange accent. The 2026 "calm reading app" cliché is cream + italic serif + terracotta, and we use none of the three.
- No hue-tinted near-black dark mode. The dark ramp is neutral graphite.
- No glows, coloured shadows, aurora blobs, radial halos or grid-line backgrounds.
- No mulberry outside assistant marks: no mulberry buttons, focus rings, selection, selected rows, toggles, links or logos.
- No red for "gaps" and no red for anything recoverable. The danger colour appears only as an icon, and only when work was lost or something failed.
- No colour-only meaning. Every state has a text label or a difference in form.

**Type**
- No Inter, Geist, Space Grotesk, Instrument Serif, Fraunces or Lora.
- No serif headings, page titles or empty states, and no italic display text. Literata appears only in reading prose (§2.3).
- No eyebrow labels, badge pills above headings, ALL-CAPS tracked labels, or "01 / 02" numbering unless there is a real sequence (sections, ordered steps).
- No crushed tracking on display sizes and no tracking on lowercase.
- Typography details are not optional: curly quotes, the `…` character, en dashes in ranges ("pp. 4–5"), `&nbsp;` in "p.&nbsp;4", "3.1&nbsp;s", "Fig.&nbsp;2", "50&nbsp;MB", and tabular figures on every count.

**Layout and surfaces**
- No card grids. The Library is a list, not a grid of PDF thumbnails.
- No cards nested in cards, no boxed messages, no chat bubbles.
- No coloured stripe on the left or top edge of anything. The prerequisite callout is an uncontained margin note.
- No hairline border and wide soft shadow on the same in-flow surface. In-flow surfaces are separated by tone steps; floating layers get a ring plus a two-layer shadow.
- No glassmorphism or backdrop blur.
- No stat banners, hero metrics, progress rings, streaks, XP or "Mastered" badges.

**Icons and imagery**
- No sparkles glyph, robot, brain or "AI" badge. No emoji anywhere in the UI.
- No illustrations, mascots or duotone empty-state art. The empty state is a sentence and a field.
- No second icon library. Only Phosphor, at a weight matched to the adjacent text.

**Motion**
- No staggered fade-in on load, no hover lift or scale on cards, rows or the PDF.
- No bounce, elastic or overshoot easing, and no `transition: all`.
- No shimmer text, pulsing dots, blinking or typing carets, or end-of-stream dot. No fake typing throttle.
- No animation on keyboard-triggered or high-frequency actions (send, depth change via keys, paging, theme change).

**Copy**
- No "AI-powered", "magic", "smart", unlock, supercharge, elevate, seamless, empower, harness, delve or world-class.
- No exclamation marks, no "Oops", no bare "Error", no "Something went wrong", no "How can I help you today?".
- No forced-contrast constructions ("not just X, but Y"). At most one em dash per screen; prefer commas and full stops.
- The assistant never says "I", never says "Great question", and never labels the reader ("as a beginner…").

**Product clutter we leave out of v1**
- No automatic highlights over the whole paper and no auto-summary on open.
- No Blog, Audio, Overview or Discussion tabs, and no model picker.
- No confidence percentages, meters or badges.
- No always-on focus bar.
- No toast for anything already shown inline.

---

## 2. Tokens

All tokens live in one file, `frontend/src/styles/tokens.css`. Components never use a raw colour, size, radius, shadow or duration. A value that isn't here gets added here first, in the same PR, with a reason.

### 2.1 Fonts (packages)

```bash
npm i @fontsource-variable/literata@5.3.0 @fontsource-variable/sofia-sans@5.3.0 @phosphor-icons/react@2.1.10
npm rm @fontsource-variable/inter @fontsource-variable/source-serif-4   # leftovers from the stack check
```

```ts
// frontend/src/main.tsx, before any CSS that uses the families
import '@fontsource-variable/literata/opsz.css'         // roman, wght 200–900 + opsz 7–72 → family 'Literata Variable'
import '@fontsource-variable/literata/opsz-italic.css'  // italic, only fetched when an <em> renders
import '@fontsource-variable/sofia-sans/wght.css'       // wght 1–1000 → family 'Sofia Sans Variable'
import 'katex/dist/katex.min.css'
```

- Weights in use. **Literata:** 380 (dark-mode answers), 400, 560 (paper titles), 600 (the cited sentence in a hover card). **Sofia Sans:** 400, 450, 500, 550, 600, 650. Nothing else.
- Subsets: Fontsource emits latin, latin-ext, greek, cyrillic and vietnamese faces with `unicode-range`. The browser downloads only what a page uses, so Greek arrives only when "θ" appears.
- Preload the two Latin files the first screen needs, in `index.html`:
  ```html
  <link rel="preload" as="font" type="font/woff2" crossorigin href="/assets/sofia-sans-latin-wght-normal.woff2">
  <link rel="preload" as="font" type="font/woff2" crossorigin href="/assets/literata-latin-opsz-normal.woff2">
  ```
  Resolve these hrefs at build time with `import url from '@fontsource-variable/…/files/…woff2?url'`. The packages use `font-display: swap`; keep it.
- Code uses the system monospace stack. There is no third webfont.
- Math is KaTeX 0.19, sized to match the x-height of the face around it (§2.3).
- Glyphs the fonts lack (`→ ↩ ↓ ⌘ ₖ √`) are never used in UI copy. Arrows come from Phosphor icons. Shortcuts render as `<kbd>` with platform text ("⌘" comes from the system font inside `<kbd>`, which is acceptable). Math symbols in suggested questions render through KaTeX (§5.17).

### 2.2 `tokens.css`

```css
/* ============================================================
   PaperMind tokens. Light is the default; dark is a designed peer.
   Theme: <html data-theme="light|dark"> when the user picks one;
   no attribute = follow the system (prefers-color-scheme).
   ============================================================ */

:root {
  color-scheme: light;

  /* ---------- surfaces (OKLCH chroma ≤ .005, hue ≈ 295) ---------- */
  --canvas:   #E6E6E8;  /* desk behind PDF pages; never holds controls directly */
  --surface:  #F7F6F9;  /* app ground: header, outline, panel, Library, profile, settings */
  --raised:   #FEFEFF;  /* composer, inputs, popovers, menus, hover cards, dialogs, toasts */

  /* ---------- text tiers ---------- */
  --ink:      #222123;  /* primary text, primary button fill, focus ring */
  --ink-2:    #5B5A5D;  /* secondary: metadata, trail, outline, labels, placeholders */
  --ink-3:    #868588;  /* disabled text and decorative marks only (3.4:1, not for reading) */
  --on-ink:   #F7F6F9;  /* text on an --ink fill */
  --ink-hover:#3B3A3C;  /* primary button hover = color-mix(in oklab, --ink 86%, --surface) */

  /* ---------- lines ---------- */
  --line:        #868588;  /* control borders: inputs, secondary buttons, segmented track (≥3:1) */
  --line-subtle: #CECDD0;  /* decorative hairlines: top-bar rule, table rules, chip ring = mix(--line 35%, --surface) */

  /* ---------- interaction tints (alpha of --ink, so they work on any neutral) ---------- */
  --hover:    rgb(34 33 35 / 0.05);  /* on surface → #ECEBEE */
  --selected: rgb(34 33 35 / 0.07);  /* on surface → #E8E7EA : selected row, active answer, current outline item */
  --pressed:  rgb(34 33 35 / 0.09);  /* on surface → #E4E3E6 */

  /* ---------- focus and selection (neutral, never mulberry) ---------- */
  --focus:        var(--ink);
  --focus-inner:  #FFFFFF;          /* two-tone ring over the PDF and over any variable background */
  --focus-outer:  var(--ink);
  --selection:    #D6D5D7;          /* UI ::selection = mix(in oklab, --ink 14%, --surface) */

  /* ---------- the assistant's ink: assistant marks ONLY (see §2.6) ---------- */
  --pencil:          #883564;       /* OKLCH .46 .126 348 · HSL 326° */
  --pencil-on-page:  #883564;       /* brackets/underlines on a light or dimmed page */

  /* ---------- status (icons + short labels only; see §2.6) ---------- */
  --danger:  #9A4312;  /* rust, OKLCH h 45: work lost, ingestion failed, destructive menu item */
  --warn:    #81520A;  /* ochre: needs attention, nothing lost (scanned PDF, withdrawn version) */
  --success: #286648;  /* green: ingestion finished ("Ready") and "Back online" only */

  /* ---------- tooltip, scrim ---------- */
  --tooltip-bg:  #222123;
  --tooltip-ink: #F7F6F9;
  --scrim:       rgb(34 33 35 / 0.32);

  /* ---------- shadows: one light source directly above (x = 0), tinted to --ink ---------- */
  --shadow-sm: 0 0 0 1px rgb(34 33 35 / .08), 0 1px 2px rgb(34 33 35 / .06), 0 4px 12px -4px rgb(34 33 35 / .12);
  --shadow-md: 0 0 0 1px rgb(34 33 35 / .08), 0 2px 4px -1px rgb(34 33 35 / .08), 0 12px 28px -8px rgb(34 33 35 / .18);
  --shadow-lg: 0 0 0 1px rgb(34 33 35 / .08), 0 4px 8px -2px rgb(34 33 35 / .08), 0 24px 56px -16px rgb(34 33 35 / .30);

  /* ---------- typography ---------- */
  --font-read: 'Literata Variable', 'Literata', Georgia, 'Times New Roman', serif;
  --font-ui:   'Sofia Sans Variable', 'Sofia Sans', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  --font-mono: ui-monospace, 'SF Mono', SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace;

  --text-12: 0.75rem;    /* 12px */
  --text-13: 0.8125rem;  /* 13px */
  --text-14: 0.875rem;   /* 14px */
  --text-15: 0.9375rem;  /* 15px */
  --text-16: 1rem;       /* 16px */
  --text-17: 1.0625rem;  /* 17px */
  --text-19: 1.1875rem;  /* 19px */
  --text-24: 1.5rem;     /* 24px */

  --answer-size: var(--text-17);  /* user setting: 15.5px (0.96875rem) | 17px | 19px (1.1875rem) */
  --answer-leading: 1.55;         /* 1.5 | 1.55 | 1.6 for S | M | L */
  --answer-weight: 400;           /* 380 in dark */
  --answer-measure: 32em;         /* ≈ 67 characters of Literata (measured); never set measure in ch */
  --katex-in-read: 1.18em;        /* KaTeX_Main x-height .431 → Literata .507 */
  --katex-in-ui:   1.13em;        /* → Sofia Sans .488 */

  /* ---------- spacing: one scale, nothing off it ---------- */
  --space-1: 4px;  --space-2: 8px;  --space-3: 12px; --space-4: 16px;
  --space-5: 24px; --space-6: 32px; --space-7: 48px; --space-8: 64px;

  /* ---------- radii: three values + paper; children use calc(parent − padding) ---------- */
  --r-page: 2px;   /* PDF pages: paper is paper */
  --r-xs:   4px;   /* chips, kbd, inline code, highlight marks */
  --r-sm:   8px;   /* buttons, inputs, rows, segmented track, tooltips, back pill */
  --r-md:   12px;  /* composer, popovers, menus, hover cards, toasts, dialogs, sheets (top corners) */

  /* ---------- z-index ---------- */
  --z-topbar: 10;  --z-panel-header: 11; --z-rail: 20;   --z-drawer: 30;
  --z-sheet: 40;   --z-backpill: 45;     --z-popover: 50; --z-toast: 60;
  --z-scrim: 69;   --z-dialog: 70;       --z-tooltip: 80; --z-dropzone: 85; --z-skiplink: 90;
  /* inside a PDF page (page-local stacking context): */
  --zp-canvas: 0; --zp-highlights: 1; --zp-underline: 2; --zp-text: 3; --zp-brackets: 4; --zp-links: 5;

  /* ---------- motion ---------- */
  --ease-out:    cubic-bezier(0.23, 1, 0.32, 1);    /* entries, UI feedback, bracket draw */
  --ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);   /* things moving on screen (thumb, scroll) */
  --ease-drawer: cubic-bezier(0.32, 0.72, 0, 1);    /* panel, drawers, sheets */
  --dur-press:  120ms;  --dur-fast: 150ms;  --dur-base: 200ms;  --dur-slow: 280ms;
  --dur-draw:   220ms;  /* bracket draw */
  --dur-undraw: 150ms;
  --dur-settle: 1200ms; /* re-ink and exact-span underline fade after a jump */
  --dur-word:   150ms;  /* streamed word opacity */
  --dur-meta:   120ms;  /* Library row metadata fill-in */
  --delay-bracket: 150ms; --delay-card: 400ms; --grace-card: 150ms;
  --delay-tooltip: 600ms; --skip-tooltip: 300ms;
  --delay-loader: 200ms;  --min-loader: 400ms;

  /* ---------- layout ---------- */
  --topbar-h: 48px;      /* Reader top bar */
  --appbar-h: 56px;      /* Library / profile / settings header */
  --outline-w: 232px;
  --panel-w: 400px;  --panel-min: 340px;  --panel-max: 560px;
  --panel-sheet-w: 420px;  /* tablet overlay */
  --rail-w: 48px;
  --lane-w: 32px;          /* bracket lane on each side of every page (20px on phones) */
  --page-fit-max: 920px;   /* page width cap at "Fit width" (user zoom may exceed) */
  --page-gap: 16px;        /* between pages */
  --page-panel-gap: 48px;  /* page edge to panel on wide screens */
  --pdf-min: 620px;        /* the PDF column never gets narrower while the panel is docked */
  --col-max: 46rem;        /* Library, profile: 736px */
  --settings-max: 40rem;   /* 640px */
  --inset: clamp(16px, 5vw, 96px);  /* left inset of single-column pages; matches the wordmark */
}

/* ---------- dark: system preference (unless the user forced light) ---------- */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --canvas:#171719; --surface:#1F1F21; --raised:#28282A;
    --ink:#E6E6E8; --ink-2:#BABABD; --ink-3:#79787B; --on-ink:#1F1F21; --ink-hover:#C7C7C9;
    --line:#79787B; --line-subtle:#3C3C3E;
    --hover:rgb(230 230 232 / 0.05); --selected:rgb(230 230 232 / 0.07); --pressed:rgb(230 230 232 / 0.09);
    --focus-inner:#1F1F21; --focus-outer:#E6E6E8; --selection:#414143;
    --pencil:#DCA4C0; --pencil-on-page:#883564;
    --danger:#E79D7B; --warn:#E4B572; --success:#88C99E;
    --tooltip-bg:#3D3D3F; --tooltip-ink:#E6E6E8; --scrim:rgb(0 0 0 / 0.56);
    --shadow-sm: 0 0 0 1px rgb(255 255 255 / .10), 0 1px 2px rgb(0 0 0 / .32), 0 4px 12px -4px rgb(0 0 0 / .48);
    --shadow-md: 0 0 0 1px rgb(255 255 255 / .10), 0 2px 4px -1px rgb(0 0 0 / .36), 0 12px 28px -8px rgb(0 0 0 / .60);
    --shadow-lg: 0 0 0 1px rgb(255 255 255 / .12), 0 4px 8px -2px rgb(0 0 0 / .40), 0 24px 56px -16px rgb(0 0 0 / .70);
    --answer-weight: 380;
  }
}

/* ---------- dark: explicit choice (same values; keep both blocks in sync) ---------- */
:root[data-theme="dark"] {
  color-scheme: dark;
  --canvas:#171719; --surface:#1F1F21; --raised:#28282A;
  --ink:#E6E6E8; --ink-2:#BABABD; --ink-3:#79787B; --on-ink:#1F1F21; --ink-hover:#C7C7C9;
  --line:#79787B; --line-subtle:#3C3C3E;
  --hover:rgb(230 230 232 / 0.05); --selected:rgb(230 230 232 / 0.07); --pressed:rgb(230 230 232 / 0.09);
  --focus-inner:#1F1F21; --focus-outer:#E6E6E8; --selection:#414143;
  --pencil:#DCA4C0; --pencil-on-page:#883564;
  --danger:#E79D7B; --warn:#E4B572; --success:#88C99E;
  --tooltip-bg:#3D3D3F; --tooltip-ink:#E6E6E8; --scrim:rgb(0 0 0 / 0.56);
  --shadow-sm: 0 0 0 1px rgb(255 255 255 / .10), 0 1px 2px rgb(0 0 0 / .32), 0 4px 12px -4px rgb(0 0 0 / .48);
  --shadow-md: 0 0 0 1px rgb(255 255 255 / .10), 0 2px 4px -1px rgb(0 0 0 / .36), 0 12px 28px -8px rgb(0 0 0 / .60);
  --shadow-lg: 0 0 0 1px rgb(255 255 255 / .12), 0 4px 8px -2px rgb(0 0 0 / .40), 0 24px 56px -16px rgb(0 0 0 / .70);
  --answer-weight: 380;
}

/* ============================================================
   PDF page tokens. Set on the viewer element:
   <div class="pdf-viewer" data-page-mode="paper|dimmed|night|inverted">
   Light theme always uses "paper". Dark theme defaults to "dimmed"
   (Settings → Page in dark mode). These tokens are palette-independent.
   ============================================================ */
.pdf-viewer {
  /* paper (default in light theme) */
  --page-canvas-filter: none;
  --hl-blend: multiply;
  --hl-yellow: #FDEB9C;  --hl-green: #C4E8C6;  --hl-blue: #C6E0F7;  --hl-orange: #FAD9B0;
  --pdf-selection: rgb(34 33 35 / 0.22);   /* text-layer ::selection, neutral graphite */
  --search-match: #5B5A5D;                 /* 1.5px outline on every match */
  --search-current: #222123;               /* 2px outline + --pdf-selection fill on the current match */
  --bracket: var(--pencil-on-page);        /* bracket strokes, numerals, exact-span underline */
}
.pdf-viewer[data-page-mode="dimmed"] {     /* dark-theme default: the page stays light, dimmed */
  --page-canvas-filter: brightness(0.85);  /* applied to the pdf.js <canvas> ONLY, never to overlays */
  --hl-yellow: #FEF0B4;  --hl-green: #D5EFD6;  --hl-blue: #D6E9FA;  --hl-orange: #FCE3C6;
  --search-match: #5B5A5D;  --search-current: #222123;
  --bracket: #883564;
}
.pdf-viewer[data-page-mode="night"] {      /* opt-in; pdf.js pageColors { background:'#1F1F21', foreground:'#D9D9DB' } */
  --page-canvas-filter: none;
  --hl-blend: lighten;
  --hl-yellow: #4C4526;  --hl-green: #25432E;  --hl-blue: #203B53;  --hl-orange: #4E3A22;
  --pdf-selection: rgb(230 230 232 / 0.25);
  --search-match: #BABABD;  --search-current: #E6E6E8;
  --bracket: #DCA4C0;
}
.pdf-viewer[data-page-mode="inverted"] {   /* opt-in fallback for scanned PDFs */
  --page-canvas-filter: invert(0.88) hue-rotate(180deg);
  --hl-blend: lighten;
  --hl-yellow: #4C4526;  --hl-green: #25432E;  --hl-blue: #203B53;  --hl-orange: #4E3A22;
  --pdf-selection: rgb(230 230 232 / 0.25);
  --search-match: #BABABD;  --search-current: #E6E6E8;
  --bracket: #DCA4C0;
}
@media (forced-colors: active) {
  .pdf-viewer { --hl-blend: difference; }
  .pdf-highlight { background: none !important; outline: 2px solid Highlight; }
}
```

**Base styles** (`frontend/src/styles/base.css`):

```css
html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
@media (pointer: fine) { html { overscroll-behavior: none; } }
body {
  margin: 0; background: var(--surface); color: var(--ink);
  font: 400 var(--text-14)/1.45 var(--font-ui);
  -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
  font-optical-sizing: auto; font-kerning: normal;
}
::selection { background: var(--selection); color: var(--ink); }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.on-media:focus-visible, .pdf-viewer :focus-visible {
  outline: 2px solid var(--focus-inner); box-shadow: 0 0 0 4px var(--focus-outer);
}
button, [role="button"] { -webkit-tap-highlight-color: transparent; touch-action: manipulation; user-select: none; }
.num, time, [data-num] { font-variant-numeric: tabular-nums; }
h1, h2, h3, .title { text-wrap: balance; }
.prose-done { text-wrap: pretty; }   /* added to an answer only after answer.done */
```

### 2.3 Typography roles

Root is 16px. Line heights are given in px where the role sits on a component grid. Tracking is 0 unless listed.

| Role | Family | Size / line-height | Weight | Tracking | Features and notes |
|---|---|---|---|---|---|
| Chip, bracket numeral, kbd, "Background" label | Sofia | 12 / 16px | 600 (chip numeral), 500 (chip location, kbd), 550 (label) | +0.01em | `tabular-nums`. Smallest text in the app |
| Metadata, agent trail, outline, timestamps, help text | Sofia | 13 / 18px | 450 | +0.005em | `tabular-nums` on any number |
| UI body, menu items, list secondary lines | Sofia | 14 / 20px | 400 | 0 | |
| Labels, buttons, tabs, nav | Sofia | 14 / 20px | 550 | 0 | Sentence case, never uppercase |
| Segmented control labels | Sofia | 13 / 18px | 550 | 0 | Width reserved for the selected weight (§5.9) |
| Reader's question, composer input | Sofia | 15 / 22px | 500 (question), 400 (composer) | 0 | Composer is 16px on `(pointer: coarse)` so iOS doesn't zoom |
| Concept name (profile) | Sofia | 15 / 22px | 550 | 0 | |
| Section heads (Library groups, profile areas, settings sections), answer `h2` | Sofia | 16 / 21px | 600 (650 for answer h2) | 0 | |
| Answer `h3` | Sofia | 15 / 20px | 650 | 0 | Answers use at most two heading levels |
| Page titles ("Your papers", "What you know", "Settings") and the first-run sentence | Sofia | 24 / 29px | 650 | −0.01em | `text-wrap: balance`. Never serif |
| Answer prose | Literata | 17 / 1.55 (S 15.5/1.5, L 19/1.6) | 400 (380 dark) | 0 | `max-width: 32em`; paragraph gap 0.7em; `font-optical-sizing: auto`; `text-wrap: pretty` after done |
| Verbatim paper quote (panel) | Literata | 16 / 24px | 400 roman | 0 | Never italic (italic hurts math); marked by the bracket |
| Profile evidence sentence | Literata | 15 / 1.5 | 400 roman | 0 | `--ink-2`; italic only for the reader's own quoted words |
| Paper title (Library row) | Literata | 19 / 25px | 560 | 0 | 2-line clamp, `text-wrap: balance`, `translate="no"` |
| Paper title (Reader top bar) | Literata | 16 / 20px | 560 | 0 | One line, ellipsis, full title in tooltip |
| Cited sentence inside a passage card | Literata | 15 / 1.5 | 600 | 0 | Rest of the snippet 400 |
| Inline code | mono | 0.875em | 400 | 0 | `--hover` background, `--r-xs`, padding 0.1em 0.3em |
| Code block | mono | 13 / 20px | 400 | 0 | No wrap, `tab-size: 2`, `overflow-x: auto` |
| Math | KaTeX | 1.18em in Literata, 1.13em in Sofia | — | — | Display math scrolls horizontally |

**Where Literata is allowed:** answer prose, verbatim quotes, paper titles, profile evidence sentences, and the live preview sentence in Settings. Nowhere else: no headings, buttons, labels, empty states, page titles or toasts. Literata italic appears only for `<em>` inside answers and for the reader's own words quoted back in the profile.

**Features:** use `font-variant-numeric`, never `font-feature-settings`, because a child's `font-feature-settings` replaces the parent's list. Sofia Sans has `tnum`, `pnum`, `frac`, `calt` and `liga`. Literata has `tnum`, `pnum` and `frac`. Use `font-variant-numeric: tabular-nums` on page numbers, counts, timings, percentages and chips. Prose keeps proportional figures.

**Measure:** answer prose has `max-width: 32em`, which measures about 67 characters of Literata at 17px. In a 400px panel (368px of text) a line holds about 45 characters, which suits a side column. Never use `ch` for measure: `1ch` is the width of "0" (0.58em in both faces), while the average character is 0.48em (Literata) and 0.43em (Sofia).

### 2.4 Spacing

Scale: **4 · 8 · 12 · 16 · 24 · 32 · 48 · 64**. Every margin, padding and gap is on the scale: no `6px`, `13px`, `20px` or `[27px]`. Space inside a group is always smaller than space between groups.

The only exceptions, each listed here: the 2px inset of the segmented track; 1px optical nudges on icons; hit-area pseudo-element offsets; `em`-based padding inside prose (inline code); and bracket drawing geometry on the page (§5.8). Component **heights** are a separate fixed list: 20 (chip), 28, 32, 36, 40, 44, 48 (Reader top bar), 52 (panel header), 56 (app header).

| Context | Inside | Between |
|---|---|---|
| Assistant turn | question → trail 8; trail → callout or answer 16; callout → answer 16; answer → actions 12 | turns 32 |
| Library row | title → meta 4; meta → strip line 8 | rows 8 (no rules) |
| Profile | name → evidence 4 | rows 4; areas 48 |
| Settings | label → control 8; controls 16 | sections 48 |
| Panel padding | 16 horizontal, 24 top, 16 bottom above composer | |
| Page | page → page 16; page ↔ lane 0 (lane is part of the page box) | |

Units: `rem` for font sizes and media queries; `px` for borders, radii, shadows and horizontal padding; `em` for vertical rhythm inside prose.

### 2.5 Radii

`--r-page` 2 · `--r-xs` 4 · `--r-sm` 8 · `--r-md` 12. Nested elements compute their radius from the parent: `border-radius: calc(var(--r-md) - var(--pad))`.

| Element | Radius |
|---|---|
| PDF page | 2 |
| Citation chip, `kbd`, inline code, highlight band corners, search outline | 4 |
| Buttons, inputs, list rows (hover background), tooltips, back pill, segmented track | 8 |
| Segmented thumb | `calc(8px - 2px)` = 6 |
| Composer (padding 4) | 12; its Ask/Stop button and quote chip `calc(12px - 4px)` = 8 |
| Popovers, menus, hover cards, toasts, dialogs, selection toolbar (padding 4) | 12; items inside `calc(12px - 4px)` = 8 |
| Sheets (tablet panel, phone bottom sheet) | 12 on the exposed corners only |

No pills: nothing has `border-radius: 999px` except the 8px swatch circles in the selection toolbar and the 8px dots in a learning path.

### 2.6 Colour budget: where each colour may appear

| Token | Allowed uses (exhaustive) |
|---|---|
| `--pencil` / `--bracket` | Page brackets and their numerals; exact-span underline (transient); in-panel bracket beside a verbatim quote; chip numerals; the active-chip inset ring; the gap callout's insert caret ‸; open rings for not-yet concepts in a learning path; pinned-page ticks in the Library page strip; the pin glyph on pinned brackets |
| `--ink` fill | Primary button, light-theme tooltip background, selected radio and checkbox, switch track (on). Library strip "read" ticks and known-concept dots use `--ink-2` |
| `--danger` | Phosphor `WarningCircle` icon on: lost work, ingestion failed, upload rejected. Text of the destructive menu item "Remove from your papers" and the "Reset" button in its dialog |
| `--warn` | Phosphor `Warning` icon on: scanned PDF with no text layer, withdrawn arXiv paper, missing API key banner |
| `--success` | Phosphor `Check` icon on the finished ingestion step "Ready", and the "Back online" line |
| Highlighter yellow, green, blue, orange | The reader's own highlights on the page and the swatches that create them. Pink is withheld because it is too close to mulberry |

There are no level or depth colours. Depth is shown by the control's thumb and label weight only.

### 2.7 Contrast (WCAG 2.x ratio · APCA Lc), all measured on composited colours

Every text and background pair in the product is listed. Body text targets ≥ 7:1, secondary text ≥ 4.5:1, and non-text UI ≥ 3:1. Numerals at 12px are body text and must reach ≥ 4.5:1.

**Light theme**

| Foreground | Background | Ratio | Lc | Use |
|---|---|---|---|---|
| `--ink` #222123 | `--surface` #F7F6F9 | 14.89 | 98 | All primary text |
| `--ink` | `--raised` #FEFEFF | 15.91 | 102 | Composer, popovers |
| `--ink` | `--canvas` #E6E6E8 | 12.86 | 88 | Text on the PDF desk (back pill label sits on raised; listed for safety) |
| `--ink` | `--hover` on surface #ECEBEE | 13.50 | — | Hovered rows |
| `--ink` | `--selected` on surface #E8E7EA | 13.02 | — | Selected rows, current outline item |
| `--ink` | `--pressed` on surface #E4E3E6 | 12.54 | — | Pressed |
| `--ink` | `--selection` #D6D5D7 | 10.96 | — | Selected UI text |
| `--ink-2` #5B5A5D | `--surface` | 6.36 | 79 | Metadata, trail, outline |
| `--ink-2` | `--raised` | 6.80 | 83 | Placeholders, kbd |
| `--ink-2` | `--canvas` | 5.50 | 69 | (not used for text today; floor if it ever is) |
| `--ink-2` | `--hover` / `--selected` / `--pressed` | 5.77 / 5.56 / 5.36 | — | Secondary text in rows |
| `--pencil` #883564 | `--surface` | 7.12 | 81 | Chip numerals (12px/600) |
| `--pencil` | `--raised` | 7.60 | 85 | Numerals in hover cards |
| `--pencil` | `--hover` / `--selected` | 6.45 / 6.22 | — | Numerals in hovered or active chips |
| `--pencil-on-page` | page #FFFFFF | 7.66 | — | Brackets and numerals on the page |
| `--on-ink` #F7F6F9 | `--ink` | 14.89 | — | Primary button label, tooltip text |
| `--on-ink` | `--ink-hover` #3B3A3C | 10.51 | — | Primary button hover |
| `--danger` #9A4312 | `--surface` / `--raised` | 6.12 / 6.54 | 77 | Error icon, destructive item |
| `--warn` #81520A | `--surface` | 6.20 | 78 | Warning icon |
| `--success` #286648 | `--surface` | 6.32 | 78 | Ready icon |
| `--line` #868588 | `--surface` / `--raised` | 3.41 / 3.64 | — | Control borders (non-text ≥ 3:1). Never on `--canvas` (2.94) |
| `--focus` ring | `--surface` / `--raised` / `--canvas` | 14.89 / 15.91 / 12.86 | — | 2px ring, 2px offset (2.4.13 ≥ 3:1 change) |
| Two-tone ring | white inner vs ink outer | 16.03 | — | Over the PDF page |
| Black page text | yellow / green / blue / orange band | 17.54 / 15.71 / 15.41 / 15.62 | — | Highlights (multiply) |
| Black page text | text-layer selection #CECECF | 13.35 | — | Selecting on the page |
| `--search-match` #5B5A5D | page #FFFFFF | 6.85 | — | Search outline (non-text) |

**Dark theme**

| Foreground | Background | Ratio | Lc | Use |
|---|---|---|---|---|
| `--ink` #E6E6E8 | `--surface` #1F1F21 | 13.20 | 90 | Primary text |
| `--ink` | `--raised` #28282A | 11.81 | 88 | Composer, popovers |
| `--ink` | `--canvas` #171719 | 14.36 | 91 | |
| `--ink` | hover / selected / pressed | 11.65 / 11.03 / 10.41 | — | Rows |
| `--ink` | `--selection` #414143 | 8.17 | — | Selected UI text |
| `--ink-2` #BABABD | `--surface` / `--raised` / `--canvas` | 8.50 / 7.60 / 9.25 | 63 / 62 / 64 | Metadata. Lc 63, so use weight ≥ 450 and never for paragraphs |
| `--ink-2` | hover / selected / pressed | 7.50 / 7.10 / 6.70 | — | |
| `--pencil` #DCA4C0 | `--surface` / `--raised` / `--canvas` | 7.93 / 7.09 / 8.62 | 60 | Chip numerals in the panel |
| `--pencil` | hover / selected | 6.99 / 6.62 | — | |
| `--on-ink` #1F1F21 | `--ink` / `--ink-hover` #C7C7C9 | 13.20 / 9.75 | — | Primary button |
| `--tooltip-ink` | `--tooltip-bg` #3D3D3F | 8.70 | — | Tooltips |
| `--danger` #E79D7B | `--surface` / `--raised` | 7.45 / 6.66 | 57 | |
| `--warn` #E4B572 · `--success` #88C99E | `--surface` | 8.74 · 8.54 | 65 · 64 | |
| `--line` #79787B | `--surface` / `--raised` / `--canvas` | 3.75 / 3.35 / 4.08 | — | Control borders |
| Two-tone ring | inner #1F1F21 vs outer #E6E6E8 | 13.20 | — | Over the page |
| Black page text | dimmed page #D9D9D9 | 14.88 | — | Dimmed paper (`brightness(.85)` on the canvas) |
| `--pencil-on-page` #883564 | dimmed page #D9D9D9 | 5.43 | — | Brackets and 12px numerals. The overlay is never dimmed |
| Black page text | dimmed yellow / green / blue / orange band | 13.02 / 12.18 / 12.03 / 12.05 | — | Dimmed-mode highlight fills (all ≥ 12:1) |
| Black page text | text-layer selection on dimmed page #B1B1B1 | 9.79 | — | |
| `--search-match` #5B5A5D | dimmed page | 4.85 | — | Search outline |
| Night page text #D9D9DB | night page #1F1F21 | 11.67 | 83 | pdf.js `pageColors` |
| Night page text | night yellow / green / blue / orange band | 6.81 / 7.76 / 8.22 / 7.63 | — | Highlights (lighten) |
| Night page text | night selection #515153 | 5.62 | — | |
| `--bracket` #DCA4C0 | night page #1F1F21 / inverted page #1F1F1F | 7.93 / 7.94 | — | Brackets on dark pages |

Hue separation: pencil is OKLCH h 348 (HSL 326°) and danger is OKLCH h 45 (HSL 22°), 57° apart. Both also differ in form: errors always carry an icon and a sentence and never sit where a bracket could be.

Colour-vision check: the reader's highlights are fills and the assistant's marks are strokes with numerals, so the two never depend on hue. Search is an outline and selection is a neutral tint. Every form differs, which keeps forced-colors mode and every CVD simulation unambiguous.

---

## 3. Icons

**Set:** Phosphor, `@phosphor-icons/react@2.1.10`. Use the `*Icon` named exports, e.g. `import { MagnifyingGlassIcon } from '@phosphor-icons/react'`. No second library, no inline SVGs from elsewhere, no emoji.

```tsx
<IconContext.Provider value={{ size: 16, weight: 'regular', mirrored: false }}>
```

**Rules**
1. **Fewer icons than feel natural.** Answer actions, gap-callout actions, suggestion rows, nav links and menu items are text, not icons. The full list below is everything the app may use; adding one is a design change.
2. **Size by text:** 16px beside 13–14px text; 20px only inside 44px touch targets; never 24px+ in chrome.
3. **Weight:** `regular` at rest. `fill` means on: toggle buttons with `aria-pressed="true"` (Contents, Assistant). State is never shown by colour alone.
4. **Colour:** `currentColor`. Icons sit in `--ink-2` at rest and `--ink` on hover, focus or selected. Status icons take `--danger`, `--warn` or `--success` per §2.6.
5. **Alignment:** inline icons get `vertical-align: -0.125em`. Nudge `CaretDown` and `CaretRight` 1px down so they sit optically centred on 14px text.
6. **Icon-only buttons** have an `aria-label` and a tooltip (label plus shortcut). The icon gets `aria-hidden="true"`.

**The complete icon inventory**

| Icon | Where |
|---|---|
| `ArrowLeftIcon` | Reader top bar "Your papers" (icon only below 1280px) |
| `SidebarSimpleIcon` | Toggle contents (outline) |
| `SidebarSimpleIcon mirrored` | Toggle assistant panel; rail "Show assistant" |
| `MagnifyingGlassIcon` | Find in paper; Library and profile filter fields (leading, 16px, `--ink-2`) |
| `MinusIcon` / `PlusIcon` | Zoom out / in |
| `CaretDownIcon` | Zoom menu, select-style menus, trail expand (rotates −90° when closed; no animation) |
| `TextAaIcon` | Appearance menu |
| `DotsThreeIcon` | "More" menus (top bar, row menus, answer overflow) |
| `GearSixIcon` | Settings in the app header |
| `XIcon` | Close find, remove quote, dismiss suggestions, close dialog |
| `CaretUpIcon` / `CaretDownIcon` | Previous and next find match |
| `ArrowUUpLeftIcon` | "Back to p. 11" pill |
| `ArrowDownIcon` | "Jump to latest" pill |
| `ArrowUpRightIcon` | External links only (Semantic Scholar, arXiv) |
| `FilePdfIcon` | Drag-over overlay (20px) and upload rows without a title yet |
| `CheckIcon` | Finished ingestion steps (`--ink-2`), "Ready" (`--success`), checked menu items |
| `WarningCircleIcon` | Lost work, failed ingestion, rejected upload (`--danger`) |
| `WarningIcon` | Scanned PDF, withdrawn paper, missing API key (`--warn`); failed steps in the expanded trail (`--ink-2`, 12px) |
| `CaretLeftIcon` / `CaretRightIcon` | Previous / next answer version (§5.19) |
| `ClockIcon` | Rate limited, queued question (`--ink-2`) |
| `WifiSlashIcon` | Offline bar (`--ink-2`) |
| `StopIcon weight="fill"` | Inside the Stop button, 12px, left of the label |

**Custom glyphs** (four), drawn on Phosphor's 256 grid with stroke width 16 units, round caps and joins, exported as React components alongside the Phosphor set:

| Glyph | Drawing | Where |
|---|---|---|
| `BracketGlyph` | Vertical stroke at x=176 from y=40 to y=216; ticks at both ends from x=176 to x=112 | The one-time legend line only (in `--pencil`), where it depicts the mark it explains. Never decorative, never in the logo |
| `CaretInsertGlyph` (‸) | Polyline (72,184) → (128,72) → (184,184) | Gap callout (in `--pencil`), learning-path destination |
| `PinToPageGlyph` | A bracket (as above, scaled 70%) with a 40-unit filled circle at its top tick | "Pin to page" menu item; pinned brackets on the page, replacing the numeral |
| `HighlighterGlyph` | Chisel-tip marker: parallelogram (64,184)(152,96)(192,136)(104,224) plus tip (40,216)(64,184)(104,224) | "Your marks" heading in the outline |

---

## 4. Layouts and wireframes

### 4.1 Breakpoints and URL state

| Name | Width | Reader behaviour |
|---|---|---|
| Wide | ≥ 1920 | As Desktop, and the page hugs the panel (§4.4) |
| Desktop | 1440–1919 | Outline open by default (232), PDF, panel docked (400) |
| Laptop | 1280–1439 | Outline is a drawer (closed by default), panel docked (400) |
| Compact | 1000–1279 | Outline drawer; panel docked at `min(user width, viewport − 620)`, floored at 340 |
| Tablet | 768–999 | Panel is a 420px overlay sheet that tucks into a 48px rail; outline drawer |
| Phone | < 768 | Panel is a bottom sheet (peek / 50% / 92%); outline is a sheet |

Media queries use `rem`: 48rem (768), 62.5rem (1000), 80rem (1280), 90rem (1440), 120rem (1920).

Routes (react-router, real `<Link>`s):
- `/`: Library
- `/paper/:paperId?p=4&depth=standard&panel=open|closed&outline=open`: Reader
- `/know`: What you know
- `/settings`: Settings

Page, depth and panel state live in the URL. Zoom and the panel width are remembered per device in `localStorage`. Scroll position (page and offset) is saved to the session (`PATCH /sessions/{id}`) every 2 s while reading and restored on open. The composer draft is saved per paper in `localStorage`.

### 4.2 App header (Library, What you know, Settings)

56px tall, `--surface`, with a 1px `--line-subtle` rule at the bottom. Left padding is `--inset`, so the wordmark and the page column share one left edge.

```
|<-- --inset -->PaperMind     Your papers   What you know                            [⚙]  |
               ^ wordmark: text only, Sofia 16/650 --ink    Sofia 14/550 --ink-2      28px icon button
                                  current item: --ink, 2px --ink underline 6px below baseline
```

The Reader replaces this header with its own 48px top bar (§5.6).

### 4.3 Library

Single left-aligned column, max `--col-max` (736px), starting at `--inset`. The ground is `--surface`. No grid, no thumbnails.

**Desktop ≥ 1280** (column 736px; extra space stays to the right, deliberately)

```
+------------------------------------------------------------------------------------------------+
| PaperMind    Your papers   What you know                                                [⚙]   |  56
+------------------------------------------------------------------------------------------------+
|                                                                                                |
|  Your papers                                                                     Sofia 24/650  |
|                                                                                           ↕ 24 |
|  +------------------------------------------------------------------+ +-------------+          |
|  | Paste an arXiv link or ID, or drop a PDF on this page…           | |  Add paper  |  40 tall |
|  +------------------------------------------------------------------+ +-------------+          |
|  PDF up to 50 MB. arXiv IDs look like 1706.03762.                         Sofia 13 --ink-2     |
|                                                                                           ↕ 32 |
|  Attention Is All You Need                                     You stopped at §3.2             |
|  Vaswani et al., 2017 · 15 pages                                                               |
|  ▮▮▮▮···········  p. 4 of 15   (page strip, §5.3: 4 pages read, 11 not yet)                   |
|                                                                                           ↕ 8  |
|  BERT: Pre-training of Deep Bidirectional Transformers for              Not opened             |
|  Language Understanding                                                                        |
|  Devlin et al., 2018 · 16 pages                                                                |
|                                                                                           ↕ 8  |
|  Denoising Diffusion Probabilistic Models                     Getting it ready                 |
|  Ho et al., 2020                                                                               |
|  ✓ Downloaded  ✓ Read 25 pages  ✓ Found 9 sections  Indexing 140 of 212 passages…   Cancel    |
|                                                                                                |
+------------------------------------------------------------------------------------------------+
  |<-------------------------------------- 736 max ----------------------------------->|
  right column (status) is a 200px right-aligned column inside the row; title wraps around it
```

**First run** (no papers). The column holds only these three lines, at the same left edge. Nothing else is on screen except the header.

```
|  Start with a paper you’ve been meaning to read.                              Sofia 24/650   |
|                                                                                       ↕ 24   |
|  +------------------------------------------------------------------+ +-------------+        |
|  | Paste an arXiv link or ID, or drop a PDF on this page…           | |  Add paper  |        |
|  +------------------------------------------------------------------+ +-------------+        |
|  PDF up to 50 MB. arXiv IDs look like 1706.03762.                                            |
|                                                                                       ↕ 24   |
|  Or open Attention Is All You Need to see how it works.     (the title is a link, --ink)     |
```

**Tablet 768–999:** same column, `--inset` 38–50px (5vw). Status moves under the meta line (the right column collapses below 900px).

**Phone < 768:**

```
+------------------------------+
| PaperMind              [⚙]   |  56   nav moves to a 2-item row under the header:
| Your papers · What you know  |  40   Sofia 14/550, current item --ink with underline
+------------------------------+
| Your papers                  |  24/650
| +--------------------------+ |
| | Paste an arXiv link or   | |  input 44 tall, 16px text, full width
| | ID, or drop a PDF…       | |
| +--------------------------+ |
| [        Add paper        ]  |  44 tall, full width
| PDF up to 50 MB.             |
|                              |
| Attention Is All You Need    |  Literata 19/560
| Vaswani et al., 2017         |
| You stopped at §3.2          |  Sofia 13 --ink-2
| ▮▮▮▮···········  p. 4 of 15  |
+------------------------------+
```
On touch, a dropped PDF becomes a file picker: the input's trailing text button "Choose PDF" opens `<input type="file" accept="application/pdf">`.

**Density:** 0, 1 and 300 papers are all valid. From 12 papers up, a filter field appears above the list (240px, `MagnifyingGlassIcon` leading, placeholder "Filter by title or author…"). The list doesn't virtualize; rows use `content-visibility: auto; contain-intrinsic-size: auto 96px`. Sort order is last opened, then date added.

### 4.4 Reader and assistant panel

Three columns, no boxes, separated by tone steps:
- Outline (`--surface`)
- PDF column (`--canvas`)
- Panel (`--surface`)

The top bar is `--surface` with a 1px `--line-subtle` bottom rule. The panel's left edge is a 1px resize separator, invisible until hovered.

**Desktop 1440–1919** (e.g. 1440: outline 232 + PDF 808 + panel 400)

```
+------------------------------------------------------------------------------------------------------------------------+
| [←] Your papers  [▤]  Attention Is All You Need          p. 4 of 15   [−] 110% ▾ [+]   [⌕] [Aa] [▤] [⋯]                | 48
+-------------+----------------------------------------------------------+-----------------------------------------------+
| Contents    |                                                          | Depth  [ From basics | Standard | Technical ] |
|             |  +--32--+------------ page 696 -----------+--32--+       |                                               |
|    Abstract |  |      | 3.2.1 Scaled Dot-Product Attent… |  ,1  |      | ,  “We suspect that for large values of       |
|   1 Intro…  |  |      | We call our particular attention |  |   |      | |  dₖ, the dot products grow large in         |
|   2 Backgr… |  |      | “Scaled Dot-Product Attention”…  |  `   |      | `  magnitude…”                                |
|   3 Model…  |  |      | ████ your highlight (fill) ████  |      |      |    §3.2.1, p. 4                               |
| 3.1 Encoder…|  |      | …pushing the softmax function    |  ,2  |      | Why divide by √dₖ?                            |
|▌3.2 Attenti…|  |      | into regions where it has        |  |   |      | Read 4 passages and checked 2 ideas,          |
| 3.3 Positio…|  |      | extremely small gradients…       |  `   |      | 3.1 s                                  ▸      |
|   4 Why sel…|  +------+----------------------------------+------+      |                                               |
|   5 Training|                   ↕ 16 page gap                          | ‸ This builds on softmax, which hasn’t        |
|             |  +------+----------------------------------+------+      |   come up in your reading yet.                |
| Your marks  |  |      |                                  |      |      |   ● dot product ─ ○ softmax ─ ‸ this          |
| 3 highlights|                                                          | [Explain softmax first]  I know softmax       |
| 1 pinned    |                                                          |                                               |
|             |                                                          | When dₖ is large, the dot products grow,      |
|             |                                                          | and softmax goes flat [1 §3.2.1].             |
|             |                                                          | Dividing by √dₖ keeps them in range           |
|             |                                                          | [2 §3.2.1].                                   |
|             |                                                          | Copy  Simpler  More technical  Pin to page ⋯  |
|             |                [↩ Back to p. 11]  bottom-centre, 10 s    |-----------------------------------------------|
|             |                                                          | +-------------------------------------------+ |
|             |                                                          | | Ask about this paper…               [Ask] | |
|             |                                                          | +-------------------------------------------+ |
+-------------+----------------------------------------------------------+-----------------------------------------------+
    232        PDF column = viewport − 232 − 400; 24px side padding;      400 (340–560, resizable)
               page = column − 48 − 2×32 lanes; ≤ 920 at Fit width
```

The panel header fits without truncation at every docked width. The "Depth" label is 34.5px, then an 8px gap, then three equal 84px segments plus 4px of track padding: 298.5px in total, measured with Sofia Sans 13/550. That fits the 368px content width at the default 400px panel and the 308px content width at the 340px minimum. Below 360px of panel width (phones only) the control becomes a native select (§5.9).

**Wide ≥ 1920:** the PDF column has spare room. The page group (page plus its two lanes) is **right-aligned** so its right lane ends 16px from the panel, which puts the page edge 48px from the panel. The spare canvas goes to the left, between outline and page. This keeps the bracket and its answer one short glance apart at 2560px. At user zoom above Fit width the page grows beyond 920px and the group centres again once it overflows.

**Laptop 1280–1439** (e.g. 1280: PDF 880 + panel 400; outline is a drawer)

```
+----------------------------------------------------------------------------------------------------------------------+
| [←] Your papers [▤]  Attention Is All You Need   p. 4 of 15  [−] 110% ▾ [+]  [⌕] [Aa] [▤] [⋯]                        |
+----------------------------------------------------------------------+-----------------------------------------------+
|  +--32--+------------- page 768 -------------+--32--+                | Depth  [ From basics | Standard | Technical ] |
|  |  1,  | left column    | right column         |      |             | …answer…                                      |
|  |   |  | cited lines in | cited lines in the   |      |             |                                               |
|  |   `  | left column →  | right column → right |  ,2  |             |                                               |
|  |      | left lane      | lane                 |  `   |             |                                               |
+----------------------------------------------------------------------+-----------------------------------------------+
  Outline drawer: 280px wide (232 content + 24 padding each side), slides over the PDF from the left
  edge under the top bar, --surface, --shadow-lg, no scrim, closes on Esc / [ / outside click.
```

**Compact 1000–1279** (e.g. 1024: PDF 624 + panel 400). The panel width is `min(saved width, viewport − 620)`, floored at 340: 1000 → 380, 1024 → 400. PDF column padding drops to 16, so a 1024 viewport gives a 528px page. The back link is icon-only (as everywhere below 1280px) and the zoom value is hidden below 1100.

**Tablet 768–999** (e.g. 834):

```
+----------------------------------------------------------------+
| [←] [▤] Attention Is All You…   p. 4 of 15  [−][+] [⌕][Aa][▤][⋯]|
+------------------------------------------------------+---------+
|  +--32--+----------- page 690 ----------+--32--+  ,1 | 420     |
|  |      |                                |      |    | overlay |
|  |      |                                |      |    | sheet   |
|  +------+--------------------------------+------+    | over the|
|                                                      | PDF, no |
|                                                      | dimming |
+------------------------------------------------------+---------+
 tucked state, after a chip click or [ ] ]:
+-----------------------------------------------------------+----+
|  page uses viewport − 48                                   | ▤  |  ← 48px rail: Show assistant
|                                                            | 1  |  ← active answer's numerals,
|                                                            | 2  |    28px buttons in --pencil;
|                                                            |    |    click = jump to that passage
|                                                            | /  |  ← Ask (focuses composer, opens sheet)
+-----------------------------------------------------------+----+
```
The overlay sheet is `min(420px, viewport − 48px)`, `--surface`, `--shadow-lg`, top-left and bottom-left corners `--r-md`. It slides with `transform` (§6). Clicking a chip tucks it to the rail so the passage and its bracket are visible, and the rail keeps the numerals so the link between bracket and answer survives.

**Phone < 768** (e.g. 390):

```
+------------------------------+
| [←] Attention Is All…  [Aa][⋯]|  44, hides on scroll down, shows on scroll up
+------------------------------+
|20+------ page 334 -------+20 |  lanes 20px; page = 390 − 16 − 40
|  |                       | ,1|
|  |                       | ` |
|  +-----------------------+   |
|        [↩ Back to p. 11]     |
|==============================|  bottom sheet, --surface, --shadow-lg, top corners 12
|            ───               |  grabber 32×4, --line-subtle, 8 from top
| When dₖ is large, the dot…   |  latest answer, first line (peek)
| +--------------------------+ |
| | Ask about this paper… Ask| |  composer 44 tall
| +--------------------------+ |
+------------------------------+
  detents: peek 120px (grabber, the latest answer's first line, composer) · 50% · 92% of the visual viewport.
  At 50% and 92% the sheet shows the panel header with the Depth control: segmented when the sheet
  is ≥ 360px wide (it needs 298.5px + 32px padding), a native <select> on narrower phones.
  Contents opens from [⋯] → "Contents" as a 92% sheet.
```
- **Selecting text** on a phone replaces the system callout with a 44px action bar above the sheet: Explain · Ask about this · Highlight.
- **Tapping a chip** drops the sheet to peek, scrolls the PDF and draws the bracket.

**Panel resize (docked widths):**
- The separator is the panel's left edge: a 9px hit area (4px over the PDF, 4px over the panel, 1px line).
- At rest: invisible. On hover (after 150ms): a 1px `--line` line. While dragging: 2px `--ink-2`.
- `role="separator"`, `aria-orientation="vertical"`, `aria-valuemin=340`, `aria-valuemax=560`, `aria-valuenow`, `aria-label="Resize assistant"`.
- Keys: arrows ±16px, Shift+arrows ±64, Home/End to min/max. Double-click resets to 400.
- While dragging, set `user-select: none` and `inert` on both panes.
- The PDF re-lays out on pointer-up, not per frame. During the drag only the separator line moves.

**Collapsed (`]`) at ≥ 1000:** the panel collapses to the 48px rail described for tablet, and the PDF re-lays out once.

### 4.5 What you know (knowledge profile)

Same column as the Library (736px at `--inset`). Grouped by the 12 areas from `data/concepts/areas.json`, in that file's order.

```
+------------------------------------------------------------------------------------------------+
| PaperMind    Your papers   What you know                                                [⚙]   |
+------------------------------------------------------------------------------------------------+
|  What you know                                                                   Sofia 24/650  |
|  PaperMind’s best guess from your questions. Change anything that’s wrong.                     |
|  Only you can see this.                                                   Sofia 14 --ink-2     |
|                                                                                          ↕ 24  |
|  Ready for next: layer normalization, multi-head attention, positional encoding.              |
|  These build on what you already know.                 (concept names are links → row detail)  |
|                                                                                          ↕ 24  |
|  [⌕ Find a concept…            ]      [Add something you know]          42 concepts            |
|                                                                                          ↕ 48  |
|  Linear algebra                                                              6                 |
|  Dot product                                                         Comfortable   [⋯]        |
|  You asked why it measures similarity, in Attention Is All You Need, §3.2 · Oct 3              |
|                                                                                          ↕ 4   |
|  Matrix multiplication                                               Comfortable              |
|  You said you’re comfortable with Linear algebra · Oct 2                                       |
|                                                                                          ↕ 48  |
|  Attention & transformers                                                    4                 |
|  Softmax                                                             Seen it                   |
|  You asked about it in Attention Is All You Need, §3.2.1 · Oct 3                               |
|  Show 1 more                                                                                   |
|  Scaled dot-product attention                                        Not yet                   |
|  Came up while reading Attention Is All You Need, §3.2.1 · Oct 3                               |
+------------------------------------------------------------------------------------------------+
```

- **Rows** are not boxed. Hover shows the `--hover` background with radius 8, extending 12px beyond the text on each side. The row menu `[⋯]` is visible on hover, `:focus-within` and touch.
- **Phone:** the state word moves under the concept name, and evidence wraps. The filter and Add button stack full width (44 tall).
- **A11y:** the list is always a real `<ul>` per area with an `<h2>` area heading. The state is in text. No graph view in v1.

### 4.6 First-run onboarding

The Library first run stays as simple as §4.3. Calibration happens **in context, inside the panel, the first time the reader opens any paper** (`user.onboarded === false`). It is inline and skippable, never a modal or carousel. Gap callouts stay hidden until this is done or skipped. If skipped, depth is Standard.

```
+ panel (400) -------------------------------------+
|                                                  |
|  Before your first question                      |  Sofia 16/600
|  Where should answers start?                     |  Sofia 15/500
|                                                  |
|  ( ) From basics                                 |  radio 16px; label Sofia 14/550
|      Define terms as they come up and lean on    |  description Sofia 13 --ink-2
|      intuition. Math only when it helps.         |
|  (•) Standard                                    |  preselected
|      Assume neural nets and backprop. Explain    |
|      this paper’s new ideas and terms.           |
|  ( ) Technical                                   |
|      Skip the background. Be precise, show the   |
|      math, compare with related work.            |
|                                                  |
|  This only sets where answers start. You can     |  Sofia 13 --ink-2
|  change it for any question.                     |
|                                                  |
|  [Continue]   Skip for now                       |  primary 32 + text button
+--------------------------------------------------+

 step 2 (same place, replaces step 1, no animation):
+--------------------------------------------------+
|  Which of these are you comfortable with?        |  Sofia 15/500
|  Answers won’t stop to explain these unless you  |  Sofia 13 --ink-2
|  ask. Optional.                                  |
|                                                  |
|  [ ] Linear algebra                              |  12 checkbox rows, 32 tall each
|  [ ] Calculus & optimization basics              |  (44 on touch); label Sofia 14
|  [ ] Probability & statistics                    |  description in tooltip/hover:
|  [ ] Machine learning basics                     |  area.description from areas.json
|  [ ] Neural networks & training                  |
|  [ ] Convolutional & vision models               |
|  [ ] Sequence models & NLP                       |
|  [ ] Attention & transformers                    |
|  [ ] Large language models & alignment           |
|  [ ] Generative models                           |
|  [ ] Reinforcement learning                      |
|  [ ] Evaluation, datasets & metrics              |
|                                                  |
|  [Done]   Skip                                   |
+--------------------------------------------------+
```
- **Writes:** Continue sends `PATCH /users/{id} {level}`. Done sends `POST /users/{id}/knowledge/areas {areas}` then `PATCH {onboarded: true}`. Skip sends `PATCH {onboarded: true}` only.
- **After either step:** the panel shows its normal empty state (§5.18). Focus moves to the composer.
- **Radios** are a native `<fieldset>` with `<legend>` "Where should answers start?". The rows are plain, not cards, with radio dots 16px in `--ink`.

### 4.7 Settings

Single column, max `--settings-max` (640px) at `--inset`. Settings apply instantly; there is no Save button. Each change is announced politely ("Theme set to Dark").

```
+------------------------------------------------------------------------------------------------+
| PaperMind    Your papers   What you know                                                [⚙]   |
+------------------------------------------------------------------------------------------------+
|  Settings                                                                                      |
|                                                                                          ↕ 48  |
|  Appearance                                                                  Sofia 16/600      |
|  Theme                     [ System | Light | Dark ]                                           |
|  Page in dark mode         [ Dimmed paper | Night page | Inverted ]                            |
|                            Night page recolours the page. Figures turn two-tone.  (13 --ink-2) |
|  Answer text size          [ Small | Medium | Large ]                                          |
|                            For large key sizes the dot products grow… (live preview, Literata)|
|                                                                                          ↕ 48  |
|  Answers                                                                                       |
|  Default depth             [ From basics | Standard | Technical ]                              |
|                            Assume neural nets and backprop. Explain this paper’s new ideas…    |
|  Label background paragraphs      [on]   Marks uncited paragraphs “Background”.                |
|                                                                                          ↕ 48  |
|  Keyboard                                                                                      |
|  Single-key shortcuts      [on]   E, A, H, 1–4, [, ], / and ? work outside text fields.        |
|  See all shortcuts                                                       (text button, opens ?)|
|                                                                                          ↕ 48  |
|  What you know                                                                                 |
|  Open What you know                                                                            |
|  Reset what you know…                                         (--danger text, opens dialog)    |
|                                                                                          ↕ 48  |
|  Connection                                                                                    |
|  Server        Connected · version 0.4.0                                                       |
|  API key       Set                       (or ⚠ Missing: “Set ANTHROPIC_API_KEY on the server   |
|                                           and restart it.” with [Copy command])                |
+------------------------------------------------------------------------------------------------+
   label column 200px (Sofia 14/550), control column flexible; below 640px labels stack above
```

---

## 5. Component specs

Conventions for this section:
- Sizes are CSS px. "Sofia 14/550" means Sofia Sans, 14px, weight 550.
- All hover styles sit inside `@media (hover: hover) and (pointer: fine)`. Hover-in is instant; hover-out fades `background-color` over `--dur-fast` (`.x { transition: background-color 150ms ease } .x:hover { transition-duration: 0ms }`).
- Every control has a `:focus-visible` ring (§2.2).
- Hit targets are ≥ 24×24 on fine pointers and ≥ 44×44 on `(pointer: coarse)`. Small visuals grow their hit area with `::before { inset: … }`, never by growing the visual.
- Never pre-disable a submit button. Never put a tooltip on a disabled control.

### 5.1 Buttons, inputs, kbd

| Variant | Size | Visual | States |
|---|---|---|---|
| **Primary** | 32 tall (40 for "Add paper"; 44 on coarse), padding 0 12, radius 8, Sofia 14/550 | `--ink` fill, `--on-ink` text | hover `--ink-hover`; active `transform: scale(.97)` over `--dur-press` `--ease-out`; busy keeps its label and adds "…" ("Adding…"), with a 12px spinner left of the label after 200ms |
| **Secondary** | 32 tall, padding 0 12, radius 8, Sofia 14/550 | transparent, `box-shadow: inset 0 0 0 1px var(--line)`, `--ink` text | hover `--hover` bg; active `--pressed` + scale .97 |
| **Text** | 28 tall, padding 0 8, radius 8, Sofia 13/500 or 14/550 | `--ink-2` text, no border | hover `--ink` text + `--hover` bg; active `--pressed` |
| **Icon** | 28×28 (32×32 in the Reader top bar; 44 on coarse), radius 8, 16px icon | `--ink-2` icon | hover `--hover` + `--ink`; `aria-pressed="true"` → `--selected` bg, `fill` weight, `--ink` |
| **Destructive (menu item / dialog)** | as its container | `--danger` text; dialog button is Secondary with `--danger` text and an inset `--danger` ring | |

- Buttons never change font weight on hover. The one weight change on selection is the segmented control's selected label, which reserves its bold width so nothing moves (§5.9). Button text is `user-select: none`. A button's width doesn't change when its label toggles (Ask ↔ Stop): it reserves the wider label with `min-width`.
- **Text input:** 36 tall (Library add field 40; 44 and 16px text on coarse), `--raised` bg, `box-shadow: inset 0 0 0 1px var(--line)`, radius 8, padding 0 12, Sofia 15/400 `--ink`, placeholder `--ink-2`. Focus: the 2px `--focus` outline with 2px offset (never just a border colour change). Error: inset ring `--danger` 1px, plus a message below (§5.25).
- **Checkbox and radio:** 16px boxes. Checkbox radius 4 with an inset `--line` ring, checked = `--ink` fill with a 12px `CheckIcon` in `--on-ink`. Radio is a 16px circle with an inset ring, selected = 6px `--ink` dot. The label shares the hit area.
- **Switch:** track 28×16 radius 8, thumb 12. Off: `--line` track ring, thumb `--ink-2`. On: `--ink` track, thumb `--on-ink`. Thumb moves 12px instantly on keyboard and with `--dur-base` `--ease-out` on click.
- **kbd:** Sofia 12/500 tabular, `--ink-2`, height 18, padding 0 4, radius 4, `box-shadow: inset 0 0 0 1px var(--line-subtle)`. Shortcut glyphs are platform-aware ("⌘" on macOS, "Ctrl" elsewhere) and glued with `&nbsp;`: `⌘&nbsp;F`.

### 5.2 App header and navigation

- **Header** (`<header>`, 56px): see §4.2. The wordmark is a `<Link to="/">` with the text "PaperMind" in Sofia 16/650 `--ink`. There is no logo glyph.
- **Nav:** `<nav aria-label="Main">` with two `<Link>`s: "Your papers" and "What you know". Sofia 14/550 `--ink-2`, 40px tall hit area. The current item gets `aria-current="page"`, `--ink` text and a 2px `--ink` underline at `text-underline-offset: 6px`. Weight doesn't change.
- **Settings:** icon button `GearSixIcon`, `aria-label="Settings"`, tooltip "Settings". Links to `/settings`.
- **Skip link:** first focusable element, "Skip to content" (on Reader pages, "Skip to the assistant"). Visually hidden until focused, then shown at top-left 8/8 as a Primary button, `--z-skiplink`.

### 5.3 Library row and page strip

```
  Attention Is All You Need                                   You stopped at §3.2
  Vaswani et al., 2017 · 15 pages
  ▮▮▮▮···········  p. 4 of 15
```

**Anatomy:** the whole row is one `<a href="/paper/1706.03762">`, so there are no dead zones. The overflow menu `[⋯]` is a separate button positioned in the right column, outside the link.
- **Title:** Literata 19/560 `--ink`, 2-line clamp, `text-wrap: balance`, `translate="no"`.
- **Meta:** Sofia 13/450 `--ink-2`. Format: "Vaswani et al., 2017 · 15 pages". One author: "Vaswani, 2017". Two: "Vaswani and Shazeer, 2017". For uploads with no metadata, the filename without ".pdf", then "Uploaded Oct 3".
- **Strip line:** the page strip, an 8px gap, then "p. 4 of 15" in Sofia 13/450 tabular `--ink-2`.
- **Right column:** 200px, right-aligned, Sofia 13/450 `--ink-2`. Shows "You stopped at §3.2", "You stopped on p. 4" (no sections), "Not opened", or ingestion status (§5.4). Below 900px it moves under the meta line.
- **Row box:** padding 12px; the hover background extends 12px beyond the text block (negative margin), radius 8. Rows are 8px apart. No rules or borders. Height is about 96px with a 1-line title.
- **States:** hover `--hover`; focus-visible ring on the whole row (radius 8); pressed `--pressed` (no scale); the row of the paper last opened in this session has no special state.
- **Row menu** (`DotsThreeIcon`, `aria-label="More for Attention Is All You Need"`): "Open", "Copy arXiv link" (arXiv papers only), divider, "Remove from your papers" (`--danger`). Remove is optimistic: the row disappears instantly, with an Undo toast for 6 s (§5.21). No confirmation dialog.

**Page strip** (`<svg role="img">` with an `aria-label`, plus the visible text next to it):
- One slot per page, at a 4px pitch (2px mark, 2px gap). Max 40 slots, so max 160px wide. 12px tall.
- **Read:** a 2×12 bar in `--ink-2`.
- **Not read yet:** a 2×2 dot at the baseline in `--ink-3`. Unread pages are never emphasised; the strip shows where you've worked, not how much is left.
- **Has your marks** (highlights on that page): a read bar with a 2px notch cut 3px from the top, drawn as two rects (2×3 and 2×7).
- **Pinned answer on that page:** a 2×12 bar in `--pencil`. Pencil wins over the notch.
- **Appendix:** an 8px gap (two slots) before the first page whose section number starts with a letter ("A", "A.1").
- **More than 40 pages:** bin pages into slots of `ceil(pages / 40)`. Each slot shows the strongest state in its bin (pinned > marks > read > not yet). A bin counts as read when ≥ 50% of its pages are read.
- **"Read" means:** ≥ 50% of the page's height has been inside the viewport for a cumulative 8 s. Fast scrolling past doesn't count.
- **`aria-label`:** "Read to p. 4 of 15, 3 highlights, 1 pinned answer". The hover tooltip shows the same text.
- Strips render only for papers that have been opened. A paper never opened shows no strip, just "16 pages" in the meta.

### 5.4 Add-paper flow

**The field is the empty state.** There is no modal and no "Upload" button that opens a dialog.

**Input:** `type="text"`, `inputmode="url"`, `spellcheck="false"`, `autocapitalize="off"`, `autocomplete="off"`, `aria-describedby` pointing at the hint line. Placeholder: "Paste an arXiv link or ID, or drop a PDF on this page…". Trailing text button inside the field on touch: "Choose PDF".

**Accepted and normalised on paste** (and on Enter or blur), in the client before `POST /papers`:
- `1706.03762`, `1706.03762v5`, `arXiv:1706.03762`, `arxiv.org/abs/1706.03762`, `arxiv.org/pdf/1706.03762v5`, `…/pdf/1706.03762.pdf`, `http(s)://` and `www.` variants
- Old-style IDs: `cs/0112017`, `math.GT/0309136`
- Whitespace is trimmed and a trailing `.pdf` dropped

**Validation timing:** validate on paste, Enter, or blur, never per keystroke. Once a field is in error, re-validate on every keystroke so the error clears the moment the input is fixed. On submit with an error, focus stays in the field.

**Flow**
1. Paste or Enter → the button label becomes "Looking up…" and the hint line reads "Looking up 1706.03762 on arXiv…".
2. Metadata found → the hint line becomes the confirmation in Sofia 13 `--ink`: "Found: Vaswani et al. (2017). *Attention Is All You Need*." The title is in Literata 15 roman, not italic: render it as `<cite>` with `font-style: normal`. The button reads "Add paper". Enter or click adds it.
3. Adding → the field clears, focus stays in it, and the new row appears at the top of the list immediately. That is the orchestrated moment (§6.1).

**Drag and drop:**
- The whole window is the drop target.
- On `dragenter` with a file, an overlay covers the list area: `--surface` at 92% opacity (`color-mix(in oklab, var(--surface) 92%, transparent)`), a solid 2px `--ink` outline inset 16px with radius 12, and centred text in Sofia 16/600: "Drop to add ‘attention.pdf’". For several files: "Drop to add 3 PDFs". `FilePdfIcon` 20px sits above the text.
- The overlay appears and disappears with no animation.
- Non-PDF files show the overlay text "Only PDFs can be added" in `--ink-2`, and dropping does nothing.

**Ingestion row:** the real stages come from `PaperSummary.stage`. Each step is shown inline in the row, left to right, Sofia 13/450.

| Backend stage | While running (`--ink`, ends with "…") | Done (`--ink-2`, `CheckIcon` 12px before it) |
|---|---|---|
| `queued` | "Waiting to start…" | — |
| `downloading` | "Downloading…" | "Downloaded" |
| `extracting` | "Reading 25 pages…" | "Read 25 pages" |
| `sectioning` | "Finding sections…" | "Found 9 sections" |
| `chunking` | "Splitting into passages…" | "212 passages" |
| `mapping` (Appendix A) | "Finding the ideas it builds on…" | "Builds on 23 ideas" |
| `indexing` | "Indexing 140 of 212 passages…" (falls back to "Indexing passages… 66%" without counts) | — |
| `done` | — | `CheckIcon` in `--success` + "Ready", shown for 4 s, then the right column changes to "Not opened" |

- **Layout:** pending steps are not shown. Only completed steps and the current one appear, so the line grows as work happens. On narrow widths only the current step shows, plus "3 of 6 steps done" in `--ink-2`.
- **Cancel:** a text button at the end of the line. It removes the row and toasts "Stopped adding Denoising Diffusion Probabilistic Models." No undo, since re-adding is one paste.
- **Read early:** as soon as `downloading` is done, the title becomes a link and the right column shows "Open now". The Reader works for reading. The panel says the paper is still being indexed (§5.25).
- **Progress semantics:** stage changes (not percentages or counts) are announced through the page's single polite live region (§8.1), e.g. "Attention Is All You Need: found 9 sections". The row itself has no live region.

**Validation and failure copy:** see §7.3. All of it appears in the hint line under the field (Sofia 13 `--ink` with a 16px status icon), never as a toast.

### 5.5 Library states

| State | What shows |
|---|---|
| Loading (first fetch > 200ms) | Three skeleton rows matching the real layout: a 19px-tall bar at 60% width, a 13px bar at 30%, a 12px strip at 160px, all `--hover` fill, radius 4, static (no shimmer, no pulse). Shown for at least 400ms once visible |
| Empty (first run) | §4.3 first-run block |
| One paper | The list with a single row, plus the add field above. No extra encouragement copy |
| 300 papers | Filter field (from 12 papers up), `content-visibility: auto` rows |
| Filter with no results | "No papers match ‘diffusion’." with a "Clear filter" text button |
| Fetch failed | In place of the list: `WarningCircleIcon` + "Couldn’t load your papers. The server didn’t respond." with "Try again" |
| Offline | Slim bar under the header (§5.25); the list stays visible from cache |
| A 200-character title | 2-line clamp with ellipsis; full title in the `title` attribute and the row's accessible name |

### 5.6 Reader top bar (PDF viewer chrome)

48px, `--surface`, 1px `--line-subtle` bottom rule, `position: sticky; top: 0; z-index: var(--z-topbar)`. Left to right:

| Item | Spec |
|---|---|
| Back | `<Link to="/">`: `ArrowLeftIcon` + "Your papers" (Sofia 14/550 `--ink-2`). Icon only below 1280px (`aria-label="Your papers"`) |
| Contents toggle | Icon button `SidebarSimpleIcon`, `aria-pressed`, tooltip "Show contents  [" / "Hide contents  [" |
| Paper title | Literata 16/560 `--ink`, one line, ellipsis, max 40% of the bar; tooltip with the full title after 600ms. Hidden below 600px |
| Page indicator | "p. 4 of 15" in Sofia 13/450 tabular `--ink-2`. Click or Enter turns "4" into a 40px number input (select-all, Enter jumps, Esc cancels); tooltip "Go to page". `aria-label="Page 4 of 15"` |
| Zoom | Icon buttons `MinusIcon` / `PlusIcon` (tooltips "Zoom out  ⌘ −" / "Zoom in  ⌘ +") around a text button "110%" (tabular) that opens a menu: Fit width (default), Fit page, Actual size, then 50%, 75%, 100%, 125%, 150%, 200%. Steps: 50, 67, 75, 90, 100, 110, 125, 150, 175, 200, 300%. The zoom value is hidden below 1100px |
| Find | Icon button `MagnifyingGlassIcon`, tooltip "Find in paper  ⌘ F". Opens the find field in place of the title: 240px input "Find in paper…", count "3 of 12" (tabular `--ink-2`), `CaretUpIcon` / `CaretDownIcon` ("Previous match  ⇧ ↵" / "Next match  ↵"), `XIcon` "Close find  Esc". No matches: "No matches" in `--ink-2`. Uses pdf.js find. Every match gets a 1.5px `--search-match` outline (radius 2); the current one gets 2px `--search-current` plus `--pdf-selection` fill |
| Appearance | Icon button `TextAaIcon`, tooltip "Appearance". Popover (§5.26) with Theme (System / Light / Dark); Page in dark mode (Dimmed paper / Night page / Inverted) plus its note; Answer text size (Small / Medium / Large) |
| Assistant toggle | Icon button `SidebarSimpleIcon mirrored`, `aria-pressed`, tooltip "Hide assistant  ]" / "Show assistant  ]" |
| More | `DotsThreeIcon` menu: "New conversation", "Copy conversation", divider, "Keyboard shortcuts  ?", "Paper on arXiv" (`ArrowUpRightIcon`), divider, "Remove from your papers" (`--danger`) |

The top bar never animates in or out on desktop. On phones it hides on scroll down and returns on scroll up, using `transform: translateY(-100%)` over `--dur-base` `--ease-out` (instant under reduced motion).

### 5.7 Contents (outline) and Your marks

**Container:** a 232px column (or a 280px drawer), `--surface`, padding 16 top and 8 sides, `overflow-y: auto`, `overscroll-behavior: contain`, `scrollbar-gutter: stable`. `<nav aria-label="Contents">`.

**Heading:** "Contents" in Sofia 13/600 `--ink-2`, 8px below the top padding.

**Rows:** a `<ul>` of links, 28px tall (2-line titles grow to 44), Sofia 13/450 `--ink-2`, radius 8, padding 0 8.
- **Hanging numerals:** section numbers sit right-aligned in a fixed 2.5em (32.5px) column, tabular, followed by an 8px gap. Titles start at the same x for every level, so depth reads from the numerals alone with no indent chrome. Unnumbered sections (Abstract, References) leave the number column empty.
- Titles wrap to 2 lines max, then ellipsis.
- **Current section** (the one whose heading is the last above 30% of the viewport): `--selected` background, `--ink` text, `aria-current="location"`. The previous row's background clears with no transition.
- Hover `--hover`, text `--ink`. Click: scroll the page so the heading sits 24px below the top bar (instant if more than 2 screens away, else smooth ≤ 300ms) and close the drawer if it's a drawer.
- **No sections found:** the heading reads "Pages" and rows are "Page 1" … "Page 15". A one-line note above them in `--ink-2`: "No section headings found, so this lists pages."

**Your marks** sits below the contents after 24px, with a 1px `--line-subtle` rule 12px above it:
- Heading: `HighlighterGlyph` 16 + "Your marks" in Sofia 13/600 `--ink-2`.
- Two disclosure rows: "3 highlights" and "1 pinned answer" (tabular), each expanding a list.
- **Highlight item:** an 8px swatch of its colour, the first 60 characters of the text in Sofia 13, and "p. 4". Click jumps. Row menu: Change colour, Ask about this, Remove highlight.
- **Pinned answer item:** `PinToPageGlyph` in `--pencil`, the first 60 characters of the question, and "p. 4". Click scrolls the panel to the answer and jumps the page. Row menu: Show answer, Unpin.
- **Empty:** "Highlights and pinned answers show up here." in Sofia 13 `--ink-2`.

### 5.8 PDF page, highlights and passage brackets

**Page box:** `[lane 32][page][lane 32]`. The lanes are part of the page's layout box at every zoom (20px on phones), so brackets are never clipped and "Fit width" fits page plus lanes.
- The page has radius 2 and sits on `--canvas`, with no shadow; the tone step is the edge. Pages are 16px apart.
- **Layers per page**, in z order: canvas (`filter: var(--page-canvas-filter)`); highlight layer (`mix-blend-mode: var(--hl-blend)`); exact-span underline layer; pdf.js text layer; bracket SVG layer (spans page and lanes, `pointer-events: none` except on bracket hit areas); annotation links.
- **Virtualization:** keep page placeholders in the DOM and release canvases offscreen. Never remove page elements, because that breaks selection and browser find.

**Reader highlights (yours):**
- Filled bands from the merged text-layer rects of the selection, in `--hl-*`, with 1px vertical padding and radius 4 on the outer corners only.
- The last colour used becomes the default (`H`).
- Highlights are clickable. Clicking opens a small popover: four swatches, "Ask about this", "Remove highlight".

**Passage brackets (the assistant's):**

*Input:* for each cited `SourceRef`, fetch `ChunkDetail.boxes` (`GET /papers/{id}/chunks/{chunk_id}`, cached per paper) when the `sources` event arrives. Convert to viewport space with `convertToViewportPoint` and keep them as percentages of a scale-1 viewport, so zoom never recomputes them. Lines are the text-layer spans whose centres fall inside those boxes. A box with no text spans inside (a figure, table or display equation rendered as graphics) is treated as one "line" spanning the whole box.

*Column detection (per page, computed once from the text layer):*
- `textLeft` and `textRight` are the 5th and 95th percentiles of span x-extents, and `mid = (textLeft + textRight) / 2`.
- The page is **two-column** if ≥ 60% of lines are narrower than `0.6 × (textRight − textLeft)` and those lines split on either side of `mid` with a gap of ≥ 8pt.
- Otherwise it is **single-column**.

*Placement (nearest-margin rule):*
- Single-column page: brackets go in the **right lane** (the side nearest the panel).
- Two-column page: a cited line whose centre is left of `mid` brackets in the **left lane**; right of `mid`, in the **right lane**. Never in the gutter between columns.
- Full-width items on a two-column page (title, abstract, wide figures): right lane.

*Segments:* a citation becomes one segment per contiguous run of lines in one (page, column).
- A span that crosses a column or page boundary becomes several segments, each carrying the same numeral.
- A segment that continues elsewhere has an **open end**: no tick at that end.
- A span of lines with ≤ 1.5 line-heights of vertical gap between them is one segment.

*Geometry (right lane; mirror it for the left lane):*
- `X` is the column's right text edge. Lane `k` (0, 1, 2) puts the vertical stroke at `x = X + 10 + 6k` px.
- The stroke runs from the top of the first line box (`y0`) to the bottom of the last (`y1`), minus 1px at each end.
- Ticks are 4px long, pointing toward the text, at `y0` and `y1`, with round caps.
- The stroke is a filled path with a fixed taper: 1.25px wide at the ends and 1.75px through the middle, in `--bracket`.
- **Numeral:** Sofia 12/600 tabular in `--bracket`, its baseline 3px above `y0`, centred on the stroke's x. Numerals are 1–8 (`MAX_SOURCES` = 8), so always one digit.
- Two numerals that would collide (within 8px horizontally and 12px vertically) stack: the later one moves 13px up.
- **Pinned answers that aren't active** replace the numeral with `PinToPageGlyph` at 10px, and their stroke is a uniform 1.25px.
- **Hit area:** each segment is a `<button>` covering the lane strip from `y0 − 8` to `y1 + 8`, at least 24×24.

*Lanes and overlap:* assign lanes per (page, side) greedily by vertical overlap, lowest lane first, with lane 0 nearest the text. Max 3 lanes. If a fourth overlapping citation needs a lane, all citations in that overlapping cluster merge into one bracket spanning their union in lane 0. Its label is the numeral range "1–4" if contiguous, or "1, 3, 4" if not, set above the top tick and extending outward.

*Zoom clipping:* when the page is wider than the PDF viewport and the bracket's lane is scrolled out of view horizontally, the bracket is drawn in a 14px strip clamped inside the visible edge of the PDF viewport on that side, at the same y.

*Visibility:*
- The **active answer's** brackets are shown: the last answer the reader interacted with, initially the newest.
- **Pinned answers'** brackets are always shown, capped at the 3 most recent pins per page. More pins on that page are listed in Your marks.
- All other brackets are hidden. When the next question is sent, the previous answer's unpinned brackets fade out over `--dur-undraw`.

*Scanned PDFs (no text layer):* no brackets and no underlines. The chip still jumps to the page. The panel shows one line under the first answer: "This PDF has no text layer, so citations show the page only."

*Accessibility:*
- Each bracket segment is a `<button aria-label="Citation 2, section 3.2.1, page 4. Show the answer">`. Enter or click scrolls the panel to the answer and moves focus to the originating chip. Esc while a bracket is focused also returns focus to the chip.
- Brackets are in the tab order after the page's links, in numeral order.
- Hovering or focusing a bracket shows the exact-span underline for as long as it's held.

**Exact-span underline:** a 1.25px `--bracket` line 2px below each cited line's baseline, spanning the line's text extent. It appears (a) for 1.2 s after a jump, fading to 0 opacity (§6), and (b) while a chip or bracket is hovered or focused, at full opacity with no fade. It is never shown for pinned, inactive answers.

**Back pill:** after any jump that moves more than one screen, a Secondary-style button appears bottom-centre of the PDF column, 16px above the bottom edge: `ArrowUUpLeftIcon` + "Back to p. 11". It is 32 tall, radius 8, `--raised` with `--shadow-sm`, `--z-backpill`. It stays for 10 s or until used; `Alt+←` does the same. The jump history stack holds 20 entries.

### 5.9 Depth control

**Placement:** the panel header, a 52px sticky row (`--z-panel-header`, `--surface`, padding 0 16). Content: the label "Depth" (Sofia 13/550 `--ink-2`, `id="depth-label"`), 8px, then the control.

**Control:** Radix `ToggleGroup type="single"` with `aria-labelledby="depth-label"`. Guard empty values: `v && setDepth(v)`.
- **Track:** height 32, padding 2, radius 8, `box-shadow: inset 0 0 0 1px var(--line)`, transparent fill.
- **Segments:** three equal segments, 84px each, Sofia 13/550, `--ink-2`. Labels: **From basics** · **Standard** · **Technical** (internal `beginner` · `intermediate` · `expert`).
- **Thumb:** 28px tall, radius `calc(8px - 2px)`, `--raised` fill with `--shadow-sm`, behind the selected label. The selected label is `--ink` and weight 650. To stop the weight change shifting layout, each label reserves its 650-weight width with a hidden duplicate (`::after { content: attr(data-label); font-weight: 650; visibility: hidden; height: 0; display: block; }`).
- **Motion:** on pointer click the thumb slides with `transform: translateX()` over `--dur-base` `--ease-in-out`. From the keyboard (arrows, Home, End) it moves instantly. Theme changes never animate it.
- **Tooltips** (600ms, describing the answer, not the reader):
  - From basics: "Defines terms as they come up and leans on intuition. Math only when it helps."
  - Standard: "Assumes neural nets and backprop. Explains this paper’s new ideas and terms."
  - Technical: "Skips the background. Precise, shows the math, compares with related work."
- **First-use help:** the first three times the panel opens for a user, the selected option's description shows as a visible line under the header (Sofia 13 `--ink-2`, padding 0 16 8), followed by "Applies from your next question." After that it lives only in the tooltip.
- **Behaviour:** a change applies to the next question and is sent as `ChatRequest.level`. It is stored in the session and in the URL (`?depth=`). Changing it never re-answers anything; Simpler and More technical do that per answer.
- **Narrow:** below 360px of panel width (phones), replace it with a native `<select>` labelled "Depth" with the three options and a `CaretDownIcon`, 36 tall, Sofia 14/550.

### 5.10 The reader's question

Flat, with no bubble, avatar or name. An `<article>` per turn with `aria-labelledby` pointing at the question.
- **Question:** Sofia 15/500 `--ink`, `max-width: 32em`, preserving line breaks (`white-space: pre-wrap`).
- **Selection quote** (when the question came from a selection) sits above the question:
  - The **in-panel bracket**: a 1.5px `--pencil` vertical stroke with 4px ticks at both ends, set 4px left of the quote text. The quote is indented 16px.
  - Quote text: Literata 16/24px `--ink`, roman, `hanging-punctuation: first last`, clamped to 6 lines with "Show all" (text button) if longer.
  - Under it, the locator "§3.2.1, p. 4" in Sofia 13/450 `--ink-2`. It's a link that jumps to the passage.
  - Quote → question gap: 8px.
- **Optimistic send:** the question appears instantly and the composer clears. If the request fails before streaming, the question shows "Not sent." in `--ink-2` with a "Try again" text button, and the text is restored to the composer.
- **Queued** (paper still indexing): under the question, `ClockIcon` 14 + "Waiting for the paper to finish indexing, about 30 s. This will send when it’s ready." in Sofia 13 `--ink-2`, with a "Cancel" text button.

### 5.11 Agent activity trail

A single line under the question, Sofia 13/450 `--ink-2`, tabular figures, 8px below the question.

**While working** (one live line, replaced in place, never stacked):
- Before the first `tool.started` arrives, after 300ms: "Finding the relevant parts of the paper…"
- Then the current tool's present-tense line (table below).
- When the `sources` event arrives, before any text: "Reading from §3.2.1, §3.2.2 and §5.4…". The same sections get `--selected` in the outline until the answer is done.
- During `answer.delta`: "Writing the answer…"
- **Elapsed time** joins the line after 8 s as "… 9 s" (tabular, updated once a second). After 20 s, a reason is added when one is known (a slow tool): "Semantic Scholar is slow right now."
- No spinner, shimmer, pulsing dot or animated ellipsis. The line changes text and that's all.

**When done** (on `answer.done`), the line collapses into one summary sentence with a disclosure button:

`Read 4 passages and checked 2 ideas, 3.1 s ▸`

- **Template:** clauses in tool order, joined with commas and a final "and": "Read N passage(s)", "checked N idea(s)", "worked out a learning path", "looked up N paper(s)", "found N reference(s)", "found N citing paper(s)". Then a comma and the total turn time with `&nbsp;s`. If no tools ran: "Answered from the conversation, 1.2 s".
- **Counts:** "passages" = `sources.length` (what the writer was given, not raw search hits); "ideas" = number of `check_prerequisites` calls that matched a concept; "papers" = distinct Semantic Scholar papers in `tool.finished.detail`. The time is `usage.latency_ms` (or the client's elapsed time until `usage` arrives), one decimal.
- **The button** wraps the whole line: `aria-expanded`, `aria-controls`, and `aria-label` = the line plus ", show steps".
- **Expanded:** an ordered list, one row per finished tool: the past-tense summary, then its duration in `--ink-2` tabular "(0.4 s)". A retried call appends " (second try)". A failed one gets a `WarningIcon` 12px in `--ink-2` (not danger) and its failure text.
- **The trail never disappears.** It stays on the answer, collapsed.

**Copy per tool.** Live lines come from `tool.started.label`. Done lines come from `tool.finished.summary`. Backend strings must match this table (Appendix A lists the three that need changing).

| Tool | Live (present, ends "…") | Done (past) | Failed |
|---|---|---|---|
| `search_paper` | Searching the paper for ‘scaled dot-product’… / Searching §3 for ‘…’… | Found 5 passages in §3.2.1 and §3.2.2 · No passages matched ‘…’ | Couldn’t search the paper |
| `check_prerequisites` | Checking what ‘multi-head attention’ builds on… | ‘Multi-head attention’ builds on 2 ideas that haven’t come up in your reading yet: softmax, dot product · You’ve met everything ‘…’ builds on · ‘…’ isn’t in the concept map yet | Couldn’t check what this builds on |
| `get_learning_path` | Working out an order for learning ‘…’… | Suggested order: dot product, softmax, scaled dot-product attention | Couldn’t work out a learning path |
| `lookup_paper` | Looking up ‘Bahdanau 2014’ on Semantic Scholar… | Found ‘Neural Machine Translation by Jointly Learning…’ (2014) · Nothing on Semantic Scholar for ‘…’ | Couldn’t reach Semantic Scholar |
| `get_references` | Finding what this paper cites… / Finding what ‘…’ cites… | Found 12 papers it cites | Couldn’t reach Semantic Scholar |
| `get_citations` | Finding later work that cites this paper… | Found 8 papers that cite it | Couldn’t reach Semantic Scholar |
| `compose_answer` | (not shown; the line moves to "Writing the answer…") | Wrote the answer (2.1 s), listed last in the expanded view | — |

**Partial failure:** when a tool failed but an answer still arrived, one extra muted line appears under the summary, for example "Couldn’t reach Semantic Scholar, so this answer uses the paper alone."

### 5.12 Text-selection popover ("Explain" / "Ask about this")

**Trigger:**
- On `pointerup` or `keyup`, a selection of ≥ 2 non-space characters inside the pdf.js text layer opens the popover.
- Selections may cross onto the next page. The quote's page is the page where the selection starts.
- Use pdf.js's `endOfContent` handling so a drag never sweeps up the other column.

**Position:** Radix `Popover` with a virtual anchor at the selection's bounding rect, 8px above the first selected line and horizontally centred on the selection, clamped inside the page. It flips below the last line when there is less than 56px above. `onOpenAutoFocus={e => e.preventDefault()}`, so the popover never steals focus or clears the selection.

**Anatomy:** a `role="toolbar"` with `aria-label="Selection"`. It is `--raised`, radius 12, padding 4, `--shadow-md`, 40 tall.

```
[ Explain  E ][ Ask about this  A ] │ ( ● ● ● ● ) │ [⋯]
```

- **Text buttons:** 32 tall, radius `calc(12px - 4px)`, padding 0 8, Sofia 14/550 `--ink`. Each is followed by its `kbd`.
- **Divider:** 1×20px `--line-subtle`, 4px margin each side.
- **Swatches:** 16px circles filled with `--hl-yellow`, `--hl-green`, `--hl-blue` and `--hl-orange`, each with an inset 1px `rgb(0 0 0 / .12)` ring, centred in 28px buttons. `aria-label`s: "Highlight yellow (1)", "Highlight green (2)", "Highlight blue (3)", "Highlight orange (4)". The last-used colour's button has `aria-pressed="true"` and a 2px `--ink` ring around the swatch.
- **More** (`DotsThreeIcon`, `aria-label="More"`) opens a menu: "Copy" and "Copy with citation".

**Actions:**
- **Explain (E):** sends the question "Explain this passage." with the selection as `quote`, at the current depth. It opens the panel if it's collapsed: on tablet the sheet opens, on phones the sheet rises to 50%.
- **Ask about this (A):** attaches the selection to the composer as a quote chip (§5.20), keeps any draft, and focuses the composer.
- **Highlight (H for the last colour, 1–4 for a specific one):** creates the highlight, closes the popover and clears the selection.
- **Copy with citation:** copies "“…selected text…” (Vaswani et al., 2017, §3.2.1, p. 4)".

**Dismiss:** Esc, a scroll of more than 4px, pointer-down elsewhere, or the selection collapsing.

**Motion:** none. It appears and disappears instantly because selecting is high-frequency.

**First-use hint:** for the reader's first two selections, a second line sits under the toolbar inside the same popover, Sofia 13 `--ink-2`, padding 4 8: "Tip: press E to explain a selection."

**Keyboard:**
- E, A, H and 1–4 work while a selection exists and focus isn't in a text field (and single-key shortcuts are on).
- Tab from the page moves into the toolbar. Arrow keys move between its items. Esc returns focus to the page.

**Phones:** the popover is replaced by a 44px action bar docked above the bottom sheet: "Explain", "Ask about this", "Highlight" (which opens the four swatches).

### 5.13 Assistant answer and the streaming state

**Turn order** (top to bottom; gaps from §2.4):
1. Quote (if any)
2. Question
3. Trail
4. Background-only line (if any)
5. Gap callout (if any)
6. Answer body
7. Knowledge line (if any)
8. Actions
9. One-time legend (first cited answer only)
10. Nudges and follow-ups (if any)

**Answer body:** a `<div class="answer">` in `--font-read` at `--answer-size` / `--answer-leading` / `--answer-weight`, `--ink`, `max-width: var(--answer-measure)`. Paragraph spacing is 0.7em. No container, background, border or bubble.

**Streaming**
- **Buffer:** network chunks are buffered and revealed word by word on `requestAnimationFrame` at 40 words/s. If the buffer holds more than 400 characters, the rate doubles (adaptive catch-up), so the display never lags far behind the model. On `answer.done`, everything left is flushed in one frame.
- **Word reveal:** each newly revealed word fades from opacity 0 to 1 over `--dur-word` (150ms) `linear`. Opacity only: no translate and no blur. Under reduced motion words are appended with no fade.
- **No glyph at the end of the text.** No caret, dot or cursor. The trail line ("Writing the answer…") and the Stop button carry the state.
- **Re-parsing:** the answer is split into top-level markdown blocks. Finished blocks are memoised and only the last block re-parses.
  - The last block hides a half-typed `[S` marker.
  - It hides unclosed `$`, `$$`, `\(` and `\[` math. `\(…\)` and `\[…\]` are normalised to `$` and `$$`.
  - An open `$$` block reserves an empty 2.5em-tall block until it closes (no shimmer).
  - Code renders in monospace immediately.
- **Wrapping:** `text-wrap: pretty` is applied by adding `.prose-done` after `answer.done`, never while streaming, because re-breaking the last lines while tokens arrive makes them jitter.
- **ARIA:** the turn `<article>` has `aria-busy="true"` while streaming. Streaming text is not live-announced token by token. On `answer.done`, a visually hidden polite live region says "Answer ready, 2 citations." once.

**Scrolling**
- On send, the panel scrolls so the new question's top sits 24px below the panel header. The thread has a bottom spacer equal to the viewport height so this is always possible.
- The answer then grows downward, and **the panel does not follow it**. The reader reads from the start at their own pace.
- If new content is below the visible area, a "Jump to latest" pill appears bottom-centre of the thread, 12px above the composer. It has `ArrowDownIcon` + "Jump to latest", Secondary style, 28 tall, `--raised`, `--shadow-sm`. Clicking scrolls to the end: instantly under reduced motion, otherwise smooth over at most 300ms. The pill hides when the reader is within 80px of the end.
- The thread uses `overscroll-behavior: contain` and `scrollbar-gutter: stable`.

**Stop:**
- Esc or the Stop button aborts the request and keeps everything already shown.
- A line follows the partial text, Sofia 13 `--ink-2`: "This answer stopped partway. Your question and the text so far are saved." with a "Try again" text button. Try again re-sends the same question and quote as a new version (§5.19).

**Provenance in the body** (B's honesty, in A's language):
- In an answer with at least one citation, each paragraph or list item **without** a citation starts with a run-in label: "Background", Sofia 12/550 `--ink-2`, followed by an 8px space, inline before the first word.
  - The label has a tooltip: "General background, not from this paper, so there’s no bracket."
  - The label is a neutral marker, not a demotion: the paragraph's text stays `--ink`.
- An answer with **no** citations but where tools ran gets one line under the trail instead of labels: "General background. Nothing in this answer comes from the paper."
- Small talk (no tools, no sources) gets neither.
- The setting "Label background paragraphs" turns labels off.
- **Ship gate:** the labels default to on only after the eval metric "paper-derived paragraph without a citation" is under 5% on the benchmark. Until then the setting defaults off and the legend omits its second sentence. A false "Background" is worse than none.

**One-time legend:** under the first answer (per user) that has a citation. `BracketGlyph` 16 in `--pencil`, then Sofia 13 `--ink-2`: "Numbered brackets on the page mark the exact lines an answer used. Paragraphs marked Background are general knowledge, not from this paper." Then a "Got it" text button. It never shows again after Got it or after 3 answers.

### 5.14 Markdown and math inside answers

| Element | Style |
|---|---|
| `p` | margin 0 0 0.7em; last child 0 |
| `h1`, `h2` (h1 renders as h2) | Sofia 16/650 `--ink`, margin 1.2em 0 0.35em |
| `h3`–`h6` (render as h3) | Sofia 15/650, margin 1em 0 0.3em |
| `strong` | weight 600 (Literata) |
| `em` | Literata italic |
| `ul` | padding-left 1.25em; `::marker` `--ink-2`; items 4px apart; one visual level of nesting |
| `ol` | padding-left 1.5em; `::marker` tabular `--ink-2` |
| `a` | `--ink`, `text-decoration: underline 1px`, offset 3px, decoration colour `--ink-3`; external links get `ArrowUpRightIcon` 12px after and open in a new tab with `rel="noopener noreferrer"` |
| inline `code` | `--font-mono` 0.875em, `--hover` background, radius 4, padding 0.1em 0.3em, `translate="no"` |
| code block | `--font-mono` 13/20, background `--canvas`, radius 8, padding 12 16, margin 0.7em 0, `overflow-x: auto`, no wrapping, no syntax highlighting in v1. After `answer.done` a "Copy" text button appears at top-right 8/8 |
| `blockquote` | Only for a verbatim quote of the paper. If it contains or is followed by a citation, render it like the question's quote: in-panel bracket, Literata 16 roman, locator under it. Otherwise render it as a paragraph indented 16px in `--ink-2` with no rule |
| table | wrapper `overflow-x: auto`; Sofia 14/20 tabular; `th` 550 left-aligned; 1px `--line-subtle` rule under the header and between rows; cell padding 8 12 8 0 |
| `hr` | not rendered |
| images | not rendered; alt text shown as a paragraph in `--ink-2` |
| inline math | KaTeX at `--katex-in-read` (1.18em), `translate="no"` |
| display math | block, margin 0.7em 0, padding 4px 0, `overflow-x: auto; overflow-y: hidden` |
| KaTeX error | `throwOnError: false`; `.katex-error` is restyled to the raw TeX in `--font-mono` 13px `--ink-2`, never red |

**Typography pass** (a remark plugin, outside code and math): straight quotes become curly, `...` becomes `…`, ` -- ` becomes ` – `, "p. 4", "Fig. 2" and "Eq. 3" get `&nbsp;`, and a number followed by a unit ("3.1 s", "50 MB") gets `&nbsp;`.

### 5.15 Citation chip, hover card, jump to passage

**Source:** `[S1]` markers in the answer become chips. The numeral is the order of first appearance in that answer (1–8). It matches the bracket numeral on the page. Adjacent markers render as adjacent chips 4px apart.

**Chip anatomy:** `<button class="chip">`, `display: inline-flex`, height 20, padding 0 4, gap 4, radius 4, `vertical-align: 0.1em`. It has no fill: `box-shadow: inset 0 0 0 1px var(--line-subtle)`. A chip is never filled with mulberry; it's identified by its pencil numeral.
- **Numeral:** Sofia 12/600 tabular `--pencil`.
- **Location:** Sofia 12/500 tabular `--ink-2`.
- **Location rule:**
  - Section number present: `§3.2.1`.
  - Two chips in one answer share a section: `§3.2.1&nbsp;p.&nbsp;4`.
  - No section number: `p.&nbsp;4` (`page_start + 1`).
  - Multi-page passage: `pp.&nbsp;4–5`.
- **Hit area:** `::before { inset: -2px 0 }` for 24px; on coarse pointers `inset: -12px -4px`.
- **Accessible name:** "Citation 1: section 3.2.1, page 4". The visual "§" is spelled out.

**States**

| State | Visual and behaviour |
|---|---|
| Rest | as above |
| Hover | `--hover` background. After `--delay-bracket` (150ms) its bracket draws (§6.2); if the bracket is already drawn (active or pinned answer), its exact-span underline shows instead, for as long as the hover lasts. After `--delay-card` (400ms) the passage card opens |
| Focus-visible | ring, bracket drawn, and card opened **immediately** (no delays for keyboard) |
| Active (the citation last jumped to) | ring becomes `inset 0 0 0 1px var(--pencil)` |
| Pressed | `transform: scale(.98)` over `--dur-press` |

**Passage card:** non-modal (Radix `HoverCard`, or `Popover` on touch). `--raised`, radius 12, `--shadow-md`, width `min(320px, panel width − 32px)`, padding 12 16. Offset 8px above the chip, flipping below; `transform-origin` at the chip. Close grace `--grace-card` (150ms) so the pointer can travel into it; Esc closes.

```
Section 3.2.1 · Scaled Dot-Product Attention        Sofia 13/550 --ink, 1 line, ellipsis
Page 4                                              Sofia 13/450 --ink-2, tabular
                                                    ↕ 8
…We suspect that for large values of dₖ, the dot    Literata 15/1.5 --ink, 5-line clamp;
products grow large in magnitude, pushing the       the cited sentence (when the backend
softmax function into regions…                      sends it) at weight 600
                                                    ↕ 12
Show in paper  ↵                                    text button + kbd
```

**Jump (click, Enter on a focused chip, or "Show in paper"):**
1. Scroll the PDF so the first cited line sits 30% from the top of the PDF viewport. Use a smooth scroll (≤ 300ms, `--ease-in-out`) if the target is within 2 viewport heights, otherwise jump instantly. Always jump instantly under reduced motion.
2. Draw the bracket if it isn't drawn, then **re-ink** it and show the exact-span underline, both fading over `--dur-settle` (§6.3).
3. Mark the chip active. Push the previous position onto the back stack and show the back pill.
4. On tablet, tuck the overlay sheet to the rail. On phones, drop the sheet to peek.
5. Announce "Showing section 3.2.1, page 4" in the polite live region.
6. **Keyboard activation only:** move focus to the bracket button. Pointer clicks leave focus on the chip.

**External papers** (Semantic Scholar results the writer cites with `[P1]`, Appendix A) are not chips. They are inline text, "Bahdanau et al., 2014", in Sofia 13/500 `--ink-2` with a 1px `--ink-3` underline at offset 3px. They have no box, numeral or fill, because they have no bracket. The element is a `<button>` that opens an external-paper card (same container as the passage card):

```
Neural Machine Translation by Jointly Learning      Literata 15/560 --ink, 2-line clamp
to Align and Translate
Bahdanau, Cho and Bengio · ICLR · 2015              Sofia 13/450 --ink-2
                                                    ↕ 8
TLDR                                                Sofia 12/550 --ink-2
Introduces an attention mechanism that lets the     Sofia 14/400 --ink (Semantic Scholar TLDR)
decoder look back at the source sentence.
                                                    ↕ 12
Ask about this paper   Add to your papers   Semantic Scholar ↗
```

- "Ask about this paper" fills the composer with "How does this paper use Bahdanau et al., 2014?" and focuses it.
- "Add to your papers" appears only when the paper has an `arxiv_id`.
- The Semantic Scholar link opens in a new tab.

### 5.16 Prerequisite-gap callout and learning path

**When it shows** (all must be true):
- A `gaps` event arrived with at least one gap of `depth > 0`.
- It is the first callout in this answer (max one per answer).
- Onboarding is done or skipped.
- The concept wasn't dismissed with "I know …" or "Hide this note", and wasn't explained in this session.
- At **Technical** depth, only gaps with `depth === 1` count.

**Never blocks:** the answer streams below the callout whether or not the reader acts on it.

**Anatomy:** an uncontained margin note, with no box, fill, stripe or border.

```
‸  This builds on softmax, which hasn’t come up in your reading yet.        [×]
   Suggested order:  ● dot product ─ ○ softmax ─ ‸ scaled dot-product attention
   [Explain softmax first]   I know softmax
```

- **Hanging column:** 24px wide, holding `CaretInsertGlyph` 16px in `--pencil`, aligned to the first line's cap height.
- **Sentence:** Sofia 14/20 `--ink`. Concept names are buttons styled as links (`--ink`, underline on hover) that do the same as "Explain … first" for that concept.
  - 1 concept: "This builds on softmax, which hasn’t come up in your reading yet."
  - 2: "This builds on softmax and the dot product, which haven’t come up in your reading yet."
  - 3 or more: "This builds on softmax, the dot product and 2 more ideas that haven’t come up in your reading yet."
- **Hide:** `XIcon` icon button, `aria-label="Hide this note"`, 24px, top-right of the sentence row.
- **Learning path** (only when `teaching_order` has ≥ 2 entries and depth is not Technical), 8px below the sentence:
  - The label "Suggested order:" in Sofia 13 `--ink-2`, then an inline, wrapping row of stops. Each stop is a mark, a 4px gap, then the name in Sofia 13/450 `--ink`.
  - Stops are separated by a 12×1px `--line` connector, vertically centred on the marks. The connector is hidden when a stop wraps to the start of a line.
  - **Marks follow the system grammar.** Already in what you know: an 8px **filled** circle in `--ink-2` (fills are the reader's). Not yet: an 8px circle **stroked** 1.5px in `--pencil`, empty inside (the assistant suggests in stroke). The concept asked about: `CaretInsertGlyph` 12px in `--pencil`.
  - At most 4 stops are shown: the first two, then "2 more" (a button that expands the row), then the destination.
  - Not-yet stops are buttons that ask "Explain {concept}." at the current depth.
  - The path is a teaching order (topological), not a chain of direct dependencies. The connector is a neutral line, never an arrow, and the label says "Suggested order".
  - At Technical depth there is no path, only the sentence.
  - Accessibility: `<ol aria-label="Suggested order">`, items read "dot product, already in what you know", "softmax, not yet in what you know", "scaled dot-product attention, what you asked about".
- **Actions** (8px below):
  - **"Explain softmax first"** (plural "Explain these first"): a Secondary button, 32 tall. Not Primary: the detour is never the default. It sends "Explain softmax before we go on." as a new question at the current depth. The current answer stays.
  - **"I know softmax"** (plural "I know these"): a Text button. It is optimistic: `POST /users/{id}/knowledge` per concept, no proof needed. The callout is replaced in place by the line "Added softmax to what you know. Undo" in Sofia 13 `--ink-2`. Undo deletes the concepts again and restores the callout. On failure, the callout returns with "Couldn’t save that. Try again".

**Words never used here:** missing, gap, lacking, you don't know, required, prerequisite (in visible copy). No red and no warning icons.

### 5.17 Suggested questions

- **Source:** `GET /papers/{id}/suggestions?level=` gives 3 questions, shown only in the empty panel. After an answer, up to 2 follow-ups appear only if the backend sends them (`answer.done.followups`, Appendix A). No generic fillers ("Tell me more", "Summarize the paper").
- **Anatomy:** `<ul aria-label="Suggested questions">` of full-width Text buttons, left-aligned, Sofia 14/20 `--ink`, padding 4 8, radius 8, min-height 32, wrapping. The text renders through the inline markdown + math renderer, so `$\sqrt{d_k}$` typesets (KaTeX at `--katex-in-ui`). An optional section hint follows in `--ink-2`, e.g. "§3.2".
- **Dismiss:** `XIcon` icon button `aria-label="Hide suggestions"`, at the top right of the list. Hidden per paper (stored).
- **Click:** sends the question as written, at the current depth.
- **Depth changed before the first question:** re-fetch with the new level, with no animation.
- **Loading:** 3 static skeleton bars 14px tall at 80%, 65% and 72% width, after a 200ms delay.
- **Failed:** the list is omitted silently. The empty-state sentence is enough.

### 5.18 Panel empty and returning states

**New paper, no questions yet** (top-aligned, starting 24px under the header, never centred):

```
Ask about anything on these pages, or select a sentence     Sofia 14/20 --ink-2
and press E. If an answer assumes too much, press Simpler.
                                                            ↕ 16
Why divide the dot products by √dₖ?                    §3.2 (suggested questions)
What does multi-head attention give you that one head doesn’t?
How does this compare with the RNNs it replaces?
                                                            ↕ 24
This paper builds on 7 ideas. 2 haven’t come up in your     Sofia 13 --ink-2; only when
reading yet: softmax and layer normalization.               concept mapping exists (App. A)
```

- In the readiness line, the concept names are buttons that ask "Explain softmax." at the current depth. Readiness is never shown in the Library.
- **First paper ever:** onboarding (§4.6) replaces this block once.
- **Returning to a paper with history:** the thread is restored and scrolled to the last answer. After the last turn, one line: "You stopped at §3.2 last time." (Sofia 13 `--ink-2`) with a "Recap up to §3.2" text button that sends "Recap what I’ve read so far, up to §3.2." The line disappears when the reader asks anything. No toast, no greeting.
- **Paper still indexing:** at the top of the panel, `ClockIcon` + "Still indexing this paper. You can read now; questions will send when it’s ready, in about 30 s." Suggestions load when the paper is ready. The ETA is computed from the progress rate over the last 5 s, rounded to 10 s. If it's unknown: "…when it’s ready, shortly."
- **No text layer (scanned):** `WarningIcon` (`--warn`) + "This PDF has no text layer, so PaperMind can’t search it. You can still read it here. If it’s on arXiv, add it by ID instead." The composer still works. Sending shows this same message inline under the question.

### 5.19 Answer actions, versions, nudges, knowledge line, pins

**Actions row** (12px under the answer): Text buttons, Sofia 13/500 `--ink-2`, 28 tall: **Copy** · **Simpler** · **More technical** · **Pin to page** (or **Unpin**) · `DotsThreeIcon` "More actions" (menu: "Copy with citations", "Report a problem…").
- **Visibility:** always visible on the latest answer and on `(hover: none)` devices. On older answers they show on turn hover and `:focus-within`. Their space is always reserved, so nothing shifts. Hidden while streaming (space still reserved).
- **Copy:** copies markdown with chips converted to "(§3.2.1, p. 4)". The label reads "Copied" for 1.5 s and the polite live region says "Copied".
- **Copy with citations:** the answer plus a list after a blank line:
  ```
  Sources
  1. Vaswani et al. (2017). Attention Is All You Need, §3.2.1, p. 4.
  2. Vaswani et al. (2017). Attention Is All You Need, §3.2.2, p. 5.
  ```
  External papers are cited in the text as "(Bahdanau et al., 2014)".
- **Simpler / More technical:** re-answer **this question only** at the adjacent depth (From basics ← Standard ← Technical). At an end of the range the same depth is requested with the instruction "simpler still" or "more technical still". The new answer replaces the body as a **version**.
  - A pager appears above the actions row: `CaretLeftIcon` button "Previous version", "Version 2 of 2 · Simpler" (Sofia 13 tabular `--ink-2`), `CaretRightIcon` button "Next version".
  - Versions keep their own citations and brackets. The page shows the visible version's brackets.
- **Nudge:** after two Simpler presses in a row (on any answers in this session), one line under the actions row: "Make From basics your default for new questions?" with text buttons "Yes" and "Not now". The mirror after two More technical presses: "Make Technical your default for new questions?". Each direction asks at most once every 30 days. "Yes" updates the depth control, the session and `PATCH /users/{id} {level}`.
- **Knowledge line** (from `knowledge.updated`, under the answer, above the actions), Sofia 13 `--ink-2`:
  - "Added softmax to what you know. Undo"
  - "Added softmax and the dot product to what you know. Undo"
  - "Took softmax off what you know. Undo"

  Undo reverses it via the knowledge API and the line reads "Undone." for 2 s. No toast, no confetti.
- **Recheck** (Appendix A, `knowledge.updated.ask`): when new evidence contradicts something the reader said they know, the backend asks instead of downgrading silently. Line: "Softmax came up again. Still comfortable with it?" with "Yes" and "Refresh me". "Refresh me" sends "Give me a quick refresher on softmax." and moves it to Seen it.
- **Pin to page:** toggles a persistent pin, stored per paper. The button label becomes "Unpin". A confirmation line under the actions for 4 s: "Pinned to page 4. Its brackets stay on your copy." If that page already shows 3 pins: "Pinned. Page 4 shows the 3 most recent pins; the rest are in Your marks." Unpinning removes the brackets at once (`--dur-undraw` fade), with no line.
- **Report a problem…:** opens a dialog (§5.23).

### 5.20 Composer

**Container:** docked at the bottom of the panel with padding 0 16 16. It is `--raised`, radius 12, padding 4, `box-shadow: inset 0 0 0 1px var(--line)`, and has no drop shadow (it's in flow). On `:focus-within` the **container** gets the 2px `--focus` outline at 2px offset; the textarea itself has no outline.

```
+--------------------------------------------------+
| “…the dot products grow large in magni…”  §3.2.1, p. 4  [×] |  quote chip row (optional)
| Ask about this passage…                          |  textarea, 1–8 lines
|                                          [ Ask ] |
+--------------------------------------------------+
```

- **Quote chip** (when a selection is attached): one 28px row inside the top of the composer, background `--hover`, radius `calc(12px - 4px)`, padding 0 8.
  - The quote in Sofia 13 `--ink`, one line with ellipsis, in curly quotes. The locator in Sofia 13 `--ink-2`.
  - `XIcon` button `aria-label="Remove quote"`.
  - One quote at a time; a new selection replaces it.
- **Textarea:** Sofia 15/22 (16px on coarse pointers), `--ink`, transparent, padding 4 8. It grows from 1 to 8 lines, then scrolls inside. `enterkeyhint="send"`, `aria-label="Ask about this paper"`.
- **Placeholder:** "Ask about this paper…". With a quote attached: "Ask about this passage…". Never "Message PaperMind".
- **Button:** bottom-right, Primary, 28 tall (44 on coarse), radius `calc(12px - 4px)`, label **Ask**, tooltip "Ask  ↵".
  - While streaming it becomes **Stop** (`StopIcon` fill 12px + "Stop"), tooltip "Stop  Esc". Its `min-width` reserves the wider label, so it never changes width.
  - With an empty input, clicking Ask just focuses the textarea. It is never disabled.

**Keys**
- **Enter** sends, except while an IME is composing (`e.nativeEvent.isComposing`, `keyCode === 229`, or a compositionstart/compositionend flag that also swallows the first keydown after `compositionend`, for Safari).
- **Shift+Enter** inserts a newline. **⌘/Ctrl+Enter** also sends.
- **Esc:** stops a streaming answer. Otherwise it removes the quote if there is one. Otherwise it returns focus to the PDF.

**Rules**
- **Draft:** saved per paper in `localStorage` under `pm:draft:{paperId}` (debounced 300ms) and restored on open. Cleared on send.
- **Length:** the API limit is 4,000 characters. From 3,600 a counter appears at the bottom-left, Sofia 12 tabular `--ink-2`: "3,712 / 4,000". Over the limit, pressing Ask shows inline under the composer: "That’s over the 4,000-character limit. Shorten it or split it into two questions." The button stays enabled.
- **Offline:** sending works. The question shows "Will send when you’re back online." and sends on reconnect.
- **Paper not ready:** sending works. The question is queued (§5.10).
- **Autofocus:** desktop only, on open.

### 5.21 Toasts

Radix `Toast`. Toasts are only for events that happen away from where you are or that need an Undo. Never toast what's already shown inline (send failures, validation, knowledge updates, copy confirmations).

- **Position:** bottom-left of the window, offset 24px (16px on phones, and above the bottom sheet's peek). This keeps them clear of the composer at bottom-right.
- **Box:** width 356px (phones: viewport − 32), `--raised`, radius 12, `--shadow-md`, padding 12 16, Sofia 14/20 `--ink`. An optional action is a Text button at the right; close is an `XIcon` button for persistent toasts only.
- **Durations:** 4,000ms by default, 6,000ms with Undo. Persistent (until closed) when an action is required.
- **Stacking:** at most 3 visible, stacked upward with 8px gaps (no scale stacking). Timers pause on hover, on focus, and while the tab is hidden. Swipe left to dismiss on touch.
- **Motion:** enter opacity 0→1 plus `translateY(8px)`→0 over `--dur-base` `--ease-out`; exit opacity over `--dur-fast`. Reduced motion: opacity only.
- **ARIA:** `role="status"` with `aria-live="polite"`.

| Event | Copy | Action |
|---|---|---|
| Paper removed | "Removed Attention Is All You Need." | Undo (6 s) |
| Ingestion cancelled | "Stopped adding Denoising Diffusion Probabilistic Models." | — |
| Paper ready while you're in another paper | "Denoising Diffusion Probabilistic Models is ready." | Open |
| Ingestion failed while you're elsewhere | "Couldn’t finish preparing Denoising Diffusion Probabilistic Models." | Show (persistent) |
| What you know reset | "Reset what you know." | — |

### 5.22 What you know: list, row, corrections

Layout in §4.5.
- **Header:** "What you know" (Sofia 24/650). Subtitle, Sofia 14 `--ink-2`, max 32em: "PaperMind’s best guess from your questions. Change anything that’s wrong. Only you can see this."
- **Ready for next** (positive only; only when at least 1 exists), Sofia 14 `--ink`: "Ready for next: layer normalization, multi-head attention, positional encoding." Then Sofia 13 `--ink-2`: "These build on what you already know." At most 3 concepts, shown as link-styled buttons that open that concept's row (expanded, scrolled into view) or, for concepts not in the list, a concept detail sheet: name, description, "Builds on: …", and a Secondary "I know this".
- **Toolbar:**
  - **Filter:** 240px field, `MagnifyingGlassIcon`, placeholder "Find a concept…". Filters live as you type, since this is a local filter, not validation.
  - **Add:** Secondary button "Add something you know". It opens a combobox popover (320px), input "Search 359 concepts…" (`GET /concepts?q=`), and result rows showing name + area in `--ink-2`. Enter or click adds it. No match: "No concept called ‘xyz’ in the map yet."
  - **Count:** "42 concepts", Sofia 13 tabular `--ink-2`, right-aligned.
- **Area group:** `<h2>` Sofia 16/600 `--ink` with a tabular count in `--ink-2` at the right, then a `<ul>`. Areas are 48px apart. Empty areas are not shown.
- **Row** (`<li>`, padding 12, hover `--hover` radius 8):
  - Line 1: concept name in Sofia 15/550 `--ink`. The state word is right-aligned in Sofia 13/450 `--ink-2`: **Comfortable**, **Seen it** or **Not yet**. Then the row menu button (`DotsThreeIcon`, `aria-label="Change Softmax"`), shown on hover, focus-within and touch.
  - Line 2 (4px below): the latest evidence sentence in Literata 15/1.5 `--ink-2`, roman. If the evidence quotes the reader's own words, those words are Literata italic inside curly quotes. Then a space, then the locator link in Sofia 13/450 `--ink-2` (underline on hover): "Attention Is All You Need, §3.2 · Oct 3". It opens the paper at that section with the related answer scrolled into view.
  - When there's more than one piece of evidence, "Show 2 more" (Text button) expands the older ones in the same style.
- **State mapping** (no percentages, ever):

| State | Comes from |
|---|---|
| Comfortable | `source = self_reported`, or `onboarding`, or `inferred` with confidence ≥ 0.7 and evidence that the reader explained or applied it |
| Seen it | `inferred` with confidence < 0.7 (asked about or read) |
| Not yet | Came up as a prerequisite in a `gaps` event and isn't known (Appendix A: `GET /users/{id}/knowledge?include=not_yet`) |

- **Evidence verbs are tied to the signal type:**
  - Onboarding: "You said you’re comfortable with Linear algebra · Oct 2"
  - Self-reported: "You said you know this · Oct 3"
  - Inferred from a question: "You asked about it in Attention Is All You Need, §3.2 · Oct 3"
  - Inferred from an explanation the reader **wrote**: "You explained why it exponentiates before normalizing, in Attention Is All You Need, §3.2 · Oct 3"
  - Came up as a gap: "Came up while reading BERT, §3.1 · Oct 6"

  A question is never upgraded to "You explained".
- **Row menu:** "I know this" (state → Comfortable, source "You said you know this"), "I’m not sure about this" (→ Seen it), divider, "Forget this" (removes it).
  - All three are optimistic, with an inline line replacing line 2 for 6 s: "Marked as known. Undo" / "Moved to Seen it. Undo" / "Forgot softmax. Undo".
  - On failure the row reverts and line 2 reads "Couldn’t save that. Try again".
  - The Library and any open Reader reflect the change immediately (shared query cache).
- **Empty state:**

  ```
  This fills in as you read.                                   Sofia 16/600
  Mark the areas you’re already comfortable with:              Sofia 14 --ink-2
  [ ] Linear algebra   [ ] Calculus & optimization basics  …   12 checkboxes, 2 columns ≥ 640px
  [Save]                                                       Primary
  ```
- **Loading:** 6 skeleton rows (15px bar at 30%, 15px bar at 70%), static, after 200ms.
- **Error:** "Couldn’t load what you know. The server didn’t respond." + "Try again".
- **A11y:** real headings and lists. The state is text. Menu items announce their result through the polite live region.

### 5.23 Dialogs

Radix `Dialog`.
- **Box:** width `min(440px, 100vw − 32px)` (the shortcuts sheet is 560px), `--raised`, radius 12, `--shadow-lg`, padding 24. Centred (centring is right for a one-off modal moment). Scrim `--scrim`.
- **Title** Sofia 16/600 `--ink`; body Sofia 14/20 `--ink`, 8px below. Actions are right-aligned, 24px below the body, 8px apart, Cancel (Secondary) left of the confirm button.
- **Focus:** trapped inside. Initial focus goes to the least destructive control. Focus returns to the trigger on close. Esc closes.
- **Motion:** enter opacity 0→1 and `scale(.97)`→1 over `--dur-base` `--ease-out`. Exit opacity only over `--dur-fast`. Keyboard-opened dialogs (`?`) appear with no animation.

| Dialog | Title | Body | Actions |
|---|---|---|---|
| Reset what you know | "Reset what you know?" | "This forgets all 42 concepts. Your papers, highlights and pins stay." | Cancel · **Reset** (`--danger` Secondary) |
| Keyboard shortcuts | "Keyboard shortcuts" | The map from §8.2 as a two-column table (`kbd` + description), grouped Reading / Assistant / Everywhere | Close |
| Report a problem | "What went wrong with this answer?" | Radio list: "Too basic", "Too dense", "Wrong about the paper", "Something else", plus a textarea "Anything else? (optional)" | Cancel · **Send report** |

### 5.24 Tooltips

Radix `Tooltip`.
- **Timing:** open delay `--delay-tooltip` (600ms). Within `--skip-tooltip` (300ms) of another tooltip closing, the next opens instantly with no animation. Close delay 0. Keyboard focus opens immediately.
- **Box:** `--tooltip-bg`, text `--tooltip-ink` Sofia 13/450, padding 4 8, radius 8, max-width 240px, offset 8px. No shadow in light; `--shadow-sm` in dark.
- **Content:** label, then 8px, then the shortcut as `kbd`. Inside tooltips, `kbd` uses `color: inherit; opacity: .8; box-shadow: inset 0 0 0 1px rgb(255 255 255 / .24)` (light) or `rgb(255 255 255 / .16)` (dark).
- **Motion:** opacity 0→1 and `scale(.97)`→1 over `--dur-fast` `--ease-out` from `--radix-tooltip-content-transform-origin`. Exit instant.
- **Rules:** never on disabled controls, never containing interactive content, never on touch. Prefer visible help text; a tooltip is for icon-only buttons, truncated titles, and the Depth descriptions.

### 5.25 Error states

Errors render **where the thing would have been**, keep everything the reader typed or received, state the cause and the next step, and use one icon. `--danger` appears only when something was lost or failed; recoverable states use a neutral icon.

**Assistant errors** (SSE `error` events, rendered in the turn under whatever text arrived; the question is always kept):

| `error.code` | Icon | Copy | Action |
|---|---|---|---|
| `rate_limited` | `ClockIcon` `--ink-2` | "Lots of questions right now. Trying again in 12 s…" (tabular countdown, one automatic retry) | "Try now" |
| `overloaded` | `ClockIcon` | "The model is busy at the moment. Your question is saved." | "Try again" |
| `api_error`, `internal` | `WarningCircleIcon` `--danger` | "This answer didn’t finish. Nothing you wrote was lost." | "Try again" |
| `tool_failed` | `WarningCircleIcon` `--danger` | "Couldn’t search the paper just now, so there’s no answer rather than a guess." | "Try again" |
| `paper_not_ready` | `ClockIcon` | "Still indexing this paper, about 30 s. Your question will send when it’s ready." | (automatic) "Cancel" |
| `refusal` | none | The backend's one-sentence reason, then "Try asking about what the paper itself says." | "Rephrase" (puts the question back in the composer) |
| `no_api_key` | `WarningIcon` `--warn` | "The server has no Anthropic API key. Set ANTHROPIC_API_KEY on the server and restart it." | "Copy command" (copies `export ANTHROPIC_API_KEY=…`) |
| `auth_failed` | `WarningIcon` `--warn` | "The server’s Anthropic API key was rejected. Check the key, then restart the server." | — |
| `session_not_found` | none | "This conversation expired after a day without activity, so a new one was started. Your question was carried over." | (automatic resend) |
| Stream dropped (client: no `done` before the connection closed) | `WarningCircleIcon` `--danger` | "The connection dropped partway through. The text so far is kept." | "Try again" |
| Stopped by the reader | none | "This answer stopped partway. Your question and the text so far are saved." | "Try again" |
| Request failed before streaming (HTTP 4xx/5xx) | `WarningCircleIcon` | "Not sent." under the question; the text goes back into the composer | "Try again" |

**App-level states**

| State | Where | Copy | Action |
|---|---|---|---|
| Offline | A 32px bar at the top of the panel (Reader) or under the header (other pages), `--surface` with a 1px `--line-subtle` bottom rule, `WifiSlashIcon` | Reader: "You’re offline. Questions will send when you’re back." Library: "You’re offline. Your papers are still here; adding new ones needs a connection." | — |
| Back online | The same bar, for 3 s, `CheckIcon` `--success` | "Back online." | — |
| Server unreachable (health check fails) | Library: in place of the list. Reader: the panel | "Can’t reach the PaperMind server. If you’re running it yourself, start it with `make dev`." | "Try again" |
| No API key (health `anthropic_key` false) | A persistent bar under the header on every page, `WarningIcon` `--warn` | "Answers are off: the server has no Anthropic API key. Set ANTHROPIC_API_KEY and restart it." | "Copy command" |
| Paper not found (404) | Reader body | "This paper isn’t in your papers anymore." | "Back to your papers" |
| PDF failed to load | PDF column, top-aligned 48px from the top | "Couldn’t load the PDF. The server didn’t send it." | "Try again" |

**Add-paper and ingestion errors** (in the hint line under the field, or in the row):

| Case | Copy | Action |
|---|---|---|
| Not an ID or URL | "That doesn’t look like an arXiv ID. Try one like 2401.12345, or drop a PDF." | — |
| arXiv has no such paper | "arXiv has no paper 2401.99999. Check the ID, or upload the PDF instead." | — |
| arXiv unreachable | "Couldn’t reach arXiv. Check your connection and try again, or upload the PDF instead." | "Try again" |
| Withdrawn | `WarningIcon` `--warn` "arXiv lists 2101.00001 as withdrawn. Version v2 is still available." | "Add v2" |
| A newer version exists | "You pasted v3. The latest version is v5." | "Add v5" · "Add v3" |
| Already in your papers | "Attention Is All You Need is already in your papers." | "Open it" |
| Not a PDF | `WarningCircleIcon` "That file isn’t a PDF. PaperMind reads PDFs and arXiv papers." | — |
| Too large | `WarningCircleIcon` "That PDF is 72&nbsp;MB. The limit is 50&nbsp;MB." | — |
| Ingestion failed (row) | `WarningCircleIcon` `--danger` "Couldn’t finish preparing this paper: {reason}." Reasons: "the download stopped partway", "the PDF couldn’t be opened", "indexing failed" | "Try again" · "Remove" |
| Scanned PDF (no text layer) | Row: `WarningIcon` `--warn` "Ready to read, but it’s a scan, so it can’t be searched." | "Open" |
| No sections found | Row: none (it's ready). Reader outline: "No section headings found, so this lists pages." | — |

### 5.26 Menus and popovers

Radix `DropdownMenu` / `Popover`.
- **Box:** `--raised`, radius 12, padding 4, `--shadow-md`, min-width 200px, offset 4px from the trigger, `transform-origin: var(--radix-dropdown-menu-content-transform-origin)`.
- **Items:** 32 tall (44 on coarse), radius `calc(12px - 4px)`, padding 0 8, Sofia 14 `--ink`. Optional 16px icon at the left in `--ink-2`; shortcut `kbd` right-aligned.
- **Highlighted item** (pointer or roving focus): `--selected` background, no outline (roving focus is the highlight).
- **Checked items:** a 16px `CheckIcon` in a left column. Separators are 1px `--line-subtle` with 4px vertical margin. Destructive items use `--danger` text.
- **Motion:** pointer-opened menus fade and scale from .97 over `--dur-fast` `--ease-out`. Keyboard-opened menus appear instantly. Close is opacity over 100ms.
- **The Appearance popover** uses the same box at 280px wide, padding 12, with segmented controls (same spec as Depth, labels "System / Light / Dark", "Dimmed paper / Night page / Inverted", "Small / Medium / Large") and the one-line notes in Sofia 13 `--ink-2`.

### 5.27 Loading states

| Wait | Indicator |
|---|---|
| < 200ms | Nothing |
| 200ms – 10 s, a whole view | A skeleton that mirrors the final layout exactly (same heights and radii, no layout shift), static `--hover` fills, no shimmer and no pulse. Shown for at least 400ms once visible |
| 200ms – 10 s, one control | The button keeps its label, adds "…", and shows a 12px spinner (`border: 1.5px solid currentColor; border-right-color: transparent`, rotating 600ms linear) |
| > 10 s | Determinate, staged progress with Cancel (ingestion, §5.4) |
| An answer before its first token | The trail line is the indicator (§5.11), from 300ms |

| Surface | Skeleton |
|---|---|
| Library | 3 rows: 19px bar at 60%, 13px bar at 30%, 12px strip at 160px; rows 96px apart |
| PDF | Page placeholders at their real size from `meta.page_sizes` (white, radius 2), so scroll position is right before render. A page that hasn't rendered after 1 s shows "Rendering p. 7…" in Sofia 13 `--ink-2` at its centre |
| Panel thread restore | One skeleton per stored turn: a 15px bar at 50% (question), three 17px bars at 100/100/60% (answer) |
| What you know | 6 rows: 15px bar at 30%, 15px bar at 70% |
| Suggested questions | 3 bars, 14px, at 80/65/72% |

---

## 6. Motion

**Principle:** motion answers something the reader did, and shows where a thing came from or went. Nothing moves on page load, theme change, keyboard paging or any keyboard-triggered action. Rules:
- Animate only `transform`, `opacity` and `clip-path`. The one exception is `background-color` on hover-out.
- Never `transition: all`; list the properties.
- UI motion stays at or under 280ms; only the 1.2 s settle is longer, and it's an opacity fade.
- Exits are faster than entries.
- No `ease-in` on UI, no bounce or overshoot, no stagger.
- Use CSS transitions (which can be interrupted and retargeted), not keyframes, for anything that can be retriggered.

**By frequency**

| Frequency | Rule | Examples |
|---|---|---|
| Keyboard or 100+/day | No animation | Paging, `[` `]`, depth via arrows, `/`, `E`, shortcuts sheet via `?`, selection popover |
| Tens per day | Opacity only, ≤ 150ms | Hover backgrounds (out only), tooltips, hover cards, word reveal, back pill |
| Occasional | Standard enter/exit | Menus, dialogs, toasts, panel collapse by click, drawers, sheets |
| Rare | The orchestrated moment | Adding a paper |

### 6.1 The single orchestrated moment: a paper arrives in the Library

When the reader adds a paper, its row fills in **as real data arrives**, with no fake delay. If arXiv answers at once, everything appears at once.

| t | What happens | Motion |
|---|---|---|
| 0 | The row is inserted at the top. The list makes room instantly; rows never slide | Row opacity 0→1, `--dur-meta` (120ms) `--ease-out` |
| 0 | The placeholder title "arXiv 1706.03762" (or the filename) shows in Literata 19/560 `--ink-2` | — |
| metadata arrives | The real title replaces the placeholder and turns `--ink` | Crossfade: old opacity 1→0 and new 0→1 over 120ms, with `filter: blur(2px)` on the outgoing text during the fade |
| | The authors and year line appears | Opacity 0→1, 120ms |
| each stage | The finished step gains its `CheckIcon`; the next step's text appears | Opacity 0→1, 120ms. Counts ("Indexing 140 of 212") change in place with tabular figures and no animation |
| done | `CheckIcon` (`--success`) + "Ready" for 4 s, then it crossfades to "Not opened" | 120ms crossfades |

Under reduced motion the content swaps with no fades.

### 6.2 The signature motion: the bracket draws

- **Draw:** `clip-path: inset(0 0 100% 0)` → `inset(0 0 0 0)` on the segment's `<g>`, over `--dur-draw` (220ms) `--ease-out`, revealing top to bottom. The numeral fades in (opacity 0→1, 100ms) at the start of the draw. A split bracket draws its segments one after another, each over 220ms, in reading order.
- **When it draws:**
  - When a chip first appears in a streaming answer: once per citation, and only visible if that page is rendered.
  - On chip hover after `--delay-bracket` (150ms), for a citation from an inactive answer.
  - On keyboard focus of a chip, immediately.
- **Undraw:** for an inactive answer's bracket when the pointer leaves its chip, and for the previous answer's unpinned brackets when a new question is sent. Opacity 1→0 over `--dur-undraw` (150ms) `ease`.
- **Reduced motion:** brackets appear and disappear whole, with no clip and no fade.

### 6.3 The jump

1. **Scroll:** within 2 viewport heights, a smooth scroll of ≤ 300ms with `--ease-in-out` (implemented with `scrollTo` plus a rAF tween, so the duration is controlled). Farther than that, instant. Instant under reduced motion.
2. **Re-ink:** a second path with the same geometry and a uniform 2.25px width sits over the 1.75px bracket and fades opacity 1→0 over `--dur-settle` (1200ms) `--ease-out`.
3. **Exact-span underline:** fades opacity 0.9→0 over 1200ms `--ease-out` at the same time. Hovering or focusing the chip or bracket during or after this holds it at 0.9.
4. **Back pill:** opacity 0→1 over 150ms; it leaves after 10 s with opacity over 150ms.

The page is never filled. Under reduced motion the scroll is instant and the two fades still run, because they are opacity only.

### 6.4 Per-interaction transitions

| Interaction | Properties | Duration · easing | Reduced motion |
|---|---|---|---|
| Button press | `transform: scale(.97)` (chips .98) | 120ms `--ease-out` | none |
| Hover background | `background-color` | in 0ms, out 150ms `ease` | same |
| Tooltip | opacity, `scale(.97→1)` from the trigger | 150ms `--ease-out`; skip-delay peers instant; exit instant | opacity only |
| Passage and paper cards | opacity, `scale(.97→1)` from the chip | 150ms in, 100ms out | opacity only |
| Menu or popover (pointer) | opacity, `scale(.97→1)` from the trigger | 150ms in `--ease-out`, 100ms out | opacity only |
| Menu or popover (keyboard) | — | instant | — |
| Selection popover | — | instant | — |
| Dialog | opacity, `scale(.97→1)` | 200ms in `--ease-out`, 150ms out (opacity) | opacity only |
| Toast | opacity, `translateY(8px→0)` | 200ms in `--ease-out`, 150ms out | opacity only |
| Panel collapse/expand (pointer) | panel `transform: translateX(100% ↔ 0)` | 280ms `--ease-drawer`. On collapse the PDF re-lays out once at the end; on expand, once at the start | instant |
| Panel or outline toggle by key (`]` `[`) | — | instant | — |
| Outline drawer (pointer) | `transform: translateX(-100% ↔ 0)` | 280ms in, 200ms out, `--ease-drawer` | instant |
| Tablet sheet tuck (chip click) | `transform: translateX(calc(100% - 48px))` | 280ms `--ease-drawer` | instant |
| Phone bottom sheet | follows the finger; release snaps to the nearest detent (velocity > 0.4 px/ms advances one detent) | 300ms `--ease-drawer` | instant snap |
| Depth thumb | `transform: translateX()` | pointer: 200ms `--ease-in-out`; keyboard: instant | instant |
| Switch thumb | `transform` | 200ms `--ease-out`; keyboard: instant | instant |
| Streaming words | opacity 0→1 | 150ms `linear` | none (append) |
| Trail expand | — | instant (caret rotates instantly) | — |
| Jump-to-latest and back pills | opacity | 150ms | same |
| Top bar hide (phone) | `transform: translateY(-100%)` | 200ms `--ease-out` | instant |
| Theme change | all transitions disabled for one frame (`.theme-switching * { transition: none !important }`) | — | — |
| Spinner | `transform: rotate()` | 600ms `linear` infinite | static 3/4 ring |

### 6.5 Reduced motion

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; }
  .popover, .menu, .tooltip, .toast, .dialog, .card { transform: none !important; transition-property: opacity !important; }
  .panel, .drawer, .sheet, .depth-thumb, .switch-thumb { transition: none !important; }
  .bracket-seg { clip-path: none !important; transition: none !important; }
  .word-in { animation: none !important; opacity: 1 !important; }
  .spinner { animation: none !important; }
}
```

In JS, read `matchMedia('(prefers-reduced-motion: reduce)')` once at startup and subscribe to its `change` event. It decides the scroll mode for jumps and outline navigation, and whether streamed words fade.

---

## 7. Voice and microcopy

### 7.1 Principles

- **A friend who read it first and left pencil notes.** Brief, specific and calm. Never performative, never cheerful.
- **Sentence case everywhere**, contractions welcome, **no exclamation marks**.
- **The assistant never says "I"**, never praises the question, never apologises theatrically, and never labels the reader ("as a beginner"). It describes the answer's depth, not the person.
- **Talk about the paper, not the machinery.** Say "Searching the paper", not "Calling tool", "RAG", "Thinking…" or "Processing…".
- **Say what happened and what to do.** Every error has a cause and a next step. Never "Oops", "Error" or "Something went wrong".
- **Name actions with verbs and objects:** "Add paper", "Explain softmax first", "Remove from your papers". Never "Submit", "OK" or "Continue" when something more specific fits ("Continue" is allowed only in onboarding step 1).
- **One name per idea.** The reader's knowledge is always "what you know". Their marks are "highlights". The assistant's marks are "brackets" (in the legend only; elsewhere they're shown, not named). A paper is "a paper", never "a document" or "a file".
- **The field's conventions, explained the first time:** § (spelled "Section" in cards and accessible names), "p. 4", "pp. 4–5", "et al.", author–year.
- **Typography:** curly quotes ‘ ’ “ ”, `…` (never "..."), en dash for ranges, `&nbsp;` between numbers and units and inside "p.&nbsp;4", "Fig.&nbsp;2", "§&nbsp;3.2" when written out, and "3.1&nbsp;s".
- **Ban list** (grep in CI): AI-powered, magic, smart, unlock, supercharge, elevate, seamless, empower, harness, delve, world-class, "not just", "How can I help", Oops, "Something went wrong", "!" in UI strings.

### 7.2 Buttons and labels

| Where | Label |
|---|---|
| Add field button | Add paper (busy: Adding…; looking up: Looking up…) |
| Add field placeholder | Paste an arXiv link or ID, or drop a PDF on this page… |
| Add field hint | PDF up to 50&nbsp;MB. arXiv IDs look like 1706.03762. |
| Touch file button | Choose PDF |
| Ingestion | Cancel · Open now |
| Library row menu | Open · Copy arXiv link · Remove from your papers |
| Reader back link | Your papers |
| Top bar tooltips | Show contents · Hide contents · Go to page · Zoom out · Zoom in · Find in paper · Appearance · Show assistant · Hide assistant · More |
| Zoom menu | Fit width · Fit page · Actual size · 50% … 200% |
| More menu | New conversation · Copy conversation · Keyboard shortcuts · Paper on arXiv · Remove from your papers |
| Find | Find in paper… · 3 of 12 · No matches · Previous match · Next match · Close find |
| Appearance | Theme: System / Light / Dark · Page in dark mode: Dimmed paper / Night page / Inverted · Answer text size: Small / Medium / Large |
| Contents | Contents · Pages · Your marks · 3 highlights · 1 pinned answer |
| Highlight menu | Change colour · Ask about this · Remove highlight |
| Selection popover | Explain · Ask about this · Highlight yellow / green / blue / orange · More · Copy · Copy with citation |
| Depth | Depth · From basics · Standard · Technical |
| Composer | Ask · Stop · Remove quote |
| Answer actions | Copy (→ Copied) · Simpler · More technical · Pin to page / Unpin · More actions · Copy with citations · Report a problem… |
| Versions | Previous version · Version 2 of 2 · Simpler · Next version |
| Gap callout | Explain softmax first / Explain these first · I know softmax / I know these · Hide this note · Suggested order: |
| Suggestions | Hide suggestions |
| Pills | Back to p.&nbsp;11 · Jump to latest |
| Passage card | Show in paper |
| External paper card | Ask about this paper · Add to your papers · Semantic Scholar |
| Profile | Find a concept… · Add something you know · Search 359 concepts… · Show 2 more · I know this · I’m not sure about this · Forget this |
| Settings | Theme · Page in dark mode · Answer text size · Default depth · Label background paragraphs · Single-key shortcuts · See all shortcuts · Open What you know · Reset what you know… · Server · API key · Copy command |
| Onboarding | Before your first question · Where should answers start? · Continue · Skip for now · Which of these are you comfortable with? · Done · Skip |
| Recovery | Try again · Try now · Rephrase · Undo · Open it · Add v2 · Clear filter · Back to your papers |

### 7.3 Every empty, loading, progress and error string

**Library**
- First run title: "Start with a paper you’ve been meaning to read."
- Sample link: "Or open Attention Is All You Need to see how it works."
- Page title: "Your papers"
- Looking up: "Looking up 1706.03762 on arXiv…"
- Found: "Found: Vaswani et al. (2017). Attention Is All You Need."
- Drag over: "Drop to add ‘attention.pdf’" · "Drop to add 3 PDFs" · "Only PDFs can be added"
- Row status: "You stopped at §3.2" · "You stopped on p.&nbsp;4" · "Not opened" · "Getting it ready" · "Open now"
- Stages: "Waiting to start…" · "Downloading…" / "Downloaded" · "Reading 25 pages…" / "Read 25 pages" · "Finding sections…" / "Found 9 sections" · "Splitting into passages…" / "212 passages" · "Finding the ideas it builds on…" / "Builds on 23 ideas" · "Indexing 140 of 212 passages…" · "Indexing passages… 66%" · "Ready" · "3 of 6 steps done"
- Filter: "Filter by title or author…" · "No papers match ‘diffusion’."
- Load failed: "Couldn’t load your papers. The server didn’t respond."
- Offline: "You’re offline. Your papers are still here; adding new ones needs a connection."
- Validation and ingestion errors: §5.25 table.

**Reader**
- PDF failed: "Couldn’t load the PDF. The server didn’t send it."
- Rendering: "Rendering p.&nbsp;7…"
- No outline: "No section headings found, so this lists pages."
- Your marks empty: "Highlights and pinned answers show up here."
- Night page note: "Night page recolours the page. Figures turn two-tone."
- Inverted note: "For scanned papers. Colours in figures shift."
- Selection hint: "Tip: press E to explain a selection."
- Explain question text: "Explain this passage."
- Not found: "This paper isn’t in your papers anymore."

**Assistant panel**
- Empty: "Ask about anything on these pages, or select a sentence and press E. If an answer assumes too much, press Simpler."
- Readiness: "This paper builds on 7 ideas. 2 haven’t come up in your reading yet: softmax and layer normalization."
- Welcome back: "You stopped at §3.2 last time." · button "Recap up to §3.2" · sends "Recap what I’ve read so far, up to §3.2."
- Indexing: "Still indexing this paper. You can read now; questions will send when it’s ready, in about 30&nbsp;s." (fallback "…when it’s ready, shortly.")
- Queued question: "Waiting for the paper to finish indexing, about 30&nbsp;s. This will send when it’s ready."
- Scanned: "This PDF has no text layer, so PaperMind can’t search it. You can still read it here. If it’s on arXiv, add it by ID instead." · under first answer: "This PDF has no text layer, so citations show the page only."
- Depth help: "Applies from your next question."
- Depth tooltips: From basics "Defines terms as they come up and leans on intuition. Math only when it helps." · Standard "Assumes neural nets and backprop. Explains this paper’s new ideas and terms." · Technical "Skips the background. Precise, shows the math, compares with related work."
- Trail, live: "Finding the relevant parts of the paper…" · per-tool lines (§5.11) · "Reading from §3.2.1, §3.2.2 and §5.4…" · "Writing the answer…" · "… 9&nbsp;s" · "Semantic Scholar is slow right now."
- Trail, done: "Read 4 passages and checked 2 ideas, 3.1&nbsp;s" · "Answered from the conversation, 1.2&nbsp;s" · "(second try)" · "Couldn’t reach Semantic Scholar, so this answer uses the paper alone."
- Background: label "Background" · tooltip "General background, not from this paper, so there’s no bracket." · whole answer "General background. Nothing in this answer comes from the paper."
- Legend: "Numbered brackets on the page mark the exact lines an answer used. Paragraphs marked Background are general knowledge, not from this paper." · "Got it"
- Gap: "This builds on softmax, which hasn’t come up in your reading yet." (plural forms §5.16) · "Suggested order:" · after "I know": "Added softmax to what you know. Undo"
- Explain-first question: "Explain softmax before we go on."
- Knowledge line: "Added softmax to what you know. Undo" · "Took softmax off what you know. Undo" · "Undone."
- Recheck: "Softmax came up again. Still comfortable with it?" · "Yes" · "Refresh me" → "Give me a quick refresher on softmax."
- Nudge: "Make From basics your default for new questions?" · "Make Technical your default for new questions?" · "Yes" · "Not now"
- Pin: "Pinned to page 4. Its brackets stay on your copy." · "Pinned. Page 4 shows the 3 most recent pins; the rest are in Your marks."
- Copy: "Copied"
- Composer: "Ask about this paper…" · "Ask about this passage…" · "3,712 / 4,000" · "That’s over the 4,000-character limit. Shorten it or split it into two questions." · "Will send when you’re back online." · "Not sent."
- Stop and stream errors and all `error.code` copy: §5.25 tables.
- Offline bar: "You’re offline. Questions will send when you’re back." · "Back online."
- Live-region announcements: "Answer ready, 2 citations." · "Showing section 3.2.1, page 4" · "Copied" · "Depth set to Technical. Applies from your next question."

**Onboarding:** "Before your first question" · "Where should answers start?" · the three options and descriptions (§4.6) · "This only sets where answers start. You can change it for any question." · "Which of these are you comfortable with?" · "Answers won’t stop to explain these unless you ask. Optional."

**What you know**
- Subtitle: "PaperMind’s best guess from your questions. Change anything that’s wrong. Only you can see this."
- "Ready for next: …" · "These build on what you already know."
- States: Comfortable · Seen it · Not yet
- Evidence verbs: §5.22
- Results: "Marked as known. Undo" · "Moved to Seen it. Undo" · "Forgot softmax. Undo" · "Couldn’t save that. Try again"
- Empty: "This fills in as you read." · "Mark the areas you’re already comfortable with:" · "Save"
- Errors: "No concept called ‘xyz’ in the map yet." · "Couldn’t load what you know. The server didn’t respond."
- Reset dialog: "Reset what you know?" · "This forgets all 42 concepts. Your papers, highlights and pins stay." · "Reset" · toast "Reset what you know."

**Settings and app level**
- "Connected · version 0.4.0" · API key "Set" / "Missing"
- "Can’t reach the PaperMind server. If you’re running it yourself, start it with `make dev`."
- "Answers are off: the server has no Anthropic API key. Set ANTHROPIC_API_KEY and restart it."
- Single-key shortcuts help: "E, A, H, 1–4, [, ], / and ? work outside text fields."
- Label background help: "Marks uncited paragraphs “Background”."

---

## 8. Accessibility

### 8.1 Checklist

**Structure**
- [ ] `<html lang="en">`. One `<h1>` per page: the page title, or in the Reader a visually hidden `<h1>` with the paper title.
- [ ] Landmarks:
  - Library and other pages: `<header>`, `<nav aria-label="Main">`, `<main>`.
  - Reader: `<header>` (top bar), `<nav aria-label="Contents">`, `<main aria-label="Paper">` (PDF), `<aside aria-label="Assistant">`.
- [ ] Every assistant turn is an `<article>` labelled by its question. The thread is a `<section aria-label="Answers">`. Don't use `role="log"`: it implies `aria-live` and would read streaming tokens aloud. Announcements go through the dedicated live region.
- [ ] Each PDF page is a `role="region"` with `aria-label="Page 4 of 15"`. The pdf.js text layer is the accessible text and is never `aria-hidden`.
- [ ] Navigation uses `<a>`/`<Link>`. Actions use `<button>`. No `div onClick`.
- [ ] Paper titles, math and code carry `translate="no"`.

**Keyboard and focus**
- [ ] Everything is reachable and operable by keyboard. Tab order follows the visual order: top bar → contents → PDF (pages, then each page's bracket buttons) → panel (header, thread, composer).
- [ ] **F6 / Shift+F6** cycle the Reader regions: top bar, contents, PDF, assistant thread, composer. On other pages: header, main.
- [ ] Skip link "Skip to the assistant" (Reader) / "Skip to content" (others) is the first focusable element.
- [ ] `:focus-visible` ring everywhere: 2px `--focus`, 2px offset. Over the PDF it's two-tone (inner `--focus-inner`, outer 4px `--focus-outer`), 16.0:1 light and 13.2:1 dark. The ring changes contrast by ≥ 3:1 (WCAG 2.4.13).
- [ ] Focus is never hidden under the sticky top bar or panel header: `scroll-padding-top: 48px` on the PDF scroller, `52px` on the thread.
- [ ] **Chip ↔ bracket round trip:** focusing a chip draws its bracket and opens its card with no delay. Enter on the chip jumps and moves focus to the bracket button. Enter or Esc on the bracket returns focus to the chip.
- [ ] Hover-revealed controls (answer actions, row menus) also appear on `:focus-within`. Every hover delay has an immediate focus equivalent.
- [ ] Dialogs trap focus and return it to the trigger. Menus use roving focus with arrow keys, Home/End and type-ahead. Esc closes them and returns focus.
- [ ] The resize separator works with arrow keys (§4.4).
- [ ] Single-letter shortcuts never fire inside inputs, textareas or contenteditable, and can be turned off in Settings (WCAG 2.1.4).
- [ ] The composer respects IME composition (§5.20).

**Screen readers and announcements**
- [ ] One visually hidden `aria-live="polite"` region per page carries every announcement: answer ready, jump target, copied, knowledge changes, depth change, ingestion stage changes, offline and online.
- [ ] Streaming: the turn has `aria-busy="true"` until done. Tokens are not announced; completion is announced once.
- [ ] Brackets: `<button aria-label="Citation 2, section 3.2.1, page 4. Show the answer">`. Chips: "Citation 1: section 3.2.1, page 4". "§" is never read as a bare symbol.
- [ ] Icon-only buttons have `aria-label`s; decorative icons are `aria-hidden`.
- [ ] Graphics have text equivalents:
  - Page strip: `role="img"` with an `aria-label`.
  - Learning path: an ordered list with "already in what you know" / "not yet" in text.
  - Trail: a disclosure button with `aria-expanded`.
  - Depth: a radio-style toggle group with an accessible name.
- [ ] The profile is always a list with headings and text states. There is no graph-only view.

**Colour, contrast and form**
- [ ] Every text and background pair is in §2.7 and passes WCAG AA. Body text is ≥ 7:1. 12px numerals are ≥ 4.5:1 on every background they sit on, including the dimmed page (5.43:1).
- [ ] Control borders and focus rings are ≥ 3:1 against adjacent colours.
- [ ] No meaning by colour alone: highlights are fills, assistant marks are strokes with numerals, search is an outline, selection is a neutral tint, errors have icon + text, and depth has thumb + weight + label.
- [ ] `forced-colors: active`: highlight fills become a `Highlight` outline, brackets use `CanvasText`, the focus ring uses `Highlight`, chips get a 1px `ButtonText` border.
- [ ] Both themes are checked with a contrast tool in CI (axe in Playwright on Library, Reader with an answer, What you know and Settings, in light and dark).

**Motion, zoom and touch**
- [ ] `prefers-reduced-motion` honoured (§6.5).
- [ ] The UI works at 200% browser zoom and at 320 CSS px wide without horizontal scrolling outside the PDF.
- [ ] Targets ≥ 24×24 on fine pointers and ≥ 44×44 on coarse (chips and brackets included, via hit-area pseudo-elements).
- [ ] Inputs are ≥ 16px text on coarse pointers (no iOS zoom).
- [ ] Nothing blinks or flashes. The 1.2 s settle is a single fade.

**Manual test script before each release:** VoiceOver + Safari and NVDA + Firefox. Add a paper by ID, open it, select a sentence and press E, follow a citation to the page and back, mark a concept as known, undo it, and change depth. All of it by keyboard only.

### 8.2 Keyboard shortcut map

Letter keys work only when focus isn't in a text field and single-key shortcuts are on. "⌘" means Ctrl on Windows and Linux. Shortcuts appear in tooltips (label, then `kbd`), right-aligned in menus, and in the `?` sheet.

**Everywhere**

| Keys | Action |
|---|---|
| `?` | Keyboard shortcuts |
| `Esc` | Close the topmost popover, menu, card or dialog; otherwise stop a streaming answer |
| `F6` / `Shift+F6` | Next / previous region |

**Reading**

| Keys | Action |
|---|---|
| `←` `→` · `PageUp` `PageDown` | Previous / next page (focus in the PDF or on the page body) |
| `Home` `End` | First / last page |
| `⌘ F` | Find in paper (pdf.js find, not the browser's) |
| `Enter` / `Shift+Enter` in find | Next / previous match |
| `⌘ +` · `⌘ −` · `⌘ 0` | Zoom in · zoom out · Fit width |
| `[` | Show or hide contents |
| `]` | Show or hide the assistant |
| `Alt+←` | Back to where you were before a citation jump |
| `E` | Explain the selection |
| `A` | Ask about the selection (attach it to the composer) |
| `H` | Highlight the selection with the last colour |
| `1` `2` `3` `4` | Highlight yellow · green · blue · orange (with a selection) |
| `/` | Focus the composer |

**Assistant**

| Keys | Action |
|---|---|
| `Enter` | Ask (respects IME composition) |
| `Shift+Enter` | New line |
| `⌘ Enter` | Ask |
| `Esc` in the composer | Stop the answer → remove the quote → return to the page |
| `Alt+↑` / `Alt+↓` | Focus the previous / next answer |
| `←` `→` `Home` `End` on Depth | Change depth (instant) |
| `Enter` on a chip | Jump to the passage and focus its bracket |
| `Enter` / `Esc` on a bracket | Back to the answer's chip |

---

## 9. Anti-slop review checklist (paste into every front-end PR)

**Automated** (CI fails on any hit in `frontend/src`):
```bash
rg -n "indigo|violet|purple|from-.*to-|bg-gradient|linear-gradient|radial-gradient|bg-clip-text|text-transparent" frontend/src
rg -n "transition:\s*all|transition-property:\s*all|ease-in[^-]|cubic-bezier\([^)]*1\.[0-9]" frontend/src   # no all, no ease-in, no overshoot
rg -n "#[0-9A-Fa-f]{3,8}\b" frontend/src --glob '!**/tokens.css'            # raw hex outside tokens
rg -n "\b(1[13]|2[0237]|1[579])px\b" frontend/src --glob '!**/tokens.css'  # off-scale spacing (review hits by hand)
rg -n "'Inter|\"Inter|Geist|Space Grotesk|Instrument (Serif|Sans)|Fraunces|\bLora\b" frontend/src
rg -n "lucide|heroicons|tabler|react-icons|Sparkle" frontend/src
rg -in "ai-powered|magic|supercharge|unlock|elevate|seamless|empower|harness|delve|world-class|not just|how can i help|oops|something went wrong" frontend/src
rg -n '"[^"]*\.\.\.[^"]*"' frontend/src                                     # "..." in strings: use …
rg -n "![\"'\`]" frontend/src/**/*.tsx                                       # exclamation marks ending UI strings
rg -n "[\x{1F300}-\x{1FAFF}\x{2600}-\x{27BF}]" frontend/src                  # emoji
rg -n "backdrop-filter|backdrop-blur" frontend/src
rg -n "border-left:\s*[2-9]px|border-top:\s*[2-9]px" frontend/src            # stripe callouts
```

**By eye** (a reviewer ticks each one):
- [ ] **Mulberry budget:** `--pencil` appears only in the places listed in §2.6. Not on buttons, focus, selection, links, toggles, headers or the logo.
- [ ] **Fill/stroke rule:** nothing the assistant puts on the page is a fill. Reader highlights are fills; the assistant's marks are strokes.
- [ ] **Bracket meaning:** a bracket appears only beside verbatim paper words (page or panel quote).
- [ ] No cards in cards, no boxed messages, no chat bubbles, no avatars, no greeting.
- [ ] No card grid, stat row, progress ring, streak, percentage of knowledge or "Mastered".
- [ ] In-flow surfaces are separated by tone steps. Floating layers use ring + two-layer shadow from §2.2. Nothing has both a hairline and a wide shadow.
- [ ] Radii come from 2/4/8/12 or a `calc(parent − padding)`. No pills except swatches and path dots.
- [ ] Literata appears only in answers, verbatim quotes, paper titles, profile evidence and the settings preview. No serif headings, no italic display.
- [ ] Sentence case. No eyebrows, no ALL CAPS, no tracked labels, no "01/02".
- [ ] Tabular figures on page numbers, counts, timers, percentages and chips. `&nbsp;` in "p.&nbsp;4", "3.1&nbsp;s", "50&nbsp;MB". Curly quotes, `…`, en-dash ranges.
- [ ] Icons: Phosphor only, from the §3 inventory, 16px regular, `fill` for selected states; every icon-only button has an `aria-label` and a tooltip.
- [ ] Motion: nothing on load, theme change or keyboard actions. Durations from tokens. Only transform, opacity, clip-path (and hover-out background-color). Reduced-motion path tested.
- [ ] No shimmer, pulse, blinking caret or end-of-stream dot. The trail is specific (§5.11) and never says "Thinking" or "Processing".
- [ ] States designed and screenshotted: 0, 1 and 300 papers; a 200-character title; a failed ingestion; an indexing paper opened early; a scanned PDF; offline; a stopped answer; a dropped stream; each `error.code`.
- [ ] Errors state the cause and next step, keep the reader's text, sit where the content would have been, and use `--danger` only when something was lost.
- [ ] Copy passes §7.1: no "I" from the assistant, no reader labels, no exclamation marks, specific verbs on buttons.
- [ ] Contrast: any new text/background pair is added to §2.7 with its measured ratio.
- [ ] Two-column paper check: open a two-column paper, cite passages in both columns, across a column break and across a page break. Brackets land in the nearest margin with matching numerals, at Fit width and at 200% zoom.
- [ ] Dark mode check: page dimmed (canvas only), brackets at full strength, highlights readable, no hue-tinted near-black.

---

## Appendix A. Backend contract additions this design depends on

Each item names the UI that needs it and what the UI does until it exists.

| # | Change | Needed by | Until it exists |
|---|---|---|---|
| A1 | Rename the trail strings in `papermind/agent/registry.py` to match §5.11: `check_prerequisites` label → "Checking what ‘{concept}’ builds on"; summary "You’re missing N ideas: …" → "‘{X}’ builds on N ideas that haven’t come up in your reading yet: …"; "You have the background for X" → "You’ve met everything ‘X’ builds on"; "Couldn’t find ‘q’ in the concept graph" → "‘q’ isn’t in the concept map yet"; `get_learning_path` label → "Working out an order for learning ‘X’", summary → "Suggested order: a, b, c"; "No matching passages found" → "No passages matched ‘q’"; "No match on Semantic Scholar for" → "Nothing on Semantic Scholar for"; failures → "Couldn’t search the paper", "Couldn’t check what this builds on", "Couldn’t work out a learning path" | Trail (§5.11) | The frontend maps by `tool` and shows its own strings |
| A2 | `PaperSummary.stage_detail: {done: int, total: int} \| None` (pages read, passages indexed) | Ingestion counts (§5.4) | Show "Indexing passages… 66%" |
| A3 | Optional ingestion stage `mapping` and `PaperMeta.builds_on: list[ConceptRef]` | "Finding the ideas it builds on" step; panel readiness line (§5.18) | Step and readiness line hidden |
| A4 | Add-paper errors carry `{code: "withdrawn" \| "newer_version" \| "not_found" \| "already_added" \| "arxiv_unreachable", latest_version?, paper_id?}` | Add-paper errors (§5.25) | Generic "Couldn’t add that paper" with "Try again" (still specific about arXiv where the HTTP status allows) |
| A5 | Writer cites Semantic Scholar papers as `[P1]` and `answer.done.external: list[{label, s2_id, title, authors, year, venue, tldr, arxiv_id, url}]` | External paper text and cards (§5.15) | External papers appear only in the trail |
| A6 | `SourceRef.cited_sentence: str \| None` (the sentence of the chunk the writer relied on) | Bold sentence in passage cards (§5.15) | No bold |
| A7 | `answer.done.followups: list[str]` (≤ 2, from this answer and its gaps) | Follow-ups (§5.17) | None shown |
| A8 | `knowledge.updated.ask: list[ConceptRef]`. The mastery pass never removes a `self_reported` concept; it asks instead | Recheck line (§5.19) | Self-reported concepts are never removed automatically |
| A9 | `GET /users/{id}/knowledge?include=not_yet` and `KnownConcept.evidence_items: list[{kind: "asked"\|"explained"\|"said"\|"onboarding"\|"came_up", paper_id, paper_title, section, at}]` | Profile states and evidence verbs (§5.22) | No "Not yet" rows; one evidence line from `evidence` + `source` |
| A10 | `DELETE /papers/{id}` cancels an in-flight ingestion | Cancel (§5.4) | Cancel hidden |
| A11 | `POST /feedback {message_id, reason, note}` | Report a problem (§5.23) | Menu item hidden |
| A12 | Highlights and pins persisted per user and paper (`/papers/{id}/marks`) | Your marks, page strip, pins | v1 stores them in `localStorage` keyed by paper |
| A13 | **Eval gates before shipping the signature promises:** (a) bracket accuracy: the cited chunk's boxes cover the sentence the answer relies on, ≥ 95% on the benchmark; (b) paper-derived paragraphs without a citation < 5%. A wrong bracket is worse than no bracket | Brackets (§5.8), Background labels (§5.13) | Background labels default off |

## Appendix B. Must-fix traceability

| Judge · item | Resolution | Where |
|---|---|---|
| Slop 1, rose only for assistant marks | Primary button, focus, selection, selected rows and drop target are ink or neutral. The full budget is in §2.6 | §2.2, §2.6, §5.1, §5.4 |
| Slop 2 / Friendly 7, "strokes only" on the page | pencil-flash deleted. The jump re-inks the bracket (2.25px → 1.75px over 1.2 s) and shows a 1.25px exact-span underline that fades. Chips are unfilled | §1.2, §5.8, §5.15, §6.3 |
| Slop 3 / Ergonomics 1–2 / Friendly 1, bracket placement on real PDFs | Column detection, nearest-margin rule, split segments with open ends, 3 lanes 6px apart then merge "1–4", 32px reserved lanes at every zoom, clamped 14px strip when clipped, figure boxes, no-text-layer fallback | §5.8 |
| Slop 4, one meaning for the bracket | Removed from profile evidence; only beside verbatim paper words | §1.2, §5.10, §5.22 |
| Slop 5, no end-of-stream dot | No glyph; trail line + Stop carry the state | §5.13 |
| Slop 6 / Ergonomics 5 / Friendly 2, error vs pencil | Pencil moved to mulberry #883564 (h 348); danger is rust #9A4312 (h 45), 57° apart; errors are ink text + icon | §2.2, §2.7, §5.25 |
| Slop 7, neutralise the hue tint | Neutrals at OKLCH chroma ≤ .005; dark ramp graphite #171719 / #1F1F21 / #28282A; dark pencil #DCA4C0 (C .075) | §2.2 |
| Slop 8 / Ergonomics 7 / Friendly 11, Depth fits 340px | Labels "From basics · Standard · Technical", measured 298.5px; native select below 360px; wireframes untruncated | §4.4, §5.9 |
| Slop 9 / Ergonomics 12 / Friendly 12, page strip | One form per state, pencil only for pins, bins above 40 pages, appendix gap, defined "read", text equivalent | §5.3 |
| Slop 10, missing states | SSE drop, Stop, queued question, withdrawn and newer version, no sections, pins in Your marks with a cap of 3 per page | §5.4, §5.7, §5.8, §5.10, §5.13, §5.25 |
| Slop 11, serif cliché guard | Literata limited to reading prose; page titles Sofia 24/650; no serif headings or empty states | §2.3, §9 |
| Slop 12 / Ergonomics 13, composited contrast | All pairs measured on composited colours, including the dimmed page and dimmed highlights (≥ 12:1); the overlay is never dimmed | §2.2, §2.7 |
| Ergonomics 3, wide screens | Page group right-aligned 48px from the panel at ≥ 1920 | §4.4 |
| Ergonomics 4 / Friendly 8, breakpoints | Docked down to 1000px (PDF ≥ 620); overlay 768–999 with a 48px numeral rail; bottom sheet below 768; outline a drawer at 1280 | §4.1, §4.4 |
| Ergonomics 6, exact-span recheck | Hover or focus a chip or bracket to hold the exact-span underline | §5.8 |
| Ergonomics 8 / Friendly 9, keyboard and focus | F6 regions, skip link, chip ↔ bracket focus round trip, labelled brackets, focus-within actions, no-delay focus | §5.8, §5.15, §8 |
| Ergonomics 9, streaming controls | Stop + Esc, stopped state, `aria-busy`, single completion announcement | §5.13, §5.20 |
| Ergonomics 10, hit areas | 24px fine, 44px coarse for chips and brackets | §5.8, §5.15 |
| Ergonomics 11, readability | Evidence in Literata 15 roman; nothing below 12px, running text ≥ 13px | §2.3, §5.22 |
| Ergonomics 14, chips identifiable without fill | Pencil numeral + ring; hover background; no fill dependency | §5.15 |
| Friendly 3, one name for knowledge | "what you know" everywhere; callout says "hasn’t come up in your reading yet" | §5.16, §7 |
| Friendly 4, callout rules | Max one per answer, none before onboarding, never re-raised after dismissal, "I know" needs no proof, with Undo | §5.16 |
| Friendly 5, visible learning path | Filled ink dots (known) / pencil rings (not yet) / caret (destination); text-only at Technical | §5.16 |
| Friendly 6, honest evidence verbs | Verbs tied to signal type; a question is never "You explained" | §5.22 |
| Friendly 10, visible background marking | "Background" run-in label plus a one-time legend, gated on eval | §5.13 |

Grafts taken:
- **From Apparatus:** visible provenance (Background label and legend), Copy with citations, external paper cards with the TLDR, partial-failure and stopped copy, hanging outline numerals, the cited sentence in bold, the parsed-reference confirmation, Library metadata filling in as the one orchestrated moment, multiple evidence locators, eval gates.
- **From Office Hours:** Stop/Esc, queued early questions and reading before indexing finishes, the "Finding the ideas it builds on" step, inline first-run depth question (worded around the answer) plus area toggles, the default nudge, "Ready for next", "Only you can see this", 44px coarse targets, list equivalents for every graphic, the empty-panel line, readiness shown inside the paper only (never as a deficit count in the Library).
