// Hardening for every webContents the app creates: no navigation away from app://pocket, no new
// windows, no <webview>, no permissions. A link may go to the system browser only when it points
// at the configured Pocket server or t.me/<bot> (allowedExternalUrl); everything else is dropped.
import { app, session, shell, type WebPreferences } from 'electron'
import { allowedExternalUrl, isInternalUrl } from './urls'

export function secureWebPreferences(preload: string, extraArgs: string[], devTools: boolean): WebPreferences {
  return {
    preload,
    contextIsolation: true,
    sandbox: true,
    nodeIntegration: false,
    nodeIntegrationInWorker: false,
    nodeIntegrationInSubFrames: false,
    webSecurity: true,
    allowRunningInsecureContent: false,
    webviewTag: false,
    navigateOnDragDrop: false,
    spellcheck: false,
    safeDialogs: true,
    devTools,
    additionalArguments: extraArgs,
  }
}

/** Hand an allowed link to the system browser (the normalized href, not the raw string). */
export function openExternalSafe(url: string, serverUrl: string): boolean {
  const href = allowedExternalUrl(url, serverUrl)
  if (!href) return false
  void shell.openExternal(href)
  return true
}

export interface HardenOptions {
  /** Allow Chromium's local-network permission when the API itself is on loopback (dev/tests). */
  allowLoopbackApi: () => boolean
  apiOrigin: () => string
  /** configured server (origin + base path) — the only host besides t.me links may open */
  serverUrl: () => string
  log?: (msg: string) => void
}

export function hardenApp(opts: HardenOptions): void {
  app.on('web-contents-created', (_e, contents) => {
    // in-app windows never navigate away or open new windows; an allowed link goes to the browser
    const guard = (event: { preventDefault(): void }, url: string) => {
      if (isInternalUrl(url)) return
      event.preventDefault()
      if (!openExternalSafe(url, opts.serverUrl())) opts.log?.('blocked navigation to an external URL')
    }
    contents.on('will-navigate', (event) => guard(event, event.url))
    contents.on('will-redirect', (event) => guard(event, event.url))
    contents.on('will-frame-navigate', (event) => {
      if (!event.isMainFrame) {
        if (!isInternalUrl(event.url)) event.preventDefault()
      }
    })
    contents.setWindowOpenHandler(({ url }) => {
      if (!openExternalSafe(url, opts.serverUrl())) opts.log?.('blocked window.open to an external URL')
      return { action: 'deny' }
    })
    contents.on('will-attach-webview', (event) => event.preventDefault())
  })

  const ses = session.defaultSession
  const loopbackPermission = (permission: string) =>
    opts.allowLoopbackApi() && /local-network|loopback/i.test(permission)
  ses.setPermissionRequestHandler((_wc, permission, callback) => {
    const ok = loopbackPermission(permission)
    if (!ok) opts.log?.(`permission denied: ${permission}`)
    callback(ok)
  })
  ses.setPermissionCheckHandler((_wc, permission) => loopbackPermission(permission))
  ses.setDevicePermissionHandler(() => false)
  // Exports (CSV/XLSX) from the full panel are blob: downloads → normal «Save as» dialog.
  // Anything else (a page trying to pull a file from elsewhere) is dropped.
  ses.on('will-download', (event, item) => {
    const url = item.getURL()
    if (url.startsWith('blob:app://') || isInternalUrl(url) || url.startsWith(`${opts.apiOrigin()}/`)) return
    event.preventDefault()
  })
}
