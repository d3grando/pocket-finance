// Preload. Runs sandboxed (no Node APIs); exposes `window.pocket` via
// contextBridge — only the whitelisted calls from src/shared/ipc.ts, each wrapped so the page
// never gets ipcRenderer, the IPC event object or arbitrary channels.
// Web renderer usage: configureApi({ baseUrl: (await pocket.config.get()).apiBaseUrl, getToken: pocket.auth.getToken }).
import { contextBridge, ipcRenderer } from 'electron'
import { IPC, IPC_EVENTS, type PocketBridge, type WindowKind } from '../shared/ipc'

function windowKind(): WindowKind {
  const arg = process.argv.find((a) => a.startsWith('--pocket-window='))
  const v = arg?.slice('--pocket-window='.length)
  return v === 'full' || v === 'settings' ? v : 'popup'
}

function subscribe<T>(channel: string, cb: (payload: T) => void): () => void {
  if (typeof cb !== 'function') return () => {}
  const listener = (_e: Electron.IpcRendererEvent, payload: T) => cb(payload)
  ipcRenderer.on(channel, listener)
  return () => {
    ipcRenderer.removeListener(channel, listener)
  }
}

const invoke = <T>(channel: string, ...args: unknown[]): Promise<T> => ipcRenderer.invoke(channel, ...args) as Promise<T>

const bridge: PocketBridge = {
  isDesktop: true,
  platform: process.platform,
  windowKind: windowKind(),
  auth: {
    getToken: () => invoke(IPC.getToken),
    setToken: (token) => invoke(IPC.setToken, String(token)),
    clearToken: () => invoke(IPC.clearToken),
    login: (password) => invoke(IPC.login, String(password)),
    logout: () => invoke(IPC.logout),
  },
  config: {
    get: () => invoke(IPC.getConfig),
    setServerUrl: (url) => invoke(IPC.setServerUrl, String(url)),
    onChange: (cb) => subscribe(IPC_EVENTS.config, cb),
  },
  hotkey: {
    set: (acc) => invoke(IPC.setHotkey, String(acc)),
    suggest: () => invoke(IPC.suggestHotkeys),
    setRecording: (on) => invoke(IPC.setHotkeyRecording, !!on),
  },
  autostart: {
    set: (enabled) => invoke(IPC.setAutostart, !!enabled),
  },
  window: {
    hide: () => invoke(IPC.hide),
    openFull: (route) => invoke(IPC.openFull, route === undefined ? undefined : String(route)),
    openSettings: () => invoke(IPC.openSettings),
    resize: (height) => invoke(IPC.resize, Number(height)),
    setPinned: (pinned) => invoke(IPC.setPinned, !!pinned),
    onShown: (cb) => subscribe(IPC_EVENTS.shown, cb),
    onHidden: (cb) => subscribe(IPC_EVENTS.hidden, () => cb()),
  },
  connection: {
    get: () => invoke(IPC.getConnection),
    onChange: (cb) => subscribe(IPC_EVENTS.connection, cb),
  },
  openExternal: (url) => invoke(IPC.openExternal, String(url)),
  // legacy alias of window.onShown (older web builds)
  onShow: (cb) => subscribe(IPC_EVENTS.shown, cb),
}

contextBridge.exposeInMainWorld('pocket', bridge)

// Esc in the popup goes to the page first (DESIGN §11: close the top layer — menu, dialog,
// preview — or clear the field). Main does not swallow the key; once the page has dispatched it,
// tell main whether the page consumed it (preventDefault) — main hides the popup otherwise.
// Capture listener on window, registered before any page script: it runs first and is never cut
// off by stopPropagation(); the verdict is read in the next task, after the whole dispatch
// (MessageChannel rather than setTimeout: not throttled while the window is hidden).
if (bridge.windowKind === 'popup') {
  const pending: KeyboardEvent[] = []
  const channel = new MessageChannel()
  channel.port1.onmessage = () => {
    const e = pending.shift()
    if (e) ipcRenderer.invoke(IPC.escapeResult, e.defaultPrevented).catch(() => undefined)
  }
  window.addEventListener(
    'keydown',
    (e) => {
      if (e.key !== 'Escape' || !e.isTrusted || e.ctrlKey || e.altKey || e.shiftKey || e.metaKey) return
      pending.push(e)
      channel.port2.postMessage(null)
    },
    true,
  )
}
