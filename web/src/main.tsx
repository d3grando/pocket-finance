import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from '@/app/App'
import { installSignOutCleanup } from '@/features/auth/session'
import { startConnectionMonitor } from '@/lib/connection'
import { initDesktopBridge, isDesktopBuild } from '@/lib/desktop'
import { applyTheme, watchSystemTheme } from '@/lib/theme'
import '@/styles/globals.css'

async function boot() {
  applyTheme()
  watchSystemTheme()
  startConnectionMonitor()
  installSignOutCleanup() // any 401 → forget the user's data kept in localStorage
  await initDesktopBridge() // Electron: Bearer device token + API base from the preload bridge
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
  // PWA service worker (web only): precaches the app shell, never touches /api (no cached writes).
  // A new version waits: «Обновить» toast, or it applies itself while the app is in the background.
  if (!isDesktopBuild && import.meta.env.PROD && 'serviceWorker' in navigator) {
    const { registerSW } = await import('virtual:pwa-register')
    const { offerUpdate } = await import('@/lib/pwa')
    const updateSW = registerSW({ immediate: true, onNeedRefresh: () => offerUpdate(() => updateSW(true)) })
  }
}

void boot()
