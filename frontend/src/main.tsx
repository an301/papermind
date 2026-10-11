// Fonts first, before any CSS that names the families (DESIGN.md §2.1).
import '@fontsource-variable/literata/opsz.css' // roman, wght 200–900 + opsz 7–72 → 'Literata Variable'
import '@fontsource-variable/literata/opsz-italic.css' // italic, fetched only when an <em> renders
import '@fontsource-variable/sofia-sans/wght.css' // wght 1–1000 → 'Sofia Sans Variable'
import 'katex/dist/katex.min.css'
import './styles/tokens.css'
import './styles/base.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './app/App'
import { initTheme } from './app/theme'

initTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
