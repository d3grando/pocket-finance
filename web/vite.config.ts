/// <reference types="vitest/config" />
import path from 'node:path'
import type { Plugin } from 'vite'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// Modes: default (web/PWA, served at /, API same-origin /api/v1)
//        desktop (Electron renderer: base './', hash router, no PWA, API from VITE_API_BASE_URL or the
//        preload bridge, out → ../desktop/renderer)
// Desktop build has no service worker: stub the PWA virtual module so main.tsx compiles in both modes.
const pwaStub: Plugin = {
  name: 'pocket-pwa-stub',
  resolveId: (id) => (id === 'virtual:pwa-register' ? '\0pwa-stub' : null),
  load: (id) => (id === '\0pwa-stub' ? 'export function registerSW() { return () => Promise.resolve() }' : null),
}

export default defineConfig(({ mode }) => {
  const desktop = mode === 'desktop'
  return {
    base: desktop ? './' : '/',
    plugins: [
      react(),
      tailwindcss(),
      desktop && pwaStub,
      !desktop &&
        VitePWA({
          // 'prompt': a new build never reloads an open app by itself (it would wipe the AI line and cut
          // a request in flight) — lib/pwa.ts offers «Обновить» and updates when the app is hidden.
          registerType: 'prompt',
          injectRegister: false,
          includeAssets: ['favicon.svg', 'favicon.ico', 'apple-touch-icon.png', 'theme-init.js', 'robots.txt'],
          // the 512 px install icons are fetched by the browser when installing — no need to precache
          includeManifestIcons: false,
          manifest: {
            id: '/',
            name: 'Pocket Finance',
            short_name: 'Pocket',
            description: 'Личный учёт финансов словами',
            lang: 'ru',
            dir: 'ltr',
            start_url: '/',
            scope: '/',
            display: 'standalone',
            orientation: 'portrait',
            background_color: '#f9fafc',
            theme_color: '#5d4ed6',
            categories: ['finance', 'productivity'],
            icons: [
              { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
              { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
              { src: '/icons/maskable-192.png', sizes: '192x192', type: 'image/png', purpose: 'maskable' },
              { src: '/icons/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
            ],
            shortcuts: [
              { name: 'Добавить', short_name: 'Добавить', url: '/add', icons: [{ src: '/icons/icon-192.png', sizes: '192x192' }] },
              { name: 'Разобрать', url: '/operations?status=needs_account', icons: [{ src: '/icons/icon-192.png', sizes: '192x192' }] },
            ],
          },
          workbox: {
            // App shell only. /api/** is NOT routed through the SW at all: no caching of reads or
            // writes, no background sync — offline the app honestly says «нет связи» (DESIGN §8.4).
            globPatterns: ['**/*.{js,css,html,svg,png,ico,woff2}'],
            // first visit downloads only what the UI needs: Inter latin/latin-ext (₫)/cyrillic/vietnamese,
            // JetBrains Mono latin; other subsets and the 512 px install icons load on demand
            globIgnores: [
              '**/*-greek-*.woff2', '**/inter-cyrillic-ext-*.woff2',
              '**/jetbrains-mono-cyrillic-*.woff2', '**/jetbrains-mono-latin-ext-*.woff2', '**/jetbrains-mono-vietnamese-*.woff2',
              'icons/*-512.png',
            ],
            runtimeCaching: [
              { urlPattern: /\/assets\/[^/]+\.woff2$/, handler: 'CacheFirst', options: { cacheName: 'pf-fonts', expiration: { maxEntries: 30 } } },
            ],
            navigateFallback: '/index.html',
            navigateFallbackDenylist: [/^\/api\//, /^\/robots\.txt$/],
            cleanupOutdatedCaches: true,
          },
        }),
    ].filter(Boolean),
    resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
    build: {
      outDir: desktop ? '../desktop/renderer' : 'dist',
      emptyOutDir: true,
      chunkSizeWarningLimit: 900,
    },
    server: {
      port: 5173,
      proxy: { '/api': { target: process.env.VITE_PROXY_API ?? 'http://127.0.0.1:8000', changeOrigin: false, xfwd: true } },
    },
    preview: {
      proxy: { '/api': { target: process.env.VITE_PROXY_API ?? 'http://127.0.0.1:8000', changeOrigin: false, xfwd: true } },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
    },
  }
})
