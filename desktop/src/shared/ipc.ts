// IPC contract between main and preload/renderer. Keep the surface minimal:
// every channel here is whitelisted in src/main/ipc-handlers.ts and exposed by src/preload/index.ts.
// The renderer only sees `window.pocket` (see PocketBridge below), never ipcRenderer itself.

export const IPC = {
  // auth
  getToken: 'pocket:auth:get-token', // → string | null (decrypted from safeStorage)
  setToken: 'pocket:auth:set-token', // (token: string) → { ok: boolean }
  clearToken: 'pocket:auth:clear-token', // token rejected (401) → forget it, popup shows the login screen
  login: 'pocket:auth:login', // (password: string) → LoginResult (main calls POST /auth/device-login)
  logout: 'pocket:auth:logout', // revoke this device on the server (best effort) + forget the token
  // config
  getConfig: 'pocket:config:get', // → DesktopConfig
  setServerUrl: 'pocket:config:set-server', // (url) → SetServerResult
  // hotkey / autostart
  setHotkey: 'pocket:hotkey:set', // (accelerator) → HotkeyResult
  suggestHotkeys: 'pocket:hotkey:suggest', // → string[] (currently free combinations)
  setHotkeyRecording: 'pocket:hotkey:recording', // (on: boolean) — release the global hotkey while recording a new one
  setAutostart: 'pocket:autostart:set', // (enabled: boolean) → boolean (actual state)
  // windows
  hide: 'pocket:window:hide', // popup: hide; other windows: close
  openFull: 'pocket:window:open-full', // (route?: string) open the full dashboard window
  openSettings: 'pocket:window:open-settings',
  resize: 'pocket:window:resize', // (height) — popup grows up from the tray, max 640
  setPinned: 'pocket:window:set-pinned', // (pinned) do not auto-hide on blur / Esc while a dialog is open
  // preload-internal (not in PocketBridge): the popup page finished dispatching an Esc keydown;
  // (handled: boolean = defaultPrevented) → main hides the popup only for an unhandled Esc
  escapeResult: 'pocket:window:escape-result',
  // misc
  openExternal: 'pocket:shell:open-external', // (url) https only → system browser
  getConnection: 'pocket:connection:get', // → ConnectionState
} as const

/** main → renderer events (ipcRenderer.on). */
export const IPC_EVENTS = {
  shown: 'pocket:event:shown', // popup became visible: { edge } — focus the input, animate from the tray
  hidden: 'pocket:event:hidden',
  config: 'pocket:event:config', // DesktopConfig changed
  connection: 'pocket:event:connection', // ConnectionState changed
} as const

export type TaskbarEdge = 'top' | 'bottom' | 'left' | 'right'
export type WindowKind = 'popup' | 'full' | 'settings'
export type ConnectionState = 'online' | 'offline' | 'unknown'
export type HotkeyStatus = 'ok' | 'taken' | 'invalid' | 'off'

export interface DesktopConfig {
  serverUrl: string // '' until the user enters it (or the build-time PF_DEFAULT_SERVER_URL)
  apiBaseUrl: string // serverUrl + /api/v1 ('' without a server)
  hotkey: string // desired accelerator, default Ctrl+Alt+Space
  hotkeyStatus: HotkeyStatus // 'taken' → registration failed at startup, ask for another one
  autostart: boolean
  version: string
  platform: string // process.platform ('win32' | 'darwin' | 'linux'); the web maps it to windows|macos|linux
  deviceName: string // host name — device_name for POST /auth/device-login (same as the main-process login)
  renderer: 'web' | 'lite' // web = packaged web build (/quick), lite = built-in fallback capture page
  signedIn: boolean
}

export type LoginResult =
  | { ok: true }
  | { ok: false; code: 'wrong_password' | 'rate_limited' | 'network' | 'server' | 'bad_request'; message: string }

export type HotkeyResult =
  | { ok: true; accelerator: string }
  | { ok: false; reason: 'taken' | 'invalid'; message: string; suggestions: string[] }

export type SetServerResult = { ok: true; config: DesktopConfig } | { ok: false; message: string }

export interface ShownEvent {
  edge: TaskbarEdge
}

/** Shape of `window.pocket` (exposed by the preload via contextBridge). */
export interface PocketBridge {
  isDesktop: true
  platform: string
  windowKind: WindowKind
  auth: {
    getToken(): Promise<string | null>
    setToken(token: string): Promise<{ ok: boolean }>
    clearToken(): Promise<void>
    login(password: string): Promise<LoginResult>
    logout(): Promise<void>
  }
  config: {
    get(): Promise<DesktopConfig>
    setServerUrl(url: string): Promise<SetServerResult>
    onChange(cb: (c: DesktopConfig) => void): () => void
  }
  hotkey: {
    set(accelerator: string): Promise<HotkeyResult>
    suggest(): Promise<string[]>
    setRecording(on: boolean): Promise<void>
  }
  autostart: { set(enabled: boolean): Promise<boolean> }
  window: {
    hide(): Promise<void>
    openFull(route?: string): Promise<void>
    openSettings(): Promise<void>
    resize(height: number): Promise<void>
    setPinned(pinned: boolean): Promise<void>
    onShown(cb: (e: ShownEvent) => void): () => void
    onHidden(cb: () => void): () => void
  }
  connection: {
    get(): Promise<ConnectionState>
    onChange(cb: (s: ConnectionState) => void): () => void
  }
  openExternal(url: string): Promise<boolean>
  /** @deprecated alias of window.onShown */
  onShow(cb: (e: ShownEvent) => void): () => void
}

export const POPUP_WIDTH = 420
export const POPUP_HEIGHT = 450
export const POPUP_MAX_HEIGHT = 640
export const POPUP_MIN_HEIGHT = 300
/**
 * Server address of a fresh install: baked in at build time from the env PF_DEFAULT_SERVER_URL
 * (electron.vite.config.ts → `define`), empty by default. Empty = the login screen first asks for
 * the server address. A self-hoster builds their .exe with PF_DEFAULT_SERVER_URL=https://pocket.example.com.
 */
export const DEFAULT_SERVER_URL: string = typeof __PF_DEFAULT_SERVER_URL__ === 'string' ? __PF_DEFAULT_SERVER_URL__ : ''
export const DEFAULT_HOTKEY = 'Ctrl+Alt+Space'
/** macOS: ⌘⇧Space (Ctrl+Option+Space feels foreign there; ⌘Space is Spotlight). */
export const DEFAULT_HOTKEY_MAC = 'Shift+Super+Space' // canonical modifier order (accelerator.ts)
export function defaultHotkey(platform: string): string {
  return platform === 'darwin' ? DEFAULT_HOTKEY_MAC : DEFAULT_HOTKEY
}
/** Web route of the Quick Capture page inside the packaged web build (ARCH §13). */
export const QUICK_ROUTE = '/quick'
export const APP_SCHEME = 'app'
export const APP_HOST = 'pocket'
export const APP_ORIGIN = `${APP_SCHEME}://${APP_HOST}`
/** Desktop-native pages (login, settings, lite capture) live under this prefix of app://pocket. */
export const UI_PREFIX = '/__pocket/'
