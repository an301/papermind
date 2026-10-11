// Copies the files pdf.js fetches at runtime (character maps, standard fonts,
// wasm decoders, colour profiles) into public/pdfjs, which git ignores.
// They can't be bundled because pdf.js requests them by URL. Runs before dev and build.
import { cpSync, rmSync } from 'node:fs'

const from = new URL('../node_modules/pdfjs-dist/', import.meta.url)
const to = new URL('../public/pdfjs/', import.meta.url)
rmSync(to, { recursive: true, force: true })
for (const dir of ['cmaps', 'standard_fonts', 'wasm', 'iccs']) {
  cpSync(new URL(dir, from), new URL(dir, to), { recursive: true })
}
