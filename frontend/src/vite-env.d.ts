/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Origin of the PaperMind API, without a trailing slash or /api, e.g.
   * https://api.papermind.example. Empty = same origin (the dev server proxies /api).
   */
  readonly VITE_API_ORIGIN?: string
  /** Upload size limit shown in the hint and checked before uploading. Defaults to 50. */
  readonly VITE_MAX_UPLOAD_MB?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
