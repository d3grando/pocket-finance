/// <reference types="vite/client" />
interface ImportMetaEnv {
  /** API base. Web: '/api/v1' (same origin). Desktop: 'https://pocket.example.com/api/v1'. */
  readonly VITE_API_BASE_URL?: string
}
interface ImportMeta {
  readonly env: ImportMetaEnv
}
