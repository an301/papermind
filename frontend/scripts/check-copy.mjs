#!/usr/bin/env node
// The automated half of the anti-slop checklist (DESIGN.md §9), over src/.
// Errors fail `npm run lint`; "review" hits are printed for a human to judge.
// Comment lines are skipped, so a comment may name what it forbids.
import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const src = join(root, 'src')

/**
 * `re` tests the code on a line; `strings` tests each string literal on it.
 * @type {{name: string, re?: RegExp, strings?: (s: string) => boolean, level: 'error' | 'review', files?: RegExp, skip?: RegExp}[]}
 */
const RULES = [
  { name: 'purple/indigo/gradient', re: /indigo|violet|purple|from-.*to-|bg-gradient|linear-gradient|radial-gradient|bg-clip-text|text-transparent/, level: 'error' },
  { name: 'transition: all, ease-in or overshoot', re: /transition:\s*all|transition-property:\s*all|[^-]ease-in[^-]|cubic-bezier\([^)]*\b1\.[0-9]/, level: 'error' },
  { name: 'raw hex colour outside tokens.css', re: /#[0-9A-Fa-f]{3,8}\b(?![-\w])/, level: 'error', skip: /tokens\.css$/, files: /\.(css|tsx)$/ },
  { name: 'off-scale px (check by hand)', re: /\b(1[13]|2[0237]|1[579])px\b/, level: 'review', skip: /tokens\.css$/ },
  { name: 'banned font', re: /'Inter|"Inter|Geist|Space Grotesk|Instrument (Serif|Sans)|Fraunces|\bLora\b/, level: 'error' },
  { name: 'second icon library or sparkles', re: /lucide|heroicons|tabler|react-icons|Sparkle/, level: 'error' },
  {
    name: 'banned word in copy',
    re: /ai-powered|\bmagic\b|supercharge|\bunlock|\belevate|seamless|empower|\bharness|\bdelve|world-class|not just|how can i help|\boops\b|something went wrong/i,
    level: 'error',
    skip: /\.test\.tsx?$/,
  },
  { name: '"..." in a string (use …)', strings: (s) => /(^|[^.])\.\.\.($|[^.])/.test(s), level: 'error', skip: /\.test\.tsx?$/ },
  { name: 'exclamation mark ending a UI string', strings: (s) => /[A-Za-z.)]!\s*$/.test(s), level: 'error', files: /\.tsx$/, skip: /\.test\.tsx$/ },
  { name: 'exclamation mark ending JSX text', re: />[^<>{}]*[A-Za-z.)]!\s*<\//, level: 'error', files: /\.tsx$/, skip: /\.test\.tsx$/ },
  { name: 'emoji', re: /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u, level: 'error' },
  { name: 'backdrop blur', re: /backdrop-filter|backdrop-blur/, level: 'error' },
  { name: 'stripe on an edge', re: /border-(left|top):\s*[2-9]px/, level: 'error' },
]

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name)
    if (entry.isDirectory()) yield* walk(p)
    else if (/\.(tsx?|css|md)$/.test(entry.name) && !entry.name.endsWith('.md')) yield p
  }
}

const STRING_RE = /(["'`])((?:\\.|(?!\1)[^\\])*)\1/g
const literals = (code) => [...code.matchAll(STRING_RE)].map((m) => m[2])
const isComment = (line) => /^\s*(\/\/|\/?\*|\{\s*\/\*)/.test(line)
let errors = 0
let reviews = 0
for (const file of walk(src)) {
  const rel = relative(root, file)
  const lines = readFileSync(file, 'utf8').split('\n')
  lines.forEach((line, i) => {
    if (isComment(line)) return
    const code = line.replace(/\s\/\/\s.*$/, '').replace(/\/\*.*?\*\//g, '')
    for (const rule of RULES) {
      if (rule.files && !rule.files.test(file)) continue
      if (rule.skip?.test(file)) continue
      const hit = rule.re ? rule.re.test(code) : literals(code).some((lit) => rule.strings?.(lit))
      if (!hit) continue
      const tag = rule.level === 'error' ? 'error ' : 'review'
      console.log(`${tag}  ${rel}:${i + 1}  ${rule.name}\n        ${line.trim().slice(0, 140)}`)
      if (rule.level === 'error') errors++
      else reviews++
    }
  })
}
console.log(`\ncheck-copy: ${errors} error(s), ${reviews} to review by hand (DESIGN.md §9).`)
process.exit(errors ? 1 : 0)
