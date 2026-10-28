// Builds main + preload (Node/Electron side) and the desktop-native pages (src/ui → out/ui:
// login, settings, lite capture), served at app://pocket/__pocket/*.
// The web app itself is built by `pnpm --dir ../web build:desktop` into ./renderer and served at app://pocket/*.
// PF_DEFAULT_SERVER_URL (optional, build time): the server address of a fresh install, e.g.
// `PF_DEFAULT_SERVER_URL=https://pocket.example.com pnpm dist:win`. Empty → the login screen asks for it.
import { resolve } from 'node:path'
import { defineConfig } from 'electron-vite'
import { normalizeServerUrl } from './src/main/urls'

function defaultServerUrl(): string {
  const raw = (process.env.PF_DEFAULT_SERVER_URL ?? '').trim()
  if (!raw) return ''
  const s = normalizeServerUrl(raw)
  if (!s.ok) throw new Error(`PF_DEFAULT_SERVER_URL: ${s.message} (${raw})`)
  return s.serverUrl
}

const define = { __PF_DEFAULT_SERVER_URL__: JSON.stringify(defaultServerUrl()) }

export default defineConfig({
  main: {
    define,
    build: { outDir: 'out/main', rolldownOptions: { input: resolve(__dirname, 'src/main/index.ts') } },
  },
  preload: {
    define,
    build: { outDir: 'out/preload', rolldownOptions: { input: resolve(__dirname, 'src/preload/index.ts') } },
  },
  renderer: {
    define,
    root: resolve(__dirname, 'src/ui'),
    base: './',
    build: {
      outDir: resolve(__dirname, 'out/ui'),
      emptyOutDir: true,
      rolldownOptions: {
        input: {
          login: resolve(__dirname, 'src/ui/login.html'),
          settings: resolve(__dirname, 'src/ui/settings.html'),
          capture: resolve(__dirname, 'src/ui/capture.html'),
        },
      },
    },
  },
})
