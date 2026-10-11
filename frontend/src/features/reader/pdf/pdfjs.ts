/**
 * The one place that configures pdf.js. Import pdf.js only through here.
 *
 * The worker is emitted verbatim by Vite (`?url`) and pdf.js starts it as a
 * module worker itself. Character maps, standard fonts, wasm decoders and
 * colour profiles are fetched by URL at runtime, so they are copied to
 * public/pdfjs by scripts/copy-pdfjs-assets.mjs before dev and build.
 */
import * as pdfjs from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { accessHeaders } from '../../../api/access'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

const base = `${import.meta.env.BASE_URL}pdfjs/`

/** Open a PDF by URL (pdf.js uses Range requests when the server allows them). */
export function openPdf(url: string) {
  return pdfjs.getDocument({
    url,
    httpHeaders: accessHeaders(),
    cMapUrl: `${base}cmaps/`,
    cMapPacked: true,
    standardFontDataUrl: `${base}standard_fonts/`,
    wasmUrl: `${base}wasm/`,
    iccUrl: `${base}iccs/`,
  })
}

/** CSS px per PDF point at 100% (96 / 72): what pdf.js and every PDF reader call "Actual size". */
export const PDF_TO_CSS = pdfjs.PixelsPerInch.PDF_TO_CSS_UNITS

/**
 * pdf.js `pageColors` for the opt-in "Night page" mode (DESIGN.md §2.2, the
 * `.pdf-viewer[data-page-mode="night"]` comment). pdf.js needs literal colours
 * for its canvas, so these two can't be CSS tokens; they match the night
 * page's `--page-paper` and the text it was measured with in §2.7.
 */
export const NIGHT_PAGE_COLORS = { background: '#1F1F21', foreground: '#D9D9DB' } as const

export { pdfjs }
export type { PDFDocumentProxy, PDFPageProxy, PageViewport, RenderTask } from 'pdfjs-dist'
