/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type Plugin, type ProxyOptions } from 'vite'

// The two Latin font files the first screen needs (DESIGN.md §2.1). Their
// built names are hashed, so the preload tags are written once the bundle
// exists. In dev the browser discovers them from the CSS, which is fine.
const PRELOAD_FONTS = ['sofia-sans-latin-wght-normal', 'literata-latin-opsz-normal']

function preloadFonts(): Plugin {
  return {
    name: 'papermind:preload-fonts',
    apply: 'build',
    transformIndexHtml: {
      order: 'post',
      handler(_html, ctx) {
        if (!ctx.bundle) return []
        const files = Object.keys(ctx.bundle)
        return PRELOAD_FONTS.flatMap((stem) => {
          const file = files.find((f) => f.includes(`${stem}-`) && f.endsWith('.woff2'))
          return file
            ? [
                {
                  tag: 'link',
                  attrs: { rel: 'preload', as: 'font', type: 'font/woff2', crossorigin: '', href: `/${file}` },
                  injectTo: 'head' as const,
                },
              ]
            : []
        })
      },
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.PAPERMIND_API_PROXY || 'http://localhost:8000'

  // /api → the FastAPI server, for `vite` and `vite preview` alike. Chat answers
  // stream as text/event-stream: the proxy passes chunks through as they
  // arrive, and these headers stop anything downstream (a browser extension,
  // a corporate proxy) from buffering them. A POST is never retried here.
  const proxy: Record<string, ProxyOptions> = {
    '/api': {
      target: apiTarget,
      changeOrigin: true,
      configure(p) {
        p.on('proxyRes', (res) => {
          if (String(res.headers['content-type'] ?? '').startsWith('text/event-stream')) {
            res.headers['cache-control'] = 'no-cache, no-transform'
            res.headers['x-accel-buffering'] = 'no'
          }
        })
      },
    },
  }

  return {
    plugins: [react(), preloadFonts()],
    server: { port: 5173, proxy },
    preview: { port: 4173, proxy },
    build: {
      target: 'es2023',
      sourcemap: true,
      rolldownOptions: {
        output: {
          // Long-lived vendor chunks: an app deploy doesn't invalidate React or
          // the router in the browser cache. pdf.js, KaTeX and the markdown
          // stack stay in the lazy Reader chunk.
          codeSplitting: {
            groups: [
              { name: 'react', test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
              { name: 'router', test: /node_modules[\\/]react-router[\\/]/ },
              { name: 'query', test: /node_modules[\\/]@tanstack[\\/]/ },
              { name: 'ui', test: /node_modules[\\/](@radix-ui|radix-ui|@floating-ui|@phosphor-icons|react-remove-scroll|aria-hidden|tslib|use-callback-ref|use-sidecar)[\\/]/ },
            ],
          },
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      css: false,
      include: ['src/**/*.test.{ts,tsx}'],
    },
  }
})
